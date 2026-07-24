"""FlexCosting: the costing block that wraps EECO (architecture §3.6, decision R4).

``FlexCosting`` subclasses IDAES :class:`FlowsheetCostingBlockData` for its
registration/CapEx machinery and organizes every cost into two sub-blocks it
owns:

* **``opex``** — all operating cost: **electricity** and **fuel (gas)** cost
  (both delegated to the external EECO package via the M06 :mod:`flexops.costing.opex`
  bridge) plus a user-defined **fixed operating cost** (maintenance/labor/
  chemicals — *not* from EECO). ``opex.total_operating_cost`` is their sum and is
  re-exposed as :attr:`aggregate_operating_cost`.
* **``capex``** — capital cost. In v0 an **empty placeholder**
  (``total_capital_cost == 0``, re-exposed as :attr:`aggregate_capital_cost`);
  later milestones aggregate per-unit capital costs into it. Capital cost enters
  the objective **only in design mode** (:meth:`set_design_mode`); the
  operations-mode objective is :attr:`aggregate_operating_cost` alone.

FlexCosting writes **no** tariff cost math of its own (that is EECO's, decision
R4): it aggregates registered units' ``power_electrical`` into a kW series, hands
that plus the tariff to EECO in-objective (via
:func:`~flexops.costing.opex.add_electricity_cost`), and — post-solve — calls
EECO's post-hoc evaluator via :meth:`report_cost` to
produce the **reported** operating cost, never the raw solver objective (§6
reporting rule, R9). Demand response is containers-only in v0 (a :attr:`dr`
placeholder + the no-op :meth:`_build_dr` hook, architecture §2.4).

Construction-order invariant: FlexCosting may be constructed before any units
exist, because all aggregation and the EECO call are deferred to
:meth:`cost_process`. :meth:`cost_process` aggregates power by **pulling** every
unit's registered power from the model (via
:func:`~flexops.core.registration.iter_io_registry`), so the result is
independent of whether the costing block was created before or after the units.
"""

import dataclasses
import logging
from typing import Any

import numpy as np
import pyomo.environ as pyo
from idaes.core import FlowsheetCostingBlockData, declare_process_block_class
from pyomo.common.config import ConfigValue
from pyomo.environ import units as pyunits

from flexcore import nomenclature as nm
from flexcore.exceptions import FlexConfigError
from flexops.core.registration import iter_io_registry
from flexops.costing.opex import (
    DRConfig,
    add_electricity_cost,
    evaluate_cost,
    load_dr_program,
    load_tariff,
    tariff_currency_units,
)

_log = logging.getLogger(__name__)


@dataclasses.dataclass
class OperatingCostBreakdown:
    """Categorized operating cost from :meth:`FlexCostingData.report_cost`.

    Attributes:
        electricity: EECO post-hoc electricity bill on the realized aggregate
            power ($).
        fuel: EECO post-hoc gas bill ($); ``0`` in v0 (no gas-consuming unit).
        fixed: The configured fixed operating cost ($, a constant).
        dr_revenue: Demand-response incentive credit ($, subtracted); ``0`` in v0
            (DR is containers-only).
        total: ``electricity + fuel + fixed - dr_revenue`` ($).
    """

    electricity: float
    fuel: float
    fixed: float
    dr_revenue: float
    total: float


@dataclasses.dataclass
class CapitalCostBreakdown:
    """Categorized capital cost from :meth:`FlexCostingData.report_cost`.

    Attributes:
        by_component: Per-unit capital cost keyed by unit block name; ``{}`` in
            v0 (the capex block is an empty placeholder).
        total: Sum over ``by_component`` ($); ``0`` in v0.
    """

    by_component: dict[str, float]
    total: float


@dataclasses.dataclass
class CostReport:
    """The reported, categorized cost from :meth:`FlexCostingData.report_cost`.

    Attributes:
        operating: The :class:`OperatingCostBreakdown`.
        capital: The :class:`CapitalCostBreakdown`.
        total: ``operating.total + capital.total`` ($).
    """

    operating: OperatingCostBreakdown
    capital: CapitalCostBreakdown
    total: float


@dataclasses.dataclass
class _SizingEntry:
    """A registered sizing Var and its (optional) capex-defining constraint.

    Attributes:
        var: The sizing ``Var`` (e.g. a battery/tank capacity) modes fix/unfix.
        capex_constraint: The constraint modes (de)activate, or ``None``.
    """

    var: Any
    capex_constraint: Any | None


@declare_process_block_class("FlexCosting")
class FlexCostingData(FlowsheetCostingBlockData):
    """EECO-backed costing block with ``opex``/``capex`` sub-blocks (module docstring).

    Example:
        >>> import pyomo.environ as pyo
        >>> from pyomo.environ import units as pyunits
        >>> import flexops as fo
        >>> m = pyo.ConcreteModel()
        >>> m.time_block = fo.TimeBlock(
        ...     start_date="2025-07-08", end_date="2025-07-09",
        ...     time_step=1 * pyunits.hr,
        ... )
        >>> m.costing = fo.FlexCosting(  # doctest: +SKIP
        ...     time_block=m.time_block, tariff_file="tariff.json",
        ... )
    """

    CONFIG = FlowsheetCostingBlockData.CONFIG()
    CONFIG.declare(
        "time_block",
        ConfigValue(
            default=None,
            description="The fo.TimeBlock instance whose time_index/dt/"
            "datetime_index this costing aggregates and bills against. Required.",
        ),
    )
    CONFIG.declare(
        "tariff_file",
        ConfigValue(
            default=None,
            description="Path to an EECO tariff file. Exactly one of tariff_file "
            "or tariff must be given.",
        ),
    )
    CONFIG.declare(
        "tariff",
        ConfigValue(
            default=None,
            description="An already-loaded EECO tariff object. Exactly one of "
            "tariff or tariff_file must be given.",
        ),
    )
    CONFIG.declare(
        "dr_event_file",
        ConfigValue(
            default=None,
            description="Optional path to an EECO demand-response program file. "
            "v0 loads it into a container only (no DR constraints built).",
        ),
    )
    CONFIG.declare(
        "fixed_operating_cost",
        ConfigValue(
            default=0.0,
            domain=float,
            description="Fixed operating cost in dollars over the horizon "
            "(non-tariff: maintenance, labor, chemicals). Distinct from the "
            "tariff's own fixed charge, which EECO folds into electricity cost.",
        ),
    )

    # -- required FlowsheetCostingBlockData overloads ---------------------

    def build_global_params(self) -> None:
        """Resolve the tariff and set the base currency from its currency basis.

        Called during :meth:`build`. The base currency is the tariff sheet's
        currency basis (EECO tariffs are dollar-based → ``USD``, from the charge
        ``units`` column); every operating-cost expression FlexCosting builds is
        labeled with it. EECO's own cost expressions are dimensionless dollars,
        so FlexCosting casts them to this currency (:meth:`cost_process`).
        """
        self._tariff = self._resolve_tariff()
        self._currency = tariff_currency_units(self._tariff)
        self.base_currency = self._currency
        self.base_period = pyunits.year

    def _resolve_tariff(self):
        """Load the tariff from config, requiring exactly one source.

        Returns:
            The loaded EECO tariff object.

        Raises:
            FlexConfigError: If not exactly one of ``tariff_file``/``tariff`` is
                given.
        """
        tariff_file = self.config.tariff_file
        tariff = self.config.tariff
        if (tariff_file is None) == (tariff is None):
            raise FlexConfigError(
                "Provide exactly one of tariff_file (a path) or tariff (a loaded "
                f"EECO tariff object); got tariff_file={tariff_file!r}, "
                f"tariff={'set' if tariff is not None else None}.",
                field="tariff_file",
                value=tariff_file,
            )
        return load_tariff(tariff_file if tariff_file is not None else tariff)

    def build_process_costs(self) -> None:
        """No-op: flex-native process costs are built in :meth:`cost_process`."""

    def initialize_build(self) -> None:
        """No-op: FlexCosting builds only Expressions/Params, nothing to initialize."""

    # -- build (construction time; no aggregation, no EECO call) ----------

    def build(self) -> None:
        """Validate config, load the tariff + DR container, init empty registries.

        Loads the tariff and the DR program (into the ``dr`` container) but
        builds **no** aggregation, **no** ``opex``/``capex`` sub-blocks, and
        **no** DR constraints here — everything is deferred to
        :meth:`cost_process` so the block may be constructed before any units
        exist (the construction-order invariant).

        Raises:
            FlexConfigError: If not exactly one of ``tariff_file``/``tariff`` is
                given, or ``time_block`` is missing.
        """
        # super().build() runs build_global_params, which resolves the tariff
        # (exclusivity check) and sets self._tariff, self._currency, base_currency.
        super().build()

        if self.config.time_block is None:
            raise FlexConfigError(
                "FlexCosting requires a time_block=fo.TimeBlock instance.",
                field="time_block",
                value=None,
            )

        self.dr = DRConfig(program=load_dr_program(self.config.dr_event_file))

        # _registered_power: units that opted into this costing package (the
        # push association, consumed for capex attribution from M08). Power
        # AGGREGATION does not read this -- it pulls from the model in
        # cost_process so it is construction-order independent.
        self._registered_power: list[tuple[Any, Any, nm.PowerKind]] = []
        # _registered_sizing: sizing Vars + capex constraints the modes toggle.
        # Empty in M07 (no unit registers capex); wiring for M08/M16.
        self._registered_sizing: list[_SizingEntry] = []

    # -- registration (push association; consumed from M08) ---------------

    def register_unit_power(self, unit, var, kind: nm.PowerKind) -> None:
        """Record that ``unit`` associated a power draw with this costing package.

        Called by :meth:`~flexops.core.ops_block.OpsBlockData.register_power` when
        a unit is built with ``costing_package=`` set. The record is the explicit
        unit↔costing association used for capex attribution from M08; power
        aggregation in :meth:`cost_process` pulls from the model instead, so it
        does not depend on this being called.

        Args:
            unit: The unit block registering the draw.
            var: The unit's power-draw ``Var`` (kW).
            kind: The :class:`~flexcore.nomenclature.PowerKind` of the draw.
        """
        self._registered_power.append((unit, var, kind))

    def register_sizing_variable(self, var, capex_constraint=None) -> None:
        """Register a sizing Var (and its capex constraint) for the mode toggles.

        Args:
            var: A sizing ``Var`` (e.g. battery/tank capacity) that
                :meth:`set_operations_mode`/:meth:`set_design_mode` fix/unfix.
            capex_constraint: The capex-defining constraint the modes
                (de)activate, or ``None``.
        """
        self._registered_sizing.append(_SizingEntry(var, capex_constraint))

    # -- DR hook (no-op in v0; containers-only) ---------------------------

    def _build_dr(self) -> None:
        """No-op demand-response hook (v0 is containers-only, architecture §2.4).

        Exists so later DR work is additive; it builds no DR event, curtailment,
        incentive, or capacity constraints.
        """
        if self.dr is not None and self.dr.program is not None:
            _log.debug(
                "DR program present but v0 is containers-only; building no DR "
                "constraints on %s.",
                self.name,
            )

    # -- cost_process (all aggregation + the EECO call, deferred here) ----

    def cost_process(self) -> None:
        """Aggregate power, build the ``opex``/``capex`` blocks, enter operations mode.

        Overrides the parent ``FlowsheetCostingBlockData.cost_process`` (whose
        ``aggregate_capital_cost`` Var would collide with the flex-native
        Expression names): FlexCosting builds its own flex-native components and
        does not invoke the parent aggregation machinery.
        """
        tb = self.config.time_block

        # 1. Physical power aggregation (kW), pulled from every unit on the
        #    model so it is construction-order independent. The explicit
        #    0*kW term keeps the Expression well-defined with an empty registry.
        elec_vars, therm_vars = [], []
        for _block, registry in iter_io_registry(self.model()):
            for rec in registry.power:
                if rec.kind is nm.PowerKind.ELECTRICAL:
                    elec_vars.append(rec.var)
                elif rec.kind is nm.PowerKind.THERMAL:
                    therm_vars.append(rec.var)

        @self.Expression(tb.time_index, doc="Aggregate electrical draw (kW).")
        def aggregate_electrical_power(_b, t):
            return sum(v[t] for v in elec_vars) + 0 * pyunits.kW

        @self.Expression(tb.time_index, doc="Aggregate thermal duty (kW).")
        def aggregate_thermal_power(_b, t):
            return sum(v[t] for v in therm_vars) + 0 * pyunits.kW

        # 2. opex block: electricity + fuel + fixed, built via the M06 opex.py
        #    bridge (EECO owns the cost math; no tariff math is written here).
        #    EECO's cost expressions are dimensionless dollars; every operating
        #    cost is cast to the tariff's currency basis (self._currency, e.g. USD).
        cur = self._currency
        self.opex = pyo.Block(doc="All operating cost: electricity + fuel + fixed.")
        dt_hours = pyo.value(pyunits.convert(tb.dt, pyunits.hr))
        elec = add_electricity_cost(
            block=self.opex,
            electrical_power=self.aggregate_electrical_power,
            time_index=tb.datetime_index,
            dt_hours=dt_hours,
            tariff=self._tariff,
            dr_config=self.dr,
        )
        self.opex.electricity_cost = pyo.Expression(
            expr=elec.total_operating_cost * cur, doc="EECO electricity cost."
        )
        # No gas-consuming unit in v0, so no gas leg is built and fuel is 0.
        # (When a gas-usage series is registered, add_gas_cost fills this in.)
        self.opex.fuel_cost = pyo.Expression(expr=0.0 * cur, doc="EECO fuel/gas cost.")
        self.opex.fixed_operating_cost = pyo.Param(
            initialize=self.config.fixed_operating_cost,
            mutable=True,
            units=cur,
            doc="Non-tariff fixed operating cost (over the horizon).",
        )
        self.opex.total_operating_cost = pyo.Expression(
            expr=self.opex.electricity_cost
            + self.opex.fuel_cost
            + self.opex.fixed_operating_cost,
            doc="electricity + fuel + fixed operating cost.",
        )
        self._build_dr()  # no-op in v0

        self.aggregate_operating_cost = pyo.Expression(
            expr=self.opex.total_operating_cost,
            doc="IDAES-aggregate name for the opex total (the operations objective).",
        )

        # 3. capex block: empty placeholder in v0 (total_capital_cost == 0).
        self.capex = pyo.Block(doc="Capital cost (empty placeholder in v0).")
        self.capex.total_capital_cost = pyo.Expression(
            expr=0.0 * cur,
            doc="Sum of registered units' capital cost; 0 in v0 (empty capex).",
        )
        self.aggregate_capital_cost = pyo.Expression(
            expr=self.capex.total_capital_cost,
            doc="IDAES-aggregate name for the capex total (0 in v0).",
        )

        # 4. Objective composition. Operations objective = aggregate_operating_cost
        #    (API-freeze); design objective = total_cost (opex + capex). Capital
        #    cost thus reaches the objective only via total_cost in design mode.
        self.total_cost = pyo.Expression(
            expr=self.aggregate_operating_cost + self.aggregate_capital_cost,
            doc="Design-mode objective: operating + capital cost ($).",
        )

        self.set_operations_mode()  # default final state (scheduling first)

    # -- design / operations modes (single-model; empty registries in M07) --

    def set_operations_mode(self) -> None:
        """Fix every registered sizing Var and deactivate its capex constraint.

        The operations objective is ``aggregate_operating_cost`` alone. In
        M07 the sizing registry is empty, so this is a no-op; it is the documented
        single-model toggle later milestones populate. Idempotent.
        """
        for entry in self._registered_sizing:
            entry.var.fix()
            if entry.capex_constraint is not None:
                entry.capex_constraint.deactivate()

    def set_design_mode(self) -> None:
        """Unfix every registered sizing Var and activate its capex constraint.

        The design objective is ``total_cost`` (operating + capital). In M07
        the sizing registry is empty, so this is a no-op. Idempotent. Single-model
        only — multi-period sizing is the M16 design wrapper, not this mode.
        """
        for entry in self._registered_sizing:
            entry.var.unfix()
            if entry.capex_constraint is not None:
                entry.capex_constraint.activate()

    # -- reported cost (post-solve; never the objective, R4/R9/§6) --------

    def report_cost(self, model) -> CostReport:
        """Return the reported, categorized cost, evaluated **post-solve**.

        The user-facing cost (§6 reporting rule; M13 surfaces it). Operating
        electricity/fuel are EECO **post-hoc** evaluations on the realized
        dispatch; fixed is the config constant; DR revenue is ``0`` in v0
        (containers-only); capital is read off the (empty in v0) capex block.
        This is an independent recomputation — never ``value(model.objective)``,
        which is a relaxed/scalarized proxy (R4/R9).

        Args:
            model: The solved model (accepted for the documented API; the costing
                block reads its own components).

        Returns:
            The :class:`CostReport` breakdown.
        """
        tb = self.config.time_block
        dt_hours = pyo.value(pyunits.convert(tb.dt, pyunits.hr))
        realized_power = np.array(
            [pyo.value(self.aggregate_electrical_power[t]) for t in tb.time_index]
        )
        electricity = evaluate_cost(
            realized_power,
            self._tariff,
            dt_hours,
            dr_config=self.dr,
            time_index=tb.datetime_index,
        )
        fuel = 0.0  # no gas leg in v0
        fixed = float(pyo.value(self.opex.fixed_operating_cost))
        dr_revenue = 0.0  # DR containers-only (do not fabricate a credit)
        operating = OperatingCostBreakdown(
            electricity=electricity,
            fuel=fuel,
            fixed=fixed,
            dr_revenue=dr_revenue,
            total=electricity + fuel + fixed - dr_revenue,
        )
        # Capex is an empty placeholder in v0 -> no per-component capital costs.
        by_component: dict[str, float] = {}
        capital = CapitalCostBreakdown(
            by_component=by_component,
            total=float(pyo.value(self.aggregate_capital_cost)),
        )
        return CostReport(
            operating=operating,
            capital=capital,
            total=operating.total + capital.total,
        )
