"""FlexCosting: the costing block that wraps EECO (architecture §3.6).

``FlexCosting`` subclasses IDAES :class:`FlowsheetCostingBlockData` for its
registration/CapEx machinery and organizes every cost into two sub-blocks it
owns:

* **``opex``** — all operating cost: **electricity** and **fuel** cost (both
  delegated to the external EECO package via the :mod:`flexops.costing.opex`
  bridge), a user-defined **fixed operating cost** (maintenance/labor/chemicals),
  and any **scalar operating cost** (non-energy flows/supplies/products priced
  natively in flex-pse, never through EECO). ``opex.total_operating_cost`` is
  their sum and is re-exposed as :attr:`aggregate_operating_cost`.
* **``capex``** — capital cost. In v0 an **empty placeholder**
  (``total_capital_cost == 0``, re-exposed as :attr:`aggregate_capital_cost`);
  later milestones aggregate per-unit capital costs into it. Capital cost enters
  the objective **only in design mode** (:meth:`set_design_mode`); the
  operations-mode objective is :attr:`aggregate_operating_cost` alone.

Every quantity FlexCosting exposes is a decision-visible ``Var`` defined by an
equality ``Constraint`` (not a bare ``Expression``), so aggregate power, the
per-line-item costs, the annualized cost, and the totals are all first-class
model variables.

**Energy carriers.** FlexCosting aggregates every registered power draw into an
indexed kW series :attr:`aggregate_power` ``[t, carrier]``: ``"electrical"``, one
carrier per registered **fuel** name (natural gas, hydrogen, biogas, …), and one
per distinct **thermal** temperature (``"thermal@<T>K"`` — heat duties at
different temperatures are never summed together). Electrical and fuel carriers
are billed through EECO (electricity via ``add_electricity_cost``; each fuel via
``add_fuel_cost`` against the same tariff, normalized to EECO's gas-usage units
with the fuel's heating value). FlexCosting writes **no** tariff cost math of its
own (that is EECO's).

Construction-order invariant: FlexCosting may be constructed before any units
exist, because all aggregation and the EECO call are deferred to
:meth:`cost_process`, which **pulls** every unit's registered power from the
model (via :func:`~flexops.core.registration.iter_io_registry`).
"""

import dataclasses
import logging
from typing import Any

import numpy as np
import pyomo.environ as pyo
from idaes.core import FlowsheetCostingBlockData, declare_process_block_class
from pyomo.common.config import ConfigValue
from pyomo.core.base.units_container import UnitsError
from pyomo.environ import units as pyunits
from pyomo.util.check_units import assert_units_consistent

from flexcore import nomenclature as nm
from flexcore.exceptions import FlexConfigError
from flexops.core.registration import iter_io_registry
from flexops.costing.opex import (
    EECO_POWER_UNITS,
    DRConfig,
    add_electricity_cost,
    add_fuel_cost,
    evaluate_cost,
    evaluate_fuel_cost,
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
        fuel: EECO post-hoc fuel bill summed over registered fuels ($); ``0`` in
            v0 when no fuel-consuming unit is present.
        fixed: The configured fixed operating cost ($, a constant).
        scalar: Non-energy scalar operating cost summed over registered
            scalar-cost entries ($); ``0`` when none registered.
        dr_revenue: Demand-response incentive credit ($, subtracted); ``0`` in v0
            (DR is containers-only).
        total: ``electricity + fuel + fixed + scalar - dr_revenue`` ($).
    """

    electricity: float
    fuel: float
    fixed: float
    scalar: float
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
class FuelSpec:
    """A fuel registered on FlexCosting for EECO gas-leg billing.

    Attributes:
        name: The fuel's name (e.g. ``"natural_gas"``, ``"hydrogen"``); the
            carrier key its kW draws aggregate under.
        heating_value: The fuel's energy content in kWh per ``fuel_units``, used
            to convert the fuel's kW draw into its EECO usage rate.
        fuel_units: The Pyomo unit the fuel is metered/billed in — volumetric
            (``m**3``) or energy (``therm``) — matching the tariff's gas charge
            basis. The EECO usage series is built in ``fuel_units / hr``.
    """

    name: str
    heating_value: float
    fuel_units: Any


@dataclasses.dataclass
class ScalarCostSpec:
    """A non-energy scalar cost registered on FlexCosting (never billed via EECO).

    Attributes:
        name: The cost's name (e.g. ``"water"``, ``"chemicals"``).
        quantity: The time-indexed ``Var``/``Expression`` being costed (a rate).
        price: The signed price per unit quantity (positive = cost, negative =
            revenue/credit).
        quantity_units: The Pyomo units ``quantity`` is converted to before
            costing (a rate, e.g. ``m**3/hr``); a quantity that does not convert
            raises, forcing unit consistency.
    """

    name: str
    quantity: Any
    price: float
    quantity_units: Any


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
    CONFIG.declare(
        "lifetime_years",
        ConfigValue(
            default=20.0,
            domain=float,
            description="Plant lifetime in years, used with discount_rate to form "
            "the capital recovery factor that annualizes capital cost.",
        ),
    )
    CONFIG.declare(
        "discount_rate",
        ConfigValue(
            default=0.08,
            domain=float,
            description="Annual discount rate (fraction) for the capital recovery "
            "factor. 0 falls back to straight-line 1/lifetime.",
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
        """No-op: flex-native process costs are built in :meth:`cost_process`.
        Required override from IDAES `FlowsheetCostingBlockData`."""

    def initialize_build(self) -> None:
        """No-op: FlexCosting builds only Vars/Constraints/Params, nothing to init.
        Required override from IDAES `FlowsheetCostingBlockData`."""

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

        # _registered_power: units that opted into this costing package. Power
        # AGGREGATION does not read this -- it pulls from the model in
        # cost_process so it is construction-order independent.
        self._registered_power: list[tuple[Any, Any, nm.PowerKind]] = []
        # _registered_sizing: sizing Vars + capex constraints the modes toggle.
        self._registered_sizing: list[_SizingEntry] = []
        # Fuels and non-energy scalar costs registered on this block.
        self._registered_fuels: dict[str, FuelSpec] = {}
        self._registered_scalar_costs: dict[str, ScalarCostSpec] = {}

    # -- registration ---------------

    def register_unit_power(self, unit, var, kind: nm.PowerKind) -> None:
        """Record that ``unit`` associated a power draw with this costing package.

        Called by :meth:`~flexops.core.ops_block.OpsBlockData.register_power` when
        a unit is built with ``costing_package=`` set. The record is the explicit
        unit↔costing association used for capex attribution; power aggregation in
        :meth:`cost_process` pulls from the model instead, so it
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

    def register_fuel(
        self, name: str, heating_value: float, *, fuel_units=None
    ) -> FuelSpec:
        """Register a fuel to be billed through EECO's gas leg.

        The fuel's kW draws (declared with ``PowerKind.FUEL`` and this ``name``)
        aggregate under carrier ``name`` and are billed via
        :func:`~flexops.costing.add_fuel_cost` against the same tariff loaded
        at construction, normalized to the fuel's usage rate (``fuel_units/hr``)
        with ``heating_value``. flex-pse synthesizes no tariff content; if the
        tariff lacks the ``gas``-utility rows the fuel needs, EECO's own
        validation raises when :meth:`cost_process` runs.

        Args:
            name: The fuel's name (the carrier key its kW draws aggregate under).
            heating_value: The fuel's energy content in kWh per ``fuel_units``
                (e.g. ~10.5 for natural gas in kWh/m³; ~29.3 for a therm basis).
            fuel_units: The Pyomo unit the fuel is metered/billed in, matching the
                tariff's gas charge basis — volumetric (``pyunits.m**3``, the
                default) or energy (``pyunits.therm``).

        Returns:
            The stored :class:`FuelSpec`.

        Raises:
            FlexConfigError: If ``name`` was already registered.
        """
        if name in self._registered_fuels:
            raise FlexConfigError(
                f"Fuel {name!r} is already registered.", field="name", value=name
            )
        if fuel_units is None:
            fuel_units = pyunits.m**3
        spec = FuelSpec(name=name, heating_value=heating_value, fuel_units=fuel_units)
        self._registered_fuels[name] = spec
        return spec

    def fuel_spec(self, name: str) -> FuelSpec:
        """Return the :class:`FuelSpec` registered under ``name``.

        Args:
            name: A registered fuel name.

        Returns:
            The stored :class:`FuelSpec`.

        Raises:
            KeyError: If ``name`` is not a registered fuel.
        """
        return self._registered_fuels[name]

    def register_scalar_cost(
        self, name: str, quantity, price: float, quantity_units
    ) -> ScalarCostSpec:
        """Register a non-energy scalar operating cost (never billed via EECO).

        Costs an arbitrary time-indexed rate as ``price × Σ_t quantity[t] × dt`` —
        e.g. water withdrawal ($/m³), chemical dosing ($/kg), or a product-revenue
        credit (a negative ``price``). Built entirely in flex-pse; EECO is not
        involved.

        Args:
            name: The cost's name.
            quantity: A time-indexed ``Var``/``Expression`` (a rate).
            price: The signed price per unit quantity (positive = cost, negative
                = revenue/credit).
            quantity_units: The Pyomo units ``quantity`` is converted to before
                costing (a rate, e.g. ``m**3/hr``).

        Returns:
            The stored :class:`ScalarCostSpec`.

        Raises:
            FlexConfigError: If ``name`` was already registered.
        """
        if name in self._registered_scalar_costs:
            raise FlexConfigError(
                f"Scalar cost {name!r} is already registered.",
                field="name",
                value=name,
            )
        spec = ScalarCostSpec(
            name=name, quantity=quantity, price=price, quantity_units=quantity_units
        )
        self._registered_scalar_costs[name] = spec
        return spec

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

    # -- carrier helpers --------------------------------------------------

    @staticmethod
    def _carrier_key(record) -> str:
        """Return the aggregation carrier key for a power record.

        Electrical draws share one ``"electrical"`` carrier; fuel draws use their
        fuel name; thermal draws use a per-temperature label
        (``"thermal@<T>K"``) so duties at different temperatures never mix.

        Args:
            record: A :class:`~flexops.core.registration.PowerRecord`.

        Returns:
            The carrier key string.
        """
        if record.kind is nm.PowerKind.FUEL:
            return record.fuel_name
        if record.kind is nm.PowerKind.THERMAL:
            temp_k = pyo.value(pyunits.convert(record.temperature, pyunits.K))
            return f"thermal@{temp_k:.6g}K"
        return "electrical"

    # -- cost_process (all aggregation + the EECO call, deferred here) ----

    def cost_process(self) -> None:
        """Aggregate power, build the ``opex``/``capex`` blocks, enter operations mode.

        Overrides the parent ``FlowsheetCostingBlockData.cost_process`` (whose
        ``aggregate_capital_cost`` Var would collide with the flex-native
        names): FlexCosting builds its own flex-native Vars/Constraints and does
        not invoke the parent aggregation machinery. Every derived quantity is a
        ``Var`` defined by an ``eq_<name>`` equality ``Constraint``.
        """
        tb = self.config.time_block
        cur = self._currency
        dt_hours = pyo.value(pyunits.convert(tb.dt, pyunits.hr))

        self._build_power_aggregation(tb)
        self._build_opex(tb, cur, dt_hours)
        self._build_capex(cur)
        self._build_totals_and_annualization(tb, cur)

        self.set_operations_mode()  # default final state (scheduling first)

    def _build_power_aggregation(self, tb) -> None:
        """Build the indexed per-carrier kW aggregation (Var + Constraint).

        Pulls every registered power draw from the model, buckets it by carrier
        (``"electrical"`` / fuel name / ``"thermal@<T>K"``), and defines
        ``aggregate_power[t, carrier]`` in kW. ``pyunits.convert(v[t], kW)``
        loudly rejects any draw that is not a power. Also exposes the API-freeze
        ``aggregate_electrical_power`` (a Reference) and a temperature-blind
        ``aggregate_thermal_power`` total.
        """
        vars_by_carrier: dict[str, list] = {}
        for _block, registry in iter_io_registry(self.model()):
            for rec in registry.power:
                vars_by_carrier.setdefault(self._carrier_key(rec), []).append(rec.var)

        carriers = set(vars_by_carrier) | {"electrical"} | set(self._registered_fuels)
        carriers = sorted(carriers)
        thermal_carriers = [c for c in carriers if c.startswith("thermal@")]

        self.aggregate_power = pyo.Var(
            tb.time_index,
            carriers,
            initialize=0.0,
            units=pyunits.kW,
            doc="Aggregate power by carrier (kW).",
        )

        def _agg_rule(_b, t, carrier):
            terms = vars_by_carrier.get(carrier, [])
            return self.aggregate_power[t, carrier] == (
                sum(pyunits.convert(v[t], pyunits.kW) for v in terms) + 0 * pyunits.kW
            )

        self.eq_aggregate_power = pyo.Constraint(
            tb.time_index, carriers, rule=_agg_rule
        )

        # API-freeze accessors: electrical (a Reference) + a temperature-blind
        # thermal total (its own Var + Constraint, 0 when no thermal draws).
        self.aggregate_electrical_power = pyo.Reference(
            self.aggregate_power[:, "electrical"]
        )
        self.aggregate_thermal_power = pyo.Var(
            tb.time_index,
            initialize=0.0,
            units=pyunits.kW,
            doc="Aggregate thermal duty across all temperatures (kW).",
        )

        def _therm_rule(_b, t):
            return self.aggregate_thermal_power[t] == (
                sum(self.aggregate_power[t, c] for c in thermal_carriers)
                + 0 * pyunits.kW
            )

        self.eq_aggregate_thermal_power = pyo.Constraint(
            tb.time_index, rule=_therm_rule
        )

    def _build_opex(self, tb, cur, dt_hours) -> None:
        """Build the ``opex`` block: electricity + fuel + fixed + scalar (Vars).

        Electricity and every registered fuel are billed via the ``opex.py``
        bridge (EECO owns the cost math). Fuel legs are built on per-fuel
        sub-blocks so EECO's ``gas_*`` components never collide. Non-energy scalar
        costs are built natively (no EECO). A final unit-consistency check forces
        every operating-cost line item onto the tariff currency or errors loudly.
        """
        self.opex = pyo.Block(
            doc="All operating cost: electricity + fuel + fixed + scalar."
        )
        opex = self.opex

        # --- electricity: normalize to EECO power units, then bill ---------
        opex.eeco_aggregate_electrical_power = pyo.Var(
            tb.time_index, initialize=0.0, units=EECO_POWER_UNITS
        )

        def _norm_elec(_b, t):
            return opex.eeco_aggregate_electrical_power[t] == pyunits.convert(
                self.aggregate_power[t, "electrical"], EECO_POWER_UNITS
            )

        opex.eq_eeco_aggregate_electrical_power = pyo.Constraint(
            tb.time_index, rule=_norm_elec
        )
        elec = add_electricity_cost(
            block=opex,
            electrical_power=opex.eeco_aggregate_electrical_power,
            time_index=tb.datetime_index,
            dt_hours=dt_hours,
            tariff=self._tariff,
            dr_config=self.dr,
        )
        opex.electricity_cost = pyo.Var(
            initialize=0.0, units=cur, doc="EECO electricity cost ($)."
        )
        opex.eq_electricity_cost = pyo.Constraint(
            expr=opex.electricity_cost == elec.total_operating_cost * cur
        )

        # --- fuels: normalize kW -> m^3/hr, bill each on its own sub-block --
        fuel_names = sorted(self._registered_fuels)
        for name in fuel_names:
            self._build_fuel_leg(tb, cur, dt_hours, name)

        opex.fuel_cost = pyo.Var(
            initialize=0.0, units=cur, doc="Total EECO fuel cost ($)."
        )
        opex.eq_fuel_cost = pyo.Constraint(
            expr=opex.fuel_cost
            == sum(getattr(opex, f"fuel_cost_{n}") for n in fuel_names) + 0 * cur
        )

        # --- fixed operating cost (a config constant) ---------------------
        opex.fixed_operating_cost = pyo.Param(
            initialize=self.config.fixed_operating_cost,
            mutable=True,
            units=cur,
            doc="Non-tariff fixed operating cost ($ over the horizon).",
        )

        # --- non-energy scalar costs (native; never via EECO) -------------
        scalar_names = sorted(self._registered_scalar_costs)
        for name in scalar_names:
            self._build_scalar_leg(tb, cur, dt_hours, name)

        opex.scalar_cost = pyo.Var(
            initialize=0.0, units=cur, doc="Total non-energy scalar cost ($)."
        )
        opex.eq_scalar_cost = pyo.Constraint(
            expr=opex.scalar_cost
            == sum(getattr(opex, f"scalar_cost_{n}") for n in scalar_names) + 0 * cur
        )

        # --- total operating cost -----------------------------------------
        opex.total_operating_cost = pyo.Var(
            initialize=0.0, units=cur, doc="electricity + fuel + fixed + scalar ($)."
        )
        opex.eq_total_operating_cost = pyo.Constraint(
            expr=opex.total_operating_cost
            == opex.electricity_cost
            + opex.fuel_cost
            + opex.fixed_operating_cost
            + opex.scalar_cost
        )

        self._build_dr()  # no-op in v0
        self._assert_cost_units_consistent(fuel_names, scalar_names)

    def _build_fuel_leg(self, tb, cur, dt_hours, name: str) -> None:
        """Normalize a fuel's kW draw to a usage rate; bill it via EECO's gas leg."""
        opex = self.opex
        spec = self._registered_fuels[name]
        heating_value = spec.heating_value * pyunits.kWh / spec.fuel_units
        usage_units = spec.fuel_units / pyunits.hr

        usage = pyo.Var(
            tb.time_index,
            initialize=0.0,
            units=usage_units,
            doc=f"EECO usage rate for fuel {name} ({usage_units}).",
        )
        opex.add_component(f"eeco_gas_usage_{name}", usage)

        def _norm_fuel(_b, t):
            return usage[t] == pyunits.convert(
                self.aggregate_power[t, name] / heating_value, usage_units
            )

        opex.add_component(
            f"eq_eeco_gas_usage_{name}",
            pyo.Constraint(tb.time_index, rule=_norm_fuel),
        )

        # EECO namespaces its gas_* components by utility, not by fuel; give each
        # fuel its own sub-block so multiple fuels never collide.
        leg = pyo.Block()
        opex.add_component(f"fuel_{name}", leg)
        fuel = add_fuel_cost(
            block=leg,
            fuel_power=usage,
            time_index=tb.datetime_index,
            dt_hours=dt_hours,
            tariff=self._tariff,
            dr_config=self.dr,
        )
        cost = pyo.Var(initialize=0.0, units=cur, doc=f"EECO cost of fuel {name} ($).")
        opex.add_component(f"fuel_cost_{name}", cost)
        opex.add_component(
            f"eq_fuel_cost_{name}",
            pyo.Constraint(expr=cost == fuel.total_operating_cost * cur),
        )

    def _build_scalar_leg(self, tb, cur, dt_hours, name: str) -> None:
        """Build one native scalar-cost line item (price × Σ quantity × dt)."""
        opex = self.opex
        spec = self._registered_scalar_costs[name]
        # price is a bare $/quantity value; attach cur/(quantity_units*hr) so the
        # constraint is dimensionally consistent and a mis-unit quantity raises.
        unit_factor = cur / (spec.quantity_units * pyunits.hr)
        cost = pyo.Var(initialize=0.0, units=cur, doc=f"Scalar cost {name} ($).")
        opex.add_component(f"scalar_cost_{name}", cost)
        integral = (
            sum(
                pyunits.convert(spec.quantity[t], spec.quantity_units)
                for t in tb.time_index
            )
            * dt_hours
            * pyunits.hr
        )
        opex.add_component(
            f"eq_scalar_cost_{name}",
            pyo.Constraint(expr=cost == spec.price * integral * unit_factor),
        )

    def _assert_cost_units_consistent(self, fuel_names, scalar_names) -> None:
        """Force every operating-cost line item onto the tariff currency, or error.

        Runs Pyomo's unit-consistency check over the operating-cost equality
        constraints and re-raises any inconsistency as a :class:`FlexConfigError`
        (the "force consistency or loudly error" rule): the fixed operating cost
        and any scalar cost must reconcile with the tariff's currency.
        """
        opex = self.opex
        constraints = [
            opex.eq_total_operating_cost,
            opex.eq_electricity_cost,
            opex.eq_fuel_cost,
            opex.eq_scalar_cost,
        ]
        constraints += [getattr(opex, f"eq_fuel_cost_{n}") for n in fuel_names]
        constraints += [getattr(opex, f"eq_scalar_cost_{n}") for n in scalar_names]
        try:
            for con in constraints:
                assert_units_consistent(con)
        except UnitsError as exc:
            raise FlexConfigError(
                "Operating-cost line items are not dimensionally consistent with "
                f"the tariff currency ({self._currency}); reconcile the units: "
                f"{exc}",
                field="fixed_operating_cost",
            ) from exc

    def _build_capex(self, cur) -> None:
        """Build the empty ``capex`` placeholder block (total_capital_cost == 0)."""
        self.capex = pyo.Block(doc="Capital cost (empty placeholder in v0).")
        self.capex.total_capital_cost = pyo.Var(
            initialize=0.0,
            units=cur,
            doc="Sum of registered units' capital cost; 0 in v0.",
        )
        self.capex.eq_total_capital_cost = pyo.Constraint(
            expr=self.capex.total_capital_cost == 0 * cur
        )

    def _build_totals_and_annualization(self, tb, cur) -> None:
        """Build the aggregate cost Vars, the design-mode total, and annualized cost."""
        self.aggregate_operating_cost = pyo.Var(
            initialize=0.0,
            units=cur,
            doc="IDAES-aggregate name for the opex total (operations objective).",
        )
        self.eq_aggregate_operating_cost = pyo.Constraint(
            expr=self.aggregate_operating_cost == self.opex.total_operating_cost
        )

        self.aggregate_capital_cost = pyo.Var(
            initialize=0.0,
            units=cur,
            doc="IDAES-aggregate name for the capex total (0 in v0).",
        )
        self.eq_aggregate_capital_cost = pyo.Constraint(
            expr=self.aggregate_capital_cost == self.capex.total_capital_cost
        )

        self.total_cost = pyo.Var(
            initialize=0.0,
            units=cur,
            doc="Design-mode objective: operating + capital cost ($).",
        )
        self.eq_total_cost = pyo.Constraint(
            expr=self.total_cost
            == self.aggregate_operating_cost + self.aggregate_capital_cost
        )

        # Annualization: capital recovery factor + opex scaled horizon -> year.
        i = self.config.discount_rate
        n = self.config.lifetime_years
        crf = (1.0 / n) if i == 0 else i * (1 + i) ** n / ((1 + i) ** n - 1)
        self.lifetime = pyo.Param(
            initialize=n,
            mutable=True,
            units=pyunits.year,
            doc="Plant lifetime (years).",
        )
        self.capital_recovery_factor = pyo.Param(
            initialize=crf,
            mutable=True,
            units=1 / pyunits.year,
            doc="Capital recovery factor (1/year).",
        )
        horizon_years = pyo.value(pyunits.convert(tb.horizon, pyunits.year))
        self.annualized_cost = pyo.Var(
            initialize=0.0,
            units=cur / pyunits.year,
            doc="Total cost on an annual basis ($/year).",
        )
        self.eq_annualized_cost = pyo.Constraint(
            expr=self.annualized_cost
            == self.aggregate_operating_cost / (horizon_years * pyunits.year)
            + self.aggregate_capital_cost * self.capital_recovery_factor
        )

    # -- design / operations modes --

    def set_operations_mode(self) -> None:
        """Fix every registered sizing Var and deactivate its capex constraint.

        The operations objective is ``aggregate_operating_cost`` alone. In the
        sizing registry is empty, so this is a no-op; it is the documented
        single-model toggle later milestones populate. Idempotent.
        """
        for entry in self._registered_sizing:
            entry.var.fix()
            if entry.capex_constraint is not None:
                entry.capex_constraint.deactivate()

    def set_design_mode(self) -> None:
        """Unfix every registered sizing Var and activate its capex constraint.

        The design objective is ``total_cost`` (operating + capital).
        """
        for entry in self._registered_sizing:
            entry.var.unfix()
            if entry.capex_constraint is not None:
                entry.capex_constraint.activate()

    # -- reported cost (post-solve; never the objective, §6) --------

    def report_cost(self, model) -> CostReport:
        """Return the reported, categorized cost, evaluated **post-solve**.

        The user-facing cost (§6 reporting rule; M13 surfaces it). Operating
        electricity/fuel are EECO **post-hoc** evaluations on the realized
        dispatch; fixed is the config constant; scalar costs are recomputed
        natively; DR revenue is ``0`` in v0 (containers-only); capital is read off
        the (empty in v0) capex block. This is an independent recomputation —
        never ``value(model.objective)``, which is a relaxed/scalarized proxy.

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

        fuel = 0.0
        for name in self._registered_fuels:
            usage = getattr(self.opex, f"eeco_gas_usage_{name}")
            realized_usage = np.array([pyo.value(usage[t]) for t in tb.time_index])
            fuel += evaluate_fuel_cost(
                realized_usage,
                self._tariff,
                dt_hours,
                dr_config=self.dr,
                time_index=tb.datetime_index,
            )

        fixed = float(pyo.value(self.opex.fixed_operating_cost))

        scalar = 0.0
        for spec in self._registered_scalar_costs.values():
            scalar += (
                spec.price
                * dt_hours
                * sum(
                    pyo.value(pyunits.convert(spec.quantity[t], spec.quantity_units))
                    for t in tb.time_index
                )
            )

        dr_revenue = 0.0  # DR containers-only (do not fabricate a credit)
        operating = OperatingCostBreakdown(
            electricity=electricity,
            fuel=fuel,
            fixed=fixed,
            scalar=scalar,
            dr_revenue=dr_revenue,
            total=electricity + fuel + fixed + scalar - dr_revenue,
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
