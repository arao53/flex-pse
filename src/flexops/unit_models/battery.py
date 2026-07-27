r"""BatteryModel: SOC dynamics + first-class DERMS dispatch (M08, §3.4/§3.6, R4/R9).

No fluid ports (no ``property_package`` needed): a battery is an energy-only
unit with two dispatch-input actuators, ``power_charge[t]``/
``power_discharge[t]`` (kW), and a state of charge tracked in absolute energy
terms, ``charge[t]`` (kWh). ``capacity`` is the fixable sizing ``Var`` (R4):
fixed at the constructor value by default (operations mode); a
``costing_package=`` associates it with :meth:`FlexCostingData.set_design_mode`/
:meth:`~flexops.costing.flex_costing.FlexCostingData.set_operations_mode`.

**Deviations from the milestone spec** (recorded per ``CLAUDE.md``; see the PR
description for the full rationale):

* ``unit_commitment.status`` is **not** forced off. The milestone text has one
  line claiming a battery forces it off "similar to a storage tank," which
  directly contradicts the same document's "Applying UC per unit" section
  ("the battery enables status for mutually-exclusive charge/discharge") and
  its own required tests (an arbitrage MIP with unit commitment enabled, then
  relaxed for an LP bound). ``UnitCommitmentConfig.status`` defaults ``True``
  project-wide; here, when ``True``, :func:`~flexops.logic.status.add_status`
  is attached to ``power_charge`` and a symmetric ``discharge_exclusivity``
  constraint bounds ``power_discharge`` by ``(1 - status)`` -- one binary
  gives mutually-exclusive charge/discharge. This requires both
  ``power_charge_max``/``power_discharge_max`` (the semicontinuous link needs
  a finite bound); a caller who wants an unbounded, non-UC battery passes
  ``unit_commitment=UnitCommitmentConfig(status=False)``.
* ``soc[t]`` is a derived **Expression** (``charge[t] / capacity``), not a
  Var tied to ``capacity`` by an equality Constraint. The spec's literal
  ``charge[t] == soc[t] * capacity`` is a genuine product of two *free*
  Vars whenever ``capacity`` is unfixed (design mode) -- an unconditional
  bilinear equality that would force every design-mode solve to NLP,
  contradicting the milestone's own ``needs_highs`` marker on the
  external-dispatch sizing-only solve. The SOC bounds instead constrain
  ``charge[t]`` directly against ``soc_min``/``soc_max`` times ``capacity``
  (a constant times a Var -- linear, per Pitfall 1's own reasoning), so
  ``capacity`` never multiplies another free Var and every battery model
  stays LP/MILP regardless of design/operations mode.
* ``charge_balance`` covers **every** ``t``, including ``t=0`` (referencing
  ``charge_init`` in place of ``charge[t-1]`` at the boundary), rather than
  the spec's separate ``t=1..N-1`` difference equation plus an unrelated
  ``charge[0] == charge_init``/``soc[0] == soc_init`` initial condition.
  Leaving ``t=0`` governed only by a hard pin on ``charge[0]`` -- as
  ``Tank.initial_volume_eq`` does -- would leave
  ``power_charge[0]``/``power_discharge[0]`` completely free of any
  energy-conservation tie: the MIP arbitrage test caught this directly, with
  the solver "discharging" a free 50 kW at ``t=0`` for a cost rebate with no
  physical backing. Folding ``t=0`` into ``charge_balance`` closes that hole
  and drops the need for a separate, differently-named initial-condition
  constraint.

.. math::

    \text{charge}[t] = \text{charge}[t-1] + \Delta t \left(
        \eta_{charge} \, P_{charge}[t] - \frac{P_{discharge}[t]}{\eta_{discharge}}
    \right), \quad t = 0, \dots, N-1 \;\; (\text{charge}[-1] := \text{charge\_init})

Usage::

    >>> from flexops.testing import dummy_time_block
    >>> from flexops.unit_models import BatteryModel
    >>> from pyomo.environ import units as pyunits
    >>> m = dummy_time_block(4)
    >>> m.battery = BatteryModel(capacity=10 * pyunits.kWh)  # doctest: +SKIP

Config: see ``capacity``, ``power_charge_max``/``power_discharge_max``,
``eta_charge``/``eta_discharge``, ``soc_min``/``soc_max``, ``initial_soc``
below, plus the inherited ``relaxation``/``unit_commitment``/
``external_dispatch``/``costing_package`` (architecture §3.2).

**Behind-the-meter assumption (v0).** ``power_electrical[t]`` may go negative
(discharge exports power); any facility-level "net draw >= 0" constraint
belongs at the plant/costing level, not here.
"""

import pyomo.environ as pyo
from idaes.core import declare_process_block_class
from pyomo.common.config import ConfigValue
from pyomo.environ import units as pyunits

from flexcore import nomenclature as nm
from flexcore.exceptions import FlexConfigError
from flexops.core.ops_block import OpsBlockData
from flexops.logic.status import add_status


def _fraction_domain(value):
    """ConfigValue domain: a fraction in [0, 1]."""
    if isinstance(value, (int, float)) and 0.0 <= value <= 1.0:
        return float(value)
    raise FlexConfigError(f"Expected a fraction in [0, 1], got {value!r}.", value=value)


def _efficiency_domain(value):
    """ConfigValue domain: an efficiency fraction in (0, 1]."""
    if isinstance(value, (int, float)) and 0.0 < value <= 1.0:
        return float(value)
    raise FlexConfigError(
        f"Expected an efficiency fraction in (0, 1], got {value!r}.", value=value
    )


@declare_process_block_class("BatteryModel")
class BatteryModelData(OpsBlockData):
    """A battery: SOC dynamics, fixable capacity, DERMS dispatch (module docstring)."""

    CONFIG = OpsBlockData.CONFIG()
    CONFIG.declare(
        "capacity",
        ConfigValue(
            description="Initial battery energy capacity, kWh -- the fixed "
            "design Var value at construction. Required."
        ),
    )
    CONFIG.declare(
        "power_charge_max",
        ConfigValue(
            default=None,
            description="Maximum charging power, kW. None (default) leaves "
            "power_charge unbounded above; required (along with "
            "power_discharge_max) when unit_commitment.status is enabled, "
            "since the semicontinuous link needs a finite bound.",
        ),
    )
    CONFIG.declare(
        "power_discharge_max",
        ConfigValue(
            default=None,
            description="Maximum discharging power, kW. Same requirement as "
            "power_charge_max.",
        ),
    )
    CONFIG.declare(
        "eta_charge",
        ConfigValue(
            default=1.0,
            domain=_efficiency_domain,
            description="Charging efficiency, a fraction in (0, 1].",
        ),
    )
    CONFIG.declare(
        "eta_discharge",
        ConfigValue(
            default=1.0,
            domain=_efficiency_domain,
            description="Discharging efficiency, a fraction in (0, 1].",
        ),
    )
    CONFIG.declare(
        "soc_min",
        ConfigValue(
            default=0.0,
            domain=_fraction_domain,
            description="Minimum state of charge, a fraction of capacity in [0, 1].",
        ),
    )
    CONFIG.declare(
        "soc_max",
        ConfigValue(
            default=1.0,
            domain=_fraction_domain,
            description="Maximum state of charge, a fraction of capacity in [0, 1].",
        ),
    )
    CONFIG.declare(
        "initial_soc",
        ConfigValue(
            default=0.5,
            domain=_fraction_domain,
            description="Initial state of charge, a fraction of capacity in "
            "[0, 1]; fixes charge[0] via charge_init (rolling-horizon state).",
        ),
    )

    def build(self) -> None:
        """Build capacity, power/charge dynamics, SOC bounds, and UC status."""
        super().build()
        tb = self._find_time_block()

        if self.config.unit_commitment.status and (
            self.config.power_charge_max is None
            or self.config.power_discharge_max is None
        ):
            raise FlexConfigError(
                "BatteryModel requires both power_charge_max and "
                "power_discharge_max when unit_commitment.status is enabled "
                "(the default): the mutually-exclusive charge/discharge link "
                "needs a finite bound. Pass both, or disable status via "
                "unit_commitment=UnitCommitmentConfig(status=False).",
                field="unit_commitment.status",
                value=True,
            )

        capacity_val = pyo.value(pyunits.convert(self.config.capacity, pyunits.kWh))
        self.capacity = pyo.Var(
            initialize=capacity_val,
            bounds=(0.0, None),
            units=pyunits.kWh,
            doc="Chosen battery energy capacity (design Var, R4); fixed at "
            "the constructor value by default (operations mode); "
            "costing.set_design_mode() unfixes it.",
        )
        self.capacity.fix(capacity_val)
        self.register_process_parameter(self.capacity, regressable=False)
        costing_package = self.config.costing_package
        if costing_package is not None:
            costing_package.register_sizing_variable(self.capacity)

        charge_max_val = (
            pyo.value(pyunits.convert(self.config.power_charge_max, pyunits.kW))
            if self.config.power_charge_max is not None
            else None
        )
        discharge_max_val = (
            pyo.value(pyunits.convert(self.config.power_discharge_max, pyunits.kW))
            if self.config.power_discharge_max is not None
            else None
        )

        self.power_charge = pyo.Var(
            tb.time_index,
            initialize=0.0,
            bounds=(0.0, charge_max_val),
            units=pyunits.kW,
            doc="Charging power draw (dispatch input).",
        )
        self.power_discharge = pyo.Var(
            tb.time_index,
            initialize=0.0,
            bounds=(0.0, discharge_max_val),
            units=pyunits.kW,
            doc="Discharging power output (dispatch input).",
        )
        self.register_io_variable(self.power_charge, role="input")
        self.register_io_variable(self.power_discharge, role="input")

        # declare_power builds power_electrical with no domain kwarg, so it
        # defaults to Reals (Pitfall 3) -- discharge must export power as a
        # negative draw.
        power = self.declare_power(nm.PowerKind.ELECTRICAL)
        self.register_io_variable(power, role="output")

        @self.Constraint(
            tb.time_index,
            doc="power_electrical == power_charge - power_discharge "
            "(discharge is a negative/export draw; v0 is behind-the-meter).",
        )
        def net_electrical(b, t):
            return power[t] == b.power_charge[t] - b.power_discharge[t]

        self.charge = pyo.Var(
            tb.time_index,
            bounds=(0.0, None),
            units=pyunits.kWh,
            doc="Stored energy content.",
        )

        initial_charge_val = self.config.initial_soc * capacity_val
        self.charge_init = pyo.Param(
            initialize=initial_charge_val,
            mutable=True,
            units=pyunits.kWh,
            doc="Initial stored energy, charge[0] (rolling-horizon initial state).",
        )
        tb.register_initial_state(self.charge_init)
        self.register_process_parameter(self.charge_init, regressable=False)

        eta_charge = self.config.eta_charge
        eta_discharge = self.config.eta_discharge

        @self.Constraint(
            tb.time_index,
            doc="Charge holdup (backward difference, conventions §2): "
            "charge[t] == charge[t-1] + dt*(eta_charge*power_charge[t] - "
            "power_discharge[t]/eta_discharge); t=0 references charge_init in "
            "place of charge[-1], so power_charge[0]/power_discharge[0] are "
            "energy-conserving too (a separate, unconstrained-at-t=0 initial "
            "condition would let power_charge[0]/power_discharge[0] move for "
            "free, manufacturing energy from nothing).",
        )
        def charge_balance(b, t):
            previous = b.charge[t - 1] if t > 0 else b.charge_init
            delta_charge = pyunits.convert(
                tb.dt
                * (
                    eta_charge * b.power_charge[t]
                    - b.power_discharge[t] / eta_discharge
                ),
                to_units=pyunits.kWh,
            )
            return b.charge[t] == previous + delta_charge

        soc_min = self.config.soc_min
        soc_max = self.config.soc_max

        @self.Constraint(
            tb.time_index,
            doc="Lower SOC bound, on charge (kWh) rather than a literal Var "
            "bound: capacity is itself a Var, so the bound must be a "
            "Constraint (Pitfall 1). charge[t] >= soc_min * capacity.",
        )
        def soc_lower(b, t):
            return b.charge[t] >= soc_min * b.capacity

        @self.Constraint(
            tb.time_index,
            doc="Upper SOC bound: charge[t] <= soc_max * capacity.",
        )
        def soc_upper(b, t):
            return b.charge[t] <= soc_max * b.capacity

        @self.Expression(
            tb.time_index,
            doc="State of charge, charge[t] / capacity (a reporting "
            "Expression, not a Var -- see the module docstring's "
            "'Deviations from the milestone spec').",
        )
        def soc(b, t):
            return b.charge[t] / b.capacity

        if self.config.unit_commitment.status:
            status = add_status(
                self,
                self.power_charge,
                0.0 * pyunits.kW,
                charge_max_val * pyunits.kW,
            )

            @self.Constraint(
                tb.time_index,
                doc="Mutually-exclusive discharge link: power_discharge[t] <= "
                "power_discharge_max * (1 - status[t]) (a battery may not "
                "charge and discharge in the same step).",
            )
            def discharge_exclusivity(b, t):
                return b.power_discharge[t] <= discharge_max_val * (1 - status[t])

    def set_dispatch(self, series) -> None:
        """Fix net battery dispatch from an external (DERMS) command series (R9).

        Splits ``series[t]`` (signed net kW, positive = charging, negative =
        discharging) into the ``power_charge``/``power_discharge`` actuators
        and fixes both via
        :meth:`~flexops.core.ops_block.OpsBlockData.set_external_dispatch`,
        removing the dispatch degree of freedom while leaving ``capacity``
        free (Pitfall 9). Fixing only the net ``power_electrical`` would
        leave the charge/discharge split underdetermined -- their
        efficiencies differ, so the split affects the SOC trajectory -- so
        both actuators are pinned directly instead.

        Args:
            series: A mapping or pandas Series of signed net dispatch power
                (kW), aligned to the time set (see
                :meth:`~flexops.core.ops_block.OpsBlockData.set_external_dispatch`).
        """
        tb = self._find_time_block()
        resolved = self._resolve_dispatch_series(series, tb)
        charge_series = {t: max(v, 0.0) for t, v in resolved.items()}
        discharge_series = {t: max(-v, 0.0) for t, v in resolved.items()}
        self.set_external_dispatch(self.power_charge, charge_series)
        self.set_external_dispatch(self.power_discharge, discharge_series)
