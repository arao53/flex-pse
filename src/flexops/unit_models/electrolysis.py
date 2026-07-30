r"""ElectrolysisSeparator(Separator): water electrolysis as a separation.

A physical subclass of :class:`~flexops.unit_models.separator.SeparatorData` that
offers **two levels of detail**, selected by the ``detail`` config option:

``ElectrolysisDetail.CONSTANT_INTENSITY`` (the default)
    The separator's own relationship, unchanged: one constant electrical
    intensity times the feed flow. No heat duty, no stack model, one regressable
    parameter. This is the right model for scheduling work, and the one
    FlexParameterize fits from data.

``ElectrolysisDetail.STACK``
    An **equation-oriented** stack model: a fixed set of variables and residuals
    with no assumed causal direction, whose electrochemistry is carried entirely
    by one fitted voltage correlation. Fidelity is raised by *estimating more of
    its coefficients*, never by adding equations, so there is no library of
    electrochemical constants to source.

Both levels build the total electrical draw as the Constraint
``power_electrical_relation`` -- the swap contract
(:meth:`~flexops.core.ops_block.OpsBlockData.swap_energy_relation`) is
preserved either way.

The stack model
---------------

The voltage correlation is the whole physics, five fitted coefficients over
current density and temperature offset :math:`\Delta T = T[t] - T_{ref}`:

.. math::

    V[t] = \theta_0
        + \theta_1 \, \Delta T[t]
        + \theta_2 \, \log_{10} i[t]
        + \theta_3 \, i[t]
        + \theta_4 \, i[t] \, \Delta T[t]

:math:`\theta_0` is the ``voltage_intercept``, :math:`\theta_2` the
``tafel_slope``, :math:`\theta_3` the ``area_specific_resistance``, and
:math:`\theta_1` / :math:`\theta_4` their temperature coefficients. Every one is
a fixed, **regressable** ``Var``, so a coarser model is the same equation with
coefficients estimated at zero -- ``theta_1 = theta_2 = theta_4 = 0`` leaves the
straight line :math:`V = \theta_0 + \theta_3 i`, which is also the smooth
continuation path to use when initializing the full form is hard.

The rest of the system is bookkeeping around it:

.. math::

    \frac{\dot{V}_{out,a}[t] \, \rho}{M_{H_2O}}
        &= \eta_F \, \frac{I[t] \, N_{cells}}{2F} \\
    \dot{m}_{H_2}[t] &= M_{H_2} \,
        \frac{\dot{V}_{out,a}[t] \, \rho}{M_{H_2O}} \\
    I[t] &= i[t] \, A_{cell} \\
    V_{stack}[t] &= N_{cells} \, V[t] \\
    P_{stack}[t] &= V_{stack}[t] \, I[t] \\
    P_{elec}[t] \, \eta_{rect} &= P_{stack}[t] \\
    \mathrm{SEC}[t] \, \dot{m}_{H_2}[t] &= P_{elec}[t]

The separator's own ``split_definition``/``split_mass_balance`` supply the water
side: ``outlet_a`` is the converted water, so the inherited ``split_fraction``
*is* the stack's water recovery and ``flow_in`` its feed rate.

``power_stack`` is the stack's DC draw and ``power_electrical`` the facility's AC
draw, the two separated by the ``rectifier_efficiency`` :math:`\eta_{rect}`.
**That rectifier is the only balance-of-plant item modeled**: fluid-side
auxiliaries -- feed pumps, coolant circulation, product compression -- are
deliberately out of scope, so ``power_electrical`` is a stack-boundary draw and
a caller who needs whole-plant auxiliaries adds them as their own units.

Every quantity above is a ``Var`` with a defining residual; the module builds no
``Expression``\ s at all, so the model is one flat variable/equation set. Only
three residuals are nonlinear -- ``cell_voltage_relation`` (the
:math:`\log_{10}` term), ``power_stack_relation`` and
``specific_energy_relation`` (bilinear products). ``stack_voltage_definition``
and ``power_electrical_relation`` are linear, the latter because a fitted
coefficient multiplying a free variable stays linear while the coefficient is
fixed. HHV efficiency is not carried as a variable -- it would need a bilinear
residual to define, and it is exactly ``39.4 / specific_energy_consumption``,
whose lower bound already encodes the same "no more than 100% efficient" check.

The thermal block is one option, ``thermal``, because the three treatments are
genuinely different models rather than terms to add up -- see
:class:`ThermalModel`.

Specifying the model
--------------------

Because the residuals carry no causal direction, any consistent specification
solves the same system. Only the feed flow is registered as a process
**input**, so the default solve fixes the flow and derives the current density
and the power. The two inversions that matter need no model rewrite:

* **Current-controlled** (the rectifier setpoint): fix ``current_density``,
  unfix ``flow_in``.
* **Power-following** (renewables-coupled dispatch): fix ``power_electrical``,
  unfix ``flow_in``. This is the main reason to build the unit
  equation-oriented.

Bounds
------

A bound is written into the model only when it can actually become active and is
not implied by another one. Order-of-magnitude placeholders (a current of
``1e5`` A, a power of ``1e9`` W) are left ``None``, and so is any bound another
already dominates -- a ceiling on ``specific_energy_consumption`` would be dead
structure, since ``cell_voltage_max`` caps it at ~71 kWh/kg. What remains:

* ``current_density`` strictly above zero, so ``log10`` stays defined and
  differentiable (the physical turndown floor, set by hydrogen crossover into
  the oxygen stream, is higher anyway), and below its rated ceiling
  (``current_density_min`` / ``current_density_max``).
* ``cell_voltage`` above the 1.23 V thermodynamic floor -- the one end that is a
  module constant, because it is thermodynamics -- and below
  ``cell_voltage_max``.
* ``specific_energy_consumption`` above the 39.4 kWh/kg higher-heating-value
  floor. This is **not** redundant with the voltage floor: it binds first, at
  1.38 V rather than 1.23 V, so a solve heading for over 100% efficiency is
  reported in the units where it is recognizable.
* ``stack_temperature`` between ``stack_temperature_min`` (a cold-start floor)
  and ``stack_temperature_max`` (the materials limit). Under ``HEAT_BALANCE``
  the temperature is solved, so both ends can bind.

Every configurable end is a technology choice, not physics -- an alkaline stack
tops out near 2.0 V and tolerates 363 K -- and every one is validated at
construction, including that ``stack_temperature`` lies inside its own window: a
``Var`` fixed outside its bounds is otherwise an unexplained infeasibility.

What the stack model omits: thermal transients (the electrical and thermal time
constants differ by ~4 orders of magnitude, so the steady-state form is the
right choice for dispatch and sizing), membrane hydration dynamics, degradation,
two-phase flow, and cell-to-cell variation.

Example:
    >>> from flexops.testing import dummy_time_block
    >>> from flexops.unit_models import ElectrolysisSeparator
    >>> from flexops.unit_models.electrolysis import ElectrolysisDetail
    >>> m = dummy_time_block(3)
    >>> m.cell = ElectrolysisSeparator(  # doctest: +SKIP
    ...     property_package=m.properties,
    ...     detail=ElectrolysisDetail.STACK,
    ... )
"""

import enum

import pyomo.environ as pyo
from idaes.core import declare_process_block_class
from pyomo.common.config import ConfigValue
from pyomo.environ import units as pyunits

from flexcore import nomenclature as nm
from flexcore.exceptions import FlexConfigError
from flexops.unit_models.base.sido import SIDOBlockData
from flexops.unit_models.separator import SeparatorData

# Units
CURRENT_DENSITY_UNITS = pyunits.ampere / pyunits.cm**2
RESISTANCE_UNITS = pyunits.ohm * pyunits.cm**2
SPECIFIC_ENERGY_UNITS = pyunits.kWh / pyunits.kg

# Constants
FARADAY = 96485.33 * pyunits.coulomb / pyunits.mol
ELECTRONS_PER_H2 = 2
MOLAR_MASS_H2 = 2.016e-3 * pyunits.kg / pyunits.mol
MOLAR_MASS_H2O = 18.015e-3 * pyunits.kg / pyunits.mol

THERMONEUTRAL_VOLTAGE = 1.48 * pyunits.V
"""Enthalpy-based cell voltage; a stack above it generates heat, below it absorbs."""

REVERSIBLE_VOLTAGE = 1.23 * pyunits.V
"""Thermodynamic floor on cell voltage; minimum energy required to split water."""

HHV_HYDROGEN = 39.4 * SPECIFIC_ENERGY_UNITS
"""Higher heating value of hydrogen; the floor on specific energy consumption."""

"""The two ends of the electrical and thermal operating windows -- ``cell_voltage_max``
and ``stack_temperature_min``/``stack_temperature_max`` -- are config options rather
than constants here: they are technology choices (an alkaline stack tops out near
2.0 V and tolerates 363 K), not physics. Only ``REVERSIBLE_VOLTAGE`` and
``HHV_HYDROGEN``, which are thermodynamics, stay fixed.
"""


class ElectrolysisDetail(enum.StrEnum):
    """How much of the electrolyzer the energy relationship models."""

    CONSTANT_INTENSITY = "constant_intensity"
    STACK = "stack"


class ThermalModel(enum.StrEnum):
    """How the stack's temperature and waste heat are treated.

    The three are distinct models, not terms that add up, which is why they are
    one enum rather than a set of booleans. Each keeps the model square.

    ``NONE``
        No heat duty at all, and ``stack_temperature`` is a fixed setpoint. The
        right choice unless something downstream uses or rejects the heat.
    ``WASTE_HEAT``
        Adds ``power_thermal[t] = N_cells * I[t] * (V[t] - 1.48 V)`` as a
        registered heat duty; ``stack_temperature`` stays a fixed setpoint, so
        the duty is an output.
    ``HEAT_BALANCE``
        Also adds the steady-state balance
        ``UA * (T[t] - T_amb) = power_thermal[t]`` and **unfixes**
        ``stack_temperature``, so the coolant loop's capacity decides the
        operating temperature, which feeds back into the voltage correlation.
        Solved simultaneously, so the temperature-voltage loop needs no
        fixed-point iteration.
    """

    NONE = "none"
    WASTE_HEAT = "waste_heat"
    HEAT_BALANCE = "heat_balance"


_VOLTAGE_COEFFICIENTS = (
    "voltage_intercept",
    "voltage_temperature_coefficient",
    "tafel_slope",
    "area_specific_resistance",
    "resistance_temperature_coefficient",
)

_STACK_OPTIONS = (
    "n_cells",
    "cell_area",
    "current_density_min",
    "current_density_max",
    "cell_voltage_max",
    "stack_temperature",
    "stack_temperature_min",
    "stack_temperature_max",
    "reference_temperature",
    "faradaic_efficiency",
    "rectifier_efficiency",
    "thermal",
) + _VOLTAGE_COEFFICIENTS


def _is_stack(config) -> bool:
    """Return whether ``config`` selects the equation-oriented stack model."""
    return config.detail is ElectrolysisDetail.STACK


_OPTION_GATES = (
    (
        ("energy_intensity",),
        "detail=constant_intensity",
        lambda c: not _is_stack(c),
    ),
    (_STACK_OPTIONS, "detail=stack", _is_stack),
    (
        ("thermal_conductance", "ambient_temperature"),
        "thermal=heat_balance",
        lambda c: _is_stack(c) and c.thermal is ThermalModel.HEAT_BALANCE,
    ),
)
"""(option names, the condition that makes them live, predicate) triples."""


def _enum_domain(enum_class, field: str):
    """Build a ConfigValue domain coercing to ``enum_class``.

    Args:
        enum_class: The ``StrEnum`` the option's values come from.
        field: The option name, used in the error message.

    Returns:
        A callable domain suitable for ``ConfigValue(domain=...)``.
    """

    def domain(value):
        try:
            return enum_class(value)
        except ValueError as exc:
            allowed = ", ".join(repr(member.value) for member in enum_class)
            raise FlexConfigError(
                f"{field} must be one of {allowed}, got {value!r}.",
                field=field,
                value=value,
            ) from exc

    return domain


def _positive_int_domain(value):
    """ConfigValue domain: a strictly positive cell count."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    raise FlexConfigError(
        f"n_cells must be a positive integer number of cells, got {value!r}.",
        field="n_cells",
        value=value,
    )


def _efficiency_domain(field: str):
    """Build a ConfigValue domain accepting an efficiency fraction in (0, 1].

    Args:
        field: The option name, used in the error message.

    Returns:
        A callable domain suitable for ``ConfigValue(domain=...)``.
    """

    def domain(value):
        if isinstance(value, (int, float)) and 0.0 < value <= 1.0:
            return float(value)
        raise FlexConfigError(
            f"{field} must be a fraction in (0, 1], got {value!r}.",
            field=field,
            value=value,
        )

    return domain


def _as_value(quantity, units) -> float:
    """Return ``quantity`` as a plain float in ``units``.

    Args:
        quantity: A units-carrying Pyomo expression, or a bare number already
            expressed in ``units``.
        units: The Pyomo units to express the result in.

    Returns:
        The value as a float.
    """
    if isinstance(quantity, (int, float)):
        return float(quantity)
    return float(pyo.value(pyunits.convert(quantity, units)))


def _check_option_gates(config) -> None:
    """Reject any user-set option this config's detail or thermal model ignores.

    Args:
        config: The unit's ``ConfigDict``.

    Raises:
        FlexConfigError: If a user-set option would have no effect, naming that
            exact option and the condition that would make it live.
    """
    gated = {
        name: (predicate(config), condition)
        for names, condition, predicate in _OPTION_GATES
        for name in names
    }
    for entry in config.user_values():
        live, condition = gated.get(entry.name(), (True, ""))
        if not live:
            raise FlexConfigError(
                f"{entry.name()!r} only applies with {condition}; it would have "
                "no effect as configured. Enable that, or drop the option.",
                field=entry.name(),
                value=entry.value(),
            )


@declare_process_block_class("ElectrolysisSeparator")
class ElectrolysisSeparatorData(SeparatorData):
    """Water electrolysis as a separation (see the module docstring).

    ``outlet_a`` is the converted water -- the electrolyzed fraction, which
    leaves as product hydrogen and oxygen -- and ``outlet_b`` the unconverted
    feed returning to the water loop, so the inherited ``split_fraction`` is
    the stack's water recovery and the inherited ``energy_intensity``
    (constant-intensity detail only) its specific energy consumption per unit
    of feed.

    Config:
        Inherits the Separator/SIDO/OpsBlock config, re-defaulting
        ``split_fraction`` to 0.6 and ``energy_intensity`` to 3410 kWh/m^3 of
        feed (the stack model's own draw at 1 A/cm^2). Adds ``detail``,
        ``thermal``, and the stack options documented on the CONFIG entries
        below. Every units-carrying option also accepts a bare number, taken to
        be in the units its description names.

        The default coefficients describe a 250-cell, 1000 cm^2 PEM stack
        referenced at 353 K, and reproduce that model's sanity checks at
        1 A/cm^2: 1.78 V per cell, 50.8 kWh/kg, an HHV efficiency of 0.775, a
        waste-heat fraction of 0.169, and dV/dT of -2.7 mV/K.
    """

    CONFIG = SeparatorData.CONFIG()
    CONFIG.get("split_fraction").set_default_value(0.6)
    CONFIG.get("energy_intensity").set_default_value(
        3410.0 * pyunits.kWh / pyunits.m**3
    )
    CONFIG.declare(
        "detail",
        ConfigValue(
            default=ElectrolysisDetail.CONSTANT_INTENSITY,
            domain=_enum_domain(ElectrolysisDetail, "detail"),
            description="How much of the electrolyzer the energy relationship "
            "models: 'constant_intensity' (the default) for one energy "
            "intensity times the feed flow, or 'stack' for the "
            "equation-oriented stack model.",
        ),
    )
    CONFIG.declare(
        "thermal",
        ConfigValue(
            default=ThermalModel.NONE,
            domain=_enum_domain(ThermalModel, "thermal"),
            description="How temperature and waste heat are treated: 'none' "
            "(the default, no heat duty and a fixed temperature setpoint), "
            "'waste_heat' (a registered duty from the thermoneutral-voltage "
            "offset), or 'heat_balance' (also solves the temperature from a "
            "steady-state coolant balance). Stack detail only.",
        ),
    )
    CONFIG.declare(
        "n_cells",
        ConfigValue(
            default=250,
            domain=_positive_int_domain,
            description="Number of cells in series in the stack; the current is "
            "common to all of them. Stack detail only.",
        ),
    )
    CONFIG.declare(
        "cell_area",
        ConfigValue(
            default=1000.0 * pyunits.cm**2,
            description="Active area of one cell, cm^2. Stack detail only.",
        ),
    )
    CONFIG.declare(
        "current_density_min",
        ConfigValue(
            default=0.05 * CURRENT_DENSITY_UNITS,
            description="Lower bound of the operating window, A/cm^2. Must be "
            "strictly positive -- the voltage correlation takes log10 of it. "
            "The physical turndown floor is a safety limit and usually higher: "
            "at low current the hydrogen crossing the membrane stays roughly "
            "constant while oxygen production falls, so the H2-in-O2 fraction "
            "climbs toward its flammability limit. Stack detail only.",
        ),
    )
    CONFIG.declare(
        "current_density_max",
        ConfigValue(
            default=2.5 * CURRENT_DENSITY_UNITS,
            description="Upper (rated) bound of the operating window, A/cm^2. "
            "1-2 A/cm^2 for PEM, 0.2-0.5 A/cm^2 for alkaline. Stack detail "
            "only.",
        ),
    )
    CONFIG.declare(
        "cell_voltage_max",
        ConfigValue(
            default=2.5 * pyunits.V,
            description="Upper bound on cell voltage, V -- a stack operating "
            "above it is degraded, not operating. ~2.5 V for PEM, ~2.0 V for "
            "alkaline. This is the only ceiling on the electrical chain and is "
            "deliberately not duplicated downstream: specific energy "
            "consumption is proportional to cell voltage (~28.6 kWh/kg per volt "
            "at the default efficiencies), so this bound already caps it, and a "
            "second ceiling there could never become active. Must exceed the "
            "1.23 V thermodynamic floor. Stack detail only.",
        ),
    )
    CONFIG.declare(
        "stack_temperature",
        ConfigValue(
            default=353.0 * pyunits.K,
            description="Stack temperature, K. A fixed setpoint under "
            "thermal='none'/'waste_heat'; the initial value of a solved "
            "variable under thermal='heat_balance'. Also the temperature any "
            "heat duty is registered at. Must lie within "
            "[stack_temperature_min, stack_temperature_max]. Stack detail only.",
        ),
    )
    CONFIG.declare(
        "stack_temperature_min",
        ConfigValue(
            default=293.0 * pyunits.K,
            description="Lower bound on stack temperature, K -- a colder stack "
            "is not started. Unlike a ceiling on specific energy consumption "
            "this bound is not implied by any other, because under "
            "thermal='heat_balance' the temperature is solved: an implausible "
            "thermal_conductance then surfaces as an infeasibility here rather "
            "than as a silently frozen stack. Stack detail only.",
        ),
    )
    CONFIG.declare(
        "stack_temperature_max",
        ConfigValue(
            default=358.0 * pyunits.K,
            description="Upper bound on stack temperature, K -- a materials "
            "limit, not a thermodynamic one, so in practice a stack runs as hot "
            "as its warranty allows. 358 K for a PEM membrane, 363 K for "
            "alkaline. Stack detail only.",
        ),
    )
    CONFIG.declare(
        "reference_temperature",
        ConfigValue(
            default=353.0 * pyunits.K,
            description="Temperature the voltage correlation's coefficients "
            "were fitted at, K; the temperature offset in the correlation is "
            "measured from it. Stack detail only.",
        ),
    )
    CONFIG.declare(
        "faradaic_efficiency",
        ConfigValue(
            default=0.97,
            domain=_efficiency_domain("faradaic_efficiency"),
            description="Fraction of the charge passed that reaches the product "
            "as hydrogen, 0.95-0.99; the remainder is crossover loss, which "
            "worsens at low load and high pressure. Stack detail only.",
        ),
    )
    CONFIG.declare(
        "rectifier_efficiency",
        ConfigValue(
            default=0.96,
            domain=_efficiency_domain("rectifier_efficiency"),
            description="Rectifier (AC-DC conversion) efficiency, a fraction in "
            "(0, 1], typically 0.95-0.98: the share of the facility's draw that "
            "reaches the stack as DC. This is the only balance-of-plant item "
            "modeled -- fluid-side auxiliaries (feed pumps, coolant circulation, "
            "product compression) are deliberately out of scope. Fitted. Stack "
            "detail only.",
        ),
    )
    CONFIG.declare(
        "voltage_intercept",
        ConfigValue(
            default=1.58 * pyunits.V,
            description="Voltage correlation intercept, V -- the extrapolated "
            "cell voltage at 1 A/cm^2 with no ohmic loss, at the reference "
            "temperature. Fitted. Stack detail only.",
        ),
    )
    CONFIG.declare(
        "voltage_temperature_coefficient",
        ConfigValue(
            default=-0.0025 * pyunits.V / pyunits.K,
            description="Temperature coefficient of the intercept, V/K; "
            "negative, since a hotter stack needs less voltage. Fitted. Stack "
            "detail only.",
        ),
    )
    CONFIG.declare(
        "tafel_slope",
        ConfigValue(
            default=0.05 * pyunits.V,
            description="Coefficient on log10(current density), V per decade -- "
            "the charge-transfer kinetics, which dominate at low current. "
            "Fitted; estimate it at zero to drop the term. Stack detail only.",
        ),
    )
    CONFIG.declare(
        "area_specific_resistance",
        ConfigValue(
            default=0.20 * RESISTANCE_UNITS,
            description="Coefficient on current density, ohm*cm^2 -- the lumped "
            "ohmic resistance of membrane, plates and contacts, which dominates "
            "at mid and high current. 0.1-0.3 for PEM, ~0.3 for alkaline. "
            "Fitted. Stack detail only.",
        ),
    )
    CONFIG.declare(
        "resistance_temperature_coefficient",
        ConfigValue(
            default=-0.0002 * RESISTANCE_UNITS / pyunits.K,
            description="Temperature coefficient of the resistance, "
            "ohm*cm^2/K; negative, since membrane conductivity rises with "
            "temperature. Fitted; estimate it at zero to drop the term. Stack "
            "detail only.",
        ),
    )
    CONFIG.declare(
        "thermal_conductance",
        ConfigValue(
            default=1364.0 * pyunits.W / pyunits.K,
            description="Overall thermal conductance of the coolant loop, W/K, "
            "in the steady-state balance UA*(T - T_amb) = waste heat. Fitted. "
            "Requires thermal='heat_balance'.",
        ),
    )
    CONFIG.declare(
        "ambient_temperature",
        ConfigValue(
            default=298.0 * pyunits.K,
            description="Ambient (coolant inlet) temperature, K, the heat "
            "balance rejects to. Requires thermal='heat_balance'.",
        ),
    )

    def build(self) -> None:
        """Build the split topology, then the configured energy relationship."""
        # SIDOBlockData.build is called directly rather than through super():
        # it builds the split topology and, as part of the IDAES base build,
        # populates self.config -- which the detail selection below reads.
        # SeparatorData.build is stepped over because the stack model
        # *replaces* its constant-intensity relation rather than adding to it,
        # so that relation is built here only for the constant-intensity detail.
        SIDOBlockData.build(self)
        _check_option_gates(self.config)
        if _is_stack(self.config):
            self._build_stack()
        else:
            self.add_constant_intensity_relation(
                self.flow_in,
                kind=nm.PowerKind.ELECTRICAL,
                intensity=self.config.energy_intensity,
            )

    # -- the equation-oriented stack model ---------------------------------

    def _add_fitted_var(self, name, quantity, units, doc: str, *, regressable=True):
        """Add a scalar Var fixed at ``quantity``, registered as a parameter.

        Args:
            name: The component name to attach the Var under.
            quantity: The value, units-carrying or a bare number in ``units``.
            units: The Var's Pyomo units.
            doc: The Var's ``doc=`` string.
            regressable: Whether FlexParameterize may fit this parameter.

        Returns:
            The created, fixed ``Var``.
        """
        value = _as_value(quantity, units)
        self.add_component(name, pyo.Var(initialize=value, units=units, doc=doc))
        var = self.find_component(name)
        var.fix(value)
        self.register_process_parameter(var, regressable=regressable)
        return var

    def _operating_windows(self) -> tuple[tuple[float, float], ...]:
        """Validate and return the three configured operating windows.

        Returns:
            ``((i_min, i_max), (v_min, v_max), (t_min, t_max))`` -- current
            density in A/cm^2, cell voltage in V, stack temperature in K. The
            cell-voltage floor is the thermodynamic :data:`REVERSIBLE_VOLTAGE`
            and is not configurable; the other five ends are config options.

        Raises:
            FlexConfigError: If a window is not strictly ordered, if the current
                density's floor is not strictly positive, if the cell-voltage
                ceiling does not clear the thermodynamic floor, or if the
                stack-temperature setpoint falls outside its own window. Each
                names the offending option.
        """
        cfg = self.config
        current = (
            _as_value(cfg.current_density_min, CURRENT_DENSITY_UNITS),
            _as_value(cfg.current_density_max, CURRENT_DENSITY_UNITS),
        )
        if current[0] <= 0.0 or current[0] >= current[1]:
            raise FlexConfigError(
                "current_density_min must be strictly positive and below "
                f"current_density_max ({current[1]} A/cm^2), got {current[0]} "
                "A/cm^2. The voltage correlation takes log10 of the current "
                "density, so zero is not an admissible turndown floor.",
                field="current_density_min",
                value=current[0],
            )

        voltage = (
            pyo.value(REVERSIBLE_VOLTAGE),
            _as_value(cfg.cell_voltage_max, pyunits.V),
        )
        if voltage[1] <= voltage[0]:
            raise FlexConfigError(
                f"cell_voltage_max must exceed the {voltage[0]} V thermodynamic "
                f"floor for splitting water, got {voltage[1]} V. Try ~2.5 V for "
                "PEM or ~2.0 V for alkaline.",
                field="cell_voltage_max",
                value=voltage[1],
            )

        temperature = (
            _as_value(cfg.stack_temperature_min, pyunits.K),
            _as_value(cfg.stack_temperature_max, pyunits.K),
        )
        if temperature[0] >= temperature[1]:
            raise FlexConfigError(
                "stack_temperature_min must be below stack_temperature_max "
                f"({temperature[1]} K), got {temperature[0]} K.",
                field="stack_temperature_min",
                value=temperature[0],
            )
        setpoint = _as_value(cfg.stack_temperature, pyunits.K)
        if not temperature[0] <= setpoint <= temperature[1]:
            raise FlexConfigError(
                f"stack_temperature ({setpoint} K) must lie within "
                f"[stack_temperature_min, stack_temperature_max] = "
                f"[{temperature[0]}, {temperature[1]}] K. A setpoint outside "
                "its own bounds fixes the variable at an infeasible value, "
                "which a solver reports as an unexplained infeasibility.",
                field="stack_temperature",
                value=setpoint,
            )
        return current, voltage, temperature

    def _build_stack(self) -> None:
        """Build the stack's variable set and residuals (module docstring)."""
        cfg = self.config
        tb = self._find_time_block()
        current_window, voltage_window, temperature_window = self._operating_windows()
        lower, upper = current_window
        charge_per_mole = ELECTRONS_PER_H2 * FARADAY
        setpoint = _as_value(cfg.stack_temperature, pyunits.K)

        self.n_cells = pyo.Param(
            initialize=cfg.n_cells,
            mutable=True,
            units=pyunits.dimensionless,
            doc="Number of cells in series; the current is common to all.",
        )
        self.cell_area = pyo.Param(
            initialize=_as_value(cfg.cell_area, pyunits.cm**2),
            mutable=True,
            units=pyunits.cm**2,
            doc="Active area of one cell.",
        )
        self.reference_temperature = pyo.Param(
            initialize=_as_value(cfg.reference_temperature, pyunits.K),
            mutable=True,
            units=pyunits.K,
            doc="Temperature the voltage correlation was fitted at; the "
            "correlation's temperature offset is measured from it.",
        )
        for param in (self.n_cells, self.cell_area, self.reference_temperature):
            self.register_process_parameter(param, regressable=False)

        # The five fitted coefficients: this is the whole electrochemistry, and
        # the only thing a coarser or finer model changes.
        intercept = self._add_fitted_var(
            "voltage_intercept",
            cfg.voltage_intercept,
            pyunits.V,
            "Voltage correlation intercept: cell voltage at 1 A/cm^2 with no "
            "ohmic loss, at the reference temperature.",
        )
        intercept_slope = self._add_fitted_var(
            "voltage_temperature_coefficient",
            cfg.voltage_temperature_coefficient,
            pyunits.V / pyunits.K,
            "Temperature coefficient of the voltage correlation's intercept.",
        )
        tafel_slope = self._add_fitted_var(
            "tafel_slope",
            cfg.tafel_slope,
            pyunits.V,
            "Coefficient on log10(current density): the charge-transfer "
            "kinetics. Estimate it at zero to drop the term.",
        )
        resistance = self._add_fitted_var(
            "area_specific_resistance",
            cfg.area_specific_resistance,
            RESISTANCE_UNITS,
            "Coefficient on current density: the lumped ohmic resistance of "
            "membrane, plates and contacts.",
        )
        resistance_slope = self._add_fitted_var(
            "resistance_temperature_coefficient",
            cfg.resistance_temperature_coefficient,
            RESISTANCE_UNITS / pyunits.K,
            "Temperature coefficient of the area-specific resistance. "
            "Estimate it at zero to drop the term.",
        )
        efficiency = self._add_fitted_var(
            "faradaic_efficiency",
            cfg.faradaic_efficiency,
            pyunits.dimensionless,
            "Fraction of the charge passed that reaches the product as "
            "hydrogen; the remainder is crossover loss.",
        )
        rectifier_efficiency = self._add_fitted_var(
            "rectifier_efficiency",
            cfg.rectifier_efficiency,
            pyunits.dimensionless,
            "Rectifier (AC-DC conversion) efficiency: the fraction of the "
            "facility draw that reaches the stack as DC.",
        )

        # Initialize on the interior at 1 A/cm^2 (clamped into the window):
        # Newton methods on this system fail almost exclusively from a current
        # density at or below zero, which leaves log10 undefined.
        nominal_density = min(max(1.0, lower), upper)
        nominal_current = nominal_density * pyo.value(self.cell_area)
        nominal_hydrogen = (
            efficiency.value
            * nominal_current
            * cfg.n_cells
            / pyo.value(charge_per_mole)
            * pyo.value(MOLAR_MASS_H2)
            * 3600.0
        )

        self.current_density = pyo.Var(
            tb.time_index,
            bounds=(lower, upper),
            initialize=nominal_density,
            units=CURRENT_DENSITY_UNITS,
            doc="Current density -- the throughput dial, set by the rectifier. "
            "Bounded strictly above zero so log10 stays differentiable.",
        )
        self.stack_current = pyo.Var(
            tb.time_index,
            bounds=(0.0, None),
            initialize=nominal_current,
            units=pyunits.ampere,
            doc="Stack current, common to every cell in series.",
        )
        self.cell_voltage = pyo.Var(
            tb.time_index,
            bounds=voltage_window,
            initialize=1.75,
            units=pyunits.V,
            doc="Cell voltage from the fitted correlation. Bounded below by the "
            "thermodynamic floor, so a solve reporting over 100% efficiency is "
            "infeasible rather than plausible, and above by cell_voltage_max.",
        )
        self.stack_temperature = pyo.Var(
            tb.time_index,
            bounds=temperature_window,
            initialize=setpoint,
            units=pyunits.K,
            doc="Stack temperature. Fixed at the configured setpoint unless "
            "thermal='heat_balance' solves it; bounded by the configured "
            "cold-start floor and materials limit.",
        )
        self.hydrogen_production = pyo.Var(
            tb.time_index,
            bounds=(0.0, None),
            initialize=nominal_hydrogen,
            units=pyunits.kg / pyunits.hr,
            doc="Hydrogen delivered to the product.",
        )
        # stack_voltage and power_stack are derived quantities, but they are
        # Vars with defining residuals rather than Expressions, so the whole
        # system is one flat set of variables and equations. No bounds: they
        # follow from cell_voltage's, and n_cells is a mutable Param -- bounds
        # computed from it here would go stale the moment it is updated.
        self.stack_voltage = pyo.Var(
            tb.time_index,
            bounds=(0.0, None),
            initialize=1.75 * cfg.n_cells,
            units=pyunits.V,
            doc="Stack voltage: n_cells cells in series at the cell voltage.",
        )
        self.power_stack = pyo.Var(
            tb.time_index,
            bounds=(0.0, None),
            initialize=nominal_current * cfg.n_cells * 1.75 / 1000.0,
            units=pyunits.kW,
            doc="Stack electrical draw: n_cells * cell_voltage * stack_current.",
        )
        self.specific_energy_consumption = pyo.Var(
            tb.time_index,
            bounds=(pyo.value(HHV_HYDROGEN), None),
            initialize=51.0,
            units=SPECIFIC_ENERGY_UNITS,
            doc="Total electrical draw per unit of hydrogen delivered. Bounded "
            "below by the higher-heating-value floor -- which binds before "
            "cell_voltage's own floor does -- and unbounded above, since "
            "cell_voltage_max already caps it.",
        )

        power = self.declare_power(nm.PowerKind.ELECTRICAL)
        self.register_io_variable(power, role="output")
        self.register_io_variable(self.current_density, role="output")
        self.register_io_variable(self.hydrogen_production, role="output")
        # The temperature is a specification in two of the three thermal models
        # and a solved state in the third, so its role follows the model.
        self.register_io_variable(
            self.stack_temperature,
            role="output" if cfg.thermal is ThermalModel.HEAT_BALANCE else "input",
        )

        @self.Constraint(
            tb.time_index,
            doc="Faraday's law over the split: the water converted through "
            "outlet_a is the charge passed, discounted by the faradaic "
            "efficiency. The separator's split_fraction is therefore the "
            "stack's water recovery.",
        )
        def faraday_relation(b, t):
            return pyunits.convert(
                b._converted_molar_rate(t), pyunits.mol / pyunits.s
            ) == pyunits.convert(
                b.faradaic_efficiency
                * b.stack_current[t]
                * b.n_cells
                / charge_per_mole,
                pyunits.mol / pyunits.s,
            )

        @self.Constraint(
            tb.time_index,
            doc="Hydrogen output on a mass basis.",
        )
        def hydrogen_production_relation(b, t):
            return b.hydrogen_production[t] == pyunits.convert(
                b._converted_molar_rate(t) * MOLAR_MASS_H2, pyunits.kg / pyunits.hr
            )

        @self.Constraint(
            tb.time_index,
            doc="Current density definition: stack_current == "
            "current_density * cell_area.",
        )
        def current_density_definition(b, t):
            return b.stack_current[t] == pyunits.convert(
                b.current_density[t] * b.cell_area, pyunits.ampere
            )

        @self.Constraint(
            tb.time_index,
            doc="The fitted voltage correlation -- the whole electrochemistry. "
            "Lower fidelity is the same equation with coefficients estimated at "
            "zero, never a different equation.",
        )
        def cell_voltage_relation(b, t):
            offset = b.stack_temperature[t] - b.reference_temperature
            return b.cell_voltage[t] == pyunits.convert(
                intercept
                + intercept_slope * offset
                + tafel_slope * pyo.log10(b.current_density[t] / CURRENT_DENSITY_UNITS)
                + (resistance + resistance_slope * offset) * b.current_density[t],
                pyunits.V,
            )

        @self.Constraint(
            tb.time_index,
            doc="Stack voltage definition: n_cells cells in series. Linear.",
        )
        def stack_voltage_definition(b, t):
            return b.stack_voltage[t] == pyunits.convert(
                b.n_cells * b.cell_voltage[t], pyunits.V
            )

        @self.Constraint(
            tb.time_index,
            doc="Stack draw: the stack voltage at the common stack current.",
        )
        def power_stack_relation(b, t):
            return b.power_stack[t] == pyunits.convert(
                b.stack_voltage[t] * b.stack_current[t], pyunits.kW
            )

        @self.Constraint(
            tb.time_index,
            doc="power_electrical_relation: the facility draw is the stack's DC "
            "draw grossed up by the rectifier's conversion loss, written as a "
            "product so it stays well posed if the efficiency is unfixed for "
            "regression. This is the swap contract FlexParameterize deactivates "
            "when it fits a data-driven relationship in place of the stack "
            "model.",
        )
        def power_electrical_relation(b, t):
            return b.power_electrical[t] * rectifier_efficiency == b.power_stack[t]

        @self.Constraint(
            tb.time_index,
            doc="Specific energy consumption, written as a product so it stays "
            "well posed at zero throughput: SEC * hydrogen == power.",
        )
        def specific_energy_relation(b, t):
            return (
                pyunits.convert(
                    b.specific_energy_consumption[t] * b.hydrogen_production[t],
                    pyunits.kW,
                )
                == b.power_electrical[t]
            )

        if cfg.thermal is ThermalModel.HEAT_BALANCE:
            self._build_heat_balance(tb, setpoint)
        elif cfg.thermal is ThermalModel.WASTE_HEAT:
            self._build_waste_heat(tb, setpoint)
        if cfg.thermal is not ThermalModel.HEAT_BALANCE:
            for t in tb.time_index:
                self.stack_temperature[t].fix(setpoint)

        surrogate = getattr(self.config.flexops_config, "surrogate", None)
        if surrogate is not None and surrogate.functional_form != "constant_intensity":
            self.swap_energy_relation(surrogate, kind=nm.PowerKind.ELECTRICAL)

    def _converted_molar_rate(self, t):
        """Return the molar rate of water converted through ``outlet_a`` at ``t``.

        Args:
            t: The time index.

        Returns:
            A units-carrying Pyomo expression for the molar rate.
        """
        return self.flow_out_a[t] * self.outlet_a_state.dens_mass[t] / MOLAR_MASS_H2O

    def _build_waste_heat(self, tb, setpoint: float) -> None:
        """Declare ``power_thermal`` from the thermoneutral-voltage offset.

        Args:
            tb: The model's ``TimeBlockData``.
            setpoint: The configured stack temperature in K, which the duty is
                registered at.
        """
        duty = self.declare_power(
            nm.PowerKind.THERMAL, temperature=setpoint * pyunits.K
        )
        self.register_io_variable(duty, role="output")

        @self.Constraint(
            tb.time_index,
            doc="Waste heat: n_cells * stack_current * (cell_voltage - 1.48 V). "
            "A stack above the thermoneutral voltage generates heat and needs "
            "it rejected; below it, the duty goes negative and the stack "
            "absorbs heat.",
        )
        def waste_heat_relation(b, t):
            overvoltage = b.cell_voltage[t] - THERMONEUTRAL_VOLTAGE
            return b.power_thermal[t] == pyunits.convert(
                b.n_cells * b.stack_current[t] * overvoltage, pyunits.kW
            )

    def _build_heat_balance(self, tb, setpoint: float) -> None:
        """Build the waste heat plus the steady-state balance that solves ``T``.

        Args:
            tb: The model's ``TimeBlockData``.
            setpoint: The configured stack temperature in K, used as the initial
                value and as the duty's registered temperature.
        """
        self._build_waste_heat(tb, setpoint)
        conductance = self._add_fitted_var(
            "thermal_conductance",
            self.config.thermal_conductance,
            pyunits.W / pyunits.K,
            "Overall thermal conductance of the coolant loop.",
        )
        ambient = self._add_fitted_var(
            "ambient_temperature",
            self.config.ambient_temperature,
            pyunits.K,
            "Ambient (coolant inlet) temperature the heat balance rejects to.",
            regressable=False,
        )

        @self.Constraint(
            tb.time_index,
            doc="Steady-state coolant balance: UA * (T - T_amb) == waste heat. "
            "Solved simultaneously with the voltage correlation, so the "
            "temperature-voltage loop needs no fixed-point iteration.",
        )
        def thermal_balance(b, t):
            return (
                pyunits.convert(
                    conductance * (b.stack_temperature[t] - ambient), pyunits.kW
                )
                == b.power_thermal[t]
            )
