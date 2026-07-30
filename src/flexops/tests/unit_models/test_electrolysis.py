"""ElectrolysisSeparator: constant-intensity default and the fitted stack model.

The stack model is equation-oriented -- a fixed variable and equation set with no
assumed causal direction -- and its fidelity is raised by *estimating* more of the
voltage correlation's coefficients, never by adding equations. The reference
values asserted here are its own sanity checks at 1 A/cm^2 and 353 K: 1.70-1.80 V
per cell, 50-55 kWh/kg, an HHV efficiency of 0.70-0.78, and a waste-heat
fraction ``Q_gen / P_stack`` of 0.13-0.18.
"""

import pyomo.environ as pyo
import pytest
from pyomo.environ import units as pyunits

from flexcore import nomenclature as nm
from flexcore.exceptions import FlexConfigError
from flexops.testing import UnitModelTestHarness, dummy_time_block
from flexops.unit_models import ElectrolysisSeparator
from flexops.unit_models.electrolysis import ElectrolysisDetail, ThermalModel

# Feed flow (m^3/hr) that lands the default 250-cell, 1000 cm^2 stack at
# ~1 A/cm^2 through the default 0.6 split.
NOMINAL_FLOW = 0.1358

# Hand calculations at i = 1 A/cm^2 and the reference temperature (so dT = 0),
# with the shipped coefficients 1.58 V, 0.05 V/decade and 0.20 ohm*cm^2.
CELL_VOLTAGE = 1.58 + 0.20  # 1.78 V; the log10 term vanishes at i = 1
STACK_VOLTAGE = 250 * CELL_VOLTAGE  # 445 V
STACK_POWER = 445.0  # 250 cells * 1.78 V * 1000 A
POWER_ELECTRICAL = 463.5416667  # the stack draw over a 0.96 rectifier efficiency
HYDROGEN = 9.12039167  # kg/hr, 0.97 faradaic efficiency
SPECIFIC_ENERGY = 50.8247544  # kWh/kg
HHV_EFFICIENCY = 0.7752127967  # 39.4 / SPECIFIC_ENERGY
WASTE_HEAT = 75.0  # kW, 250 * 1000 A * (1.78 - 1.48) V


def stack_unit(**options):
    """Build a 3-point model carrying a stack-mode electrolyzer.

    Args:
        **options: Construction options forwarded to ``ElectrolysisSeparator``.

    Returns:
        The ``(model, unit)`` pair, with the feed flow set to ``NOMINAL_FLOW``.
    """
    m = dummy_time_block(3)
    m.unit = ElectrolysisSeparator(
        property_package=m.properties,
        detail=ElectrolysisDetail.STACK,
        **options,
    )
    for t in m.time_block.time_index:
        m.unit.flow_in[t].set_value(NOMINAL_FLOW)
    return m, m.unit


def at_operating_point(unit, current_density: float = 1.0) -> None:
    """Set the voltage correlation's inputs so it can be evaluated directly.

    Args:
        unit: The stack-mode electrolyzer.
        current_density: The current density to set, in A/cm^2.
    """
    for t in unit.current_density:
        unit.current_density[t].set_value(current_density)


def is_nonlinear(constraint) -> bool:
    """Report whether a ConstraintData's body is nonlinear in its free variables.

    Args:
        constraint: The ``ConstraintData`` to inspect.

    Returns:
        ``True`` if the body is non-polynomial or of degree above one. Fixed
        ``Var``\\ s count as constants, so a fitted coefficient multiplying a
        free variable stays linear.
    """
    degree = constraint.body.polynomial_degree()
    return degree is None or degree > 1


def residual_is_satisfied(constraint) -> bool:
    """Report whether an equality ConstraintData holds at the current values.

    Args:
        constraint: The ``ConstraintData`` to evaluate.

    Returns:
        ``True`` if body and right-hand side agree to 1e-6.
    """
    return pyo.value(constraint.body) == pytest.approx(
        pyo.value(constraint.upper), abs=1e-6
    )


# -- the default: constant electrical intensity, nothing else ---------------


class TestElectrolysisSeparatorDefault(UnitModelTestHarness):
    """The default electrolyzer is a constant electrical intensity separator."""

    expected_dof = 0
    # 3410 kWh/m^3 of feed times the state block's 1 m^3/hr initial flow.
    expected_solution = {"power_electrical[0]": 3410.0}

    def configure(self):
        m = dummy_time_block(3)
        m.unit = ElectrolysisSeparator(property_package=m.properties)
        return m, m.unit

    @pytest.mark.unit
    def test_default_detail_is_constant_intensity(self):
        """The default ``detail`` is the constant-intensity form."""
        _, unit = self.configure()
        assert unit.config.detail is ElectrolysisDetail.CONSTANT_INTENSITY

    @pytest.mark.unit
    def test_default_registers_electrical_power_only(self):
        """No thermal duty by default: electricity is the only registered power."""
        _, unit = self.configure()
        kinds = {record.kind for record in unit._io_registry.power}
        assert kinds == {nm.PowerKind.ELECTRICAL}
        assert not hasattr(unit, nm.POWER_THERMAL)

    @pytest.mark.unit
    def test_default_builds_the_energy_intensity_swap_contract(self):
        """``energy_intensity`` and ``power_electrical_relation`` are present."""
        _, unit = self.configure()
        assert unit.energy_intensity.fixed
        assert unit.find_component("power_electrical_relation") is not None

    @pytest.mark.unit
    def test_default_builds_no_stack_components(self):
        """None of the stack components exist in constant-intensity mode."""
        _, unit = self.configure()
        for name in (
            "stack_current",
            "current_density",
            "cell_voltage",
            "stack_voltage",
            "stack_temperature",
            "hydrogen_production",
            "voltage_intercept",
            "rectifier_efficiency",
        ):
            assert unit.find_component(name) is None


@pytest.mark.unit
def test_thermal_intensity_is_not_a_config_option():
    """The made-up ``thermal_intensity`` duty is gone from the config."""
    m = dummy_time_block(3)
    m.unit = ElectrolysisSeparator(property_package=m.properties)
    assert "thermal_intensity" not in m.unit.config
    assert "thermal_temperature" not in m.unit.config


@pytest.mark.unit
def test_first_principles_polarization_api_is_gone():
    """The detailed, heavily-parameterized polarization model was removed."""
    import flexops
    from flexops.unit_models import electrolysis

    for name in ("ActivationModel", "OhmicModel"):
        assert not hasattr(electrolysis, name)
        assert not hasattr(flexops, name)
    assert not hasattr(ElectrolysisDetail, "POLARIZATION")

    _, unit = stack_unit()
    for name in (
        "exchange_current_density",
        "membrane_thickness",
        "membrane_hydration",
        "contact_resistance",
        "limiting_current_density",
        "cathode_pressure",
        "reversible_voltage",
        "activation_overpotential",
        "concentration_overpotential",
    ):
        assert name not in unit.config, f"{name} survives in the config"
        assert unit.find_component(name) is None, f"{name} survives on the model"


@pytest.mark.unit
def test_fluid_side_balance_of_plant_is_gone():
    """Pumps, chiller and compressor are not modeled; only the rectifier is."""
    _, unit = stack_unit()
    for name in ("bop_power_fraction", "power_balance_of_plant"):
        assert name not in unit.config, f"{name} survives in the config"
        assert unit.find_component(name) is None, f"{name} survives on the model"
    assert unit.find_component("power_balance_of_plant_relation") is None
    assert unit.rectifier_efficiency.fixed


# -- the fitted stack model -------------------------------------------------


class TestElectrolysisSeparatorStack(UnitModelTestHarness):
    """The equation-oriented stack: one fitted voltage correlation, no heat duty."""

    expected_dof = 0
    solution_rtol = 1e-4
    expected_solution = {
        "current_density[0]": 0.9997554778,
        "cell_voltage[0]": 1.779945785,
        "stack_voltage[0]": 444.9864463,
        "stack_current[0]": 999.7554778,
        "hydrogen_production[0]": 9.118161532,
        "power_stack[0]": 444.8776373,
        "power_electrical[0]": 463.4142055,
        "specific_energy_consumption[0]": 50.82320639,
    }

    def configure(self):
        return stack_unit()

    @pytest.mark.unit
    def test_builds_the_equation_oriented_variable_set(self):
        """Every quantity in the system is a Var, and every one is time-indexed."""
        _, unit = self.configure()
        for name in (
            "current_density",
            "stack_current",
            "cell_voltage",
            "stack_voltage",
            "stack_temperature",
            "hydrogen_production",
            "power_stack",
            "power_electrical",
            "specific_energy_consumption",
        ):
            component = unit.find_component(name)
            assert component is not None, f"missing {name}"
            assert component.ctype is pyo.Var, f"{name} is not a Var"
            assert component.is_indexed(), f"{name} is not time-indexed"

    @pytest.mark.unit
    def test_carries_no_expressions(self):
        """Nothing is an ``Expression``: every quantity is a Var plus a residual."""
        _, unit = self.configure()
        assert list(unit.component_objects(pyo.Expression, descend_into=True)) == []

    @pytest.mark.unit
    def test_builds_the_equation_oriented_residual_set(self):
        """Every residual in the system exists, and no thermal one by default."""
        for name in (
            "faraday_relation",
            "hydrogen_production_relation",
            "current_density_definition",
            "stack_voltage_definition",
            "cell_voltage_relation",
            "power_stack_relation",
            "power_electrical_relation",
            "specific_energy_relation",
        ):
            _, unit = self.configure()
            assert unit.find_component(name) is not None, f"missing {name}"
        _, unit = self.configure()
        assert unit.find_component("waste_heat_relation") is None
        assert unit.find_component("thermal_balance") is None

    @pytest.mark.unit
    def test_keeps_the_power_electrical_relation_swap_contract(self):
        """The total draw is still ``power_electrical_relation``, not an intensity."""
        _, unit = self.configure()
        assert unit.find_component("power_electrical_relation") is not None
        assert unit.find_component("energy_intensity") is None

    @pytest.mark.unit
    def test_every_correlation_coefficient_is_a_regressable_parameter(self):
        """Fidelity comes from parameter estimation, so all coefficients are fitted."""
        _, unit = self.configure()
        regressable = {
            record.name for record in unit._io_registry.parameters if record.regressable
        }
        assert {
            "voltage_intercept",
            "voltage_temperature_coefficient",
            "tafel_slope",
            "area_specific_resistance",
            "resistance_temperature_coefficient",
            "faradaic_efficiency",
            "rectifier_efficiency",
        } <= regressable
        for name in ("voltage_intercept", "tafel_slope", "area_specific_resistance"):
            assert unit.find_component(name).fixed

    @pytest.mark.unit
    def test_carries_the_physically_meaningful_bounds(self):
        """The bounds that localize a bad solve, not just solver hygiene."""
        _, unit = self.configure()
        # Strictly positive: log10(i) must stay defined and differentiable.
        assert unit.current_density[0].lb == pytest.approx(0.05)
        assert unit.current_density[0].lb > 0.0
        assert unit.current_density[0].ub == pytest.approx(2.5)
        # The thermodynamic floor; below it the solve reports >100% efficiency.
        assert unit.cell_voltage[0].lb == pytest.approx(1.23)
        # The HHV floor for hydrogen.
        assert unit.specific_energy_consumption[0].lb == pytest.approx(39.4)
        # The PEM membrane limit.
        assert unit.stack_temperature[0].ub == pytest.approx(358.0)

    @pytest.mark.unit
    def test_voltage_correlation_matches_the_pem_sanity_check(self):
        """1.70-1.80 V at 1 A/cm^2 and the reference temperature."""
        _, unit = self.configure()
        at_operating_point(unit, 1.0)
        unit.cell_voltage[0].set_value(CELL_VOLTAGE)
        assert residual_is_satisfied(unit.cell_voltage_relation[0])
        assert 1.70 <= CELL_VOLTAGE <= 1.80

    @pytest.mark.unit
    def test_voltage_rises_with_current_density_and_falls_with_temperature(self):
        """dV/di > 0 (unique solution) and dV/dT < 0 (~ -2.5 mV/K)."""
        _, unit = self.configure()
        rise = pyo.value(
            unit.tafel_slope * pyo.log10(2.0) + unit.area_specific_resistance * 1.0
        )
        assert rise > 0.0
        slope = pyo.value(
            unit.voltage_temperature_coefficient
            + unit.resistance_temperature_coefficient * 1.0
        )
        assert slope == pytest.approx(-0.0027, rel=1e-6)
        assert -0.0035 < slope < -0.0015

    @pytest.mark.unit
    def test_zeroing_coefficients_degenerates_to_the_coarsest_fit(self):
        """Lower fidelity is fewer estimated coefficients, not fewer equations."""
        _, unit = self.configure()
        residuals = len(list(unit.component_data_objects(pyo.Constraint, active=True)))
        unit.update_parameters(
            {
                "voltage_temperature_coefficient": 0.0,
                "tafel_slope": 0.0,
                "resistance_temperature_coefficient": 0.0,
            }
        )
        assert (
            len(list(unit.component_data_objects(pyo.Constraint, active=True)))
            == residuals
        )
        at_operating_point(unit, 2.0)
        unit.cell_voltage[0].set_value(1.58 + 0.20 * 2.0)
        assert residual_is_satisfied(unit.cell_voltage_relation[0])

    @pytest.mark.unit
    def test_stack_voltage_is_a_linear_definition(self):
        """``stack_voltage`` is n_cells cells in series -- a linear residual."""
        _, unit = self.configure()
        assert is_nonlinear(unit.stack_voltage_definition[0]) is False
        unit.cell_voltage[0].set_value(CELL_VOLTAGE)
        unit.stack_voltage[0].set_value(STACK_VOLTAGE)
        assert residual_is_satisfied(unit.stack_voltage_definition[0])
        assert STACK_VOLTAGE == pytest.approx(445.0)

    @pytest.mark.unit
    def test_total_power_is_the_stack_draw_over_the_rectifier_efficiency(self):
        """The facility pays the rectifier's conversion loss on top of the stack."""
        _, unit = self.configure()
        assert pyo.value(unit.rectifier_efficiency) == pytest.approx(0.96)
        unit.power_stack[0].set_value(STACK_POWER)
        unit.power_electrical[0].set_value(POWER_ELECTRICAL)
        assert residual_is_satisfied(unit.power_electrical_relation[0])
        assert POWER_ELECTRICAL > STACK_POWER
        assert is_nonlinear(unit.power_electrical_relation[0]) is False

    @pytest.mark.unit
    def test_specific_energy_is_realistic(self):
        """50-55 kWh/kg, which is an HHV efficiency of 0.70-0.78."""
        _, unit = self.configure()
        unit.power_electrical[0].set_value(POWER_ELECTRICAL)
        unit.hydrogen_production[0].set_value(HYDROGEN)
        unit.specific_energy_consumption[0].set_value(SPECIFIC_ENERGY)
        assert residual_is_satisfied(unit.specific_energy_relation[0])
        assert 50.0 <= SPECIFIC_ENERGY <= 55.0
        assert 39.4 / SPECIFIC_ENERGY == pytest.approx(HHV_EFFICIENCY, rel=1e-6)
        assert 0.70 <= HHV_EFFICIENCY <= 0.78

    @pytest.mark.unit
    def test_faraday_relation_ties_the_split_to_the_stack_charge(self):
        """The converted water and the charge passed balance at 1 A/cm^2."""
        _, unit = self.configure()
        moles_per_second = 0.97 * 250 * 1000.0 / (2 * 96485.33)
        unit.flow_out_a[0].set_value(moles_per_second * 18.015e-3 / 1000.0 * 3600.0)
        unit.stack_current[0].set_value(1000.0)
        unit.hydrogen_production[0].set_value(HYDROGEN)
        assert residual_is_satisfied(unit.faraday_relation[0])
        assert residual_is_satisfied(unit.hydrogen_production_relation[0])


@pytest.mark.unit
def test_nonlinearity_is_confined_to_three_residuals():
    """The log10 term and two bilinear products; everything else is linear."""
    _, unit = stack_unit()
    nonlinear = {
        data.parent_component().local_name
        for data in unit.component_data_objects(pyo.Constraint, active=True)
        if is_nonlinear(data)
    }
    assert nonlinear == {
        "cell_voltage_relation",
        "power_stack_relation",
        "specific_energy_relation",
    }


# -- the thermal block, one enum ---------------------------------------------


@pytest.mark.unit
def test_thermal_none_leaves_the_temperature_a_fixed_setpoint():
    """The default builds no duty and treats the temperature as a specification."""
    _, unit = stack_unit()
    assert unit.config.thermal is ThermalModel.NONE
    kinds = {record.kind for record in unit._io_registry.power}
    assert kinds == {nm.PowerKind.ELECTRICAL}
    assert not hasattr(unit, nm.POWER_THERMAL)
    assert all(unit.stack_temperature[t].fixed for t in unit.stack_temperature)
    assert pyo.value(unit.stack_temperature[0]) == pytest.approx(353.0)


@pytest.mark.unit
def test_thermal_waste_heat_registers_the_duty_at_the_stack_temperature():
    """``power_thermal`` is I*N*(V - 1.48 V), still with a temperature setpoint."""
    _, unit = stack_unit(thermal=ThermalModel.WASTE_HEAT)
    kinds = {record.kind for record in unit._io_registry.power}
    assert kinds == {nm.PowerKind.ELECTRICAL, nm.PowerKind.THERMAL}
    duty = next(
        record
        for record in unit._io_registry.power
        if record.kind is nm.PowerKind.THERMAL
    )
    assert pyo.value(duty.temperature) == pytest.approx(353.0)
    assert all(unit.stack_temperature[t].fixed for t in unit.stack_temperature)
    assert unit.find_component("thermal_balance") is None

    at_operating_point(unit, 1.0)
    unit.stack_current[0].set_value(1000.0)
    unit.cell_voltage[0].set_value(CELL_VOLTAGE)
    unit.power_thermal[0].set_value(WASTE_HEAT)
    assert residual_is_satisfied(unit.waste_heat_relation[0])
    assert WASTE_HEAT / STACK_POWER == pytest.approx(0.1685, rel=1e-3)
    assert 0.13 <= WASTE_HEAT / STACK_POWER <= 0.18


@pytest.mark.unit
def test_thermal_heat_balance_frees_the_temperature():
    """The steady-state balance closes the temperature instead of specifying it."""
    _, unit = stack_unit(thermal=ThermalModel.HEAT_BALANCE)
    assert unit.find_component("waste_heat_relation") is not None
    assert unit.find_component("thermal_balance") is not None
    assert not any(unit.stack_temperature[t].fixed for t in unit.stack_temperature)
    assert unit.thermal_conductance.fixed
    assert pyo.value(unit.ambient_temperature) == pytest.approx(298.0)
    assert is_nonlinear(unit.thermal_balance[0]) is False


class TestElectrolysisSeparatorHeatBalance(UnitModelTestHarness):
    """The stack with its temperature solved from a steady-state heat balance."""

    expected_dof = 0
    solution_rtol = 1e-4
    # The coolant loop's capacity settles the stack just below its 353 K design
    # point: dT = (N*I*(V(0) - V_tn) - UA*(T_ref - T_amb)) / (UA - N*I*dV/dT),
    # which is -0.02545 K for the shipped conductance.
    expected_solution = {
        "current_density[0]": 0.9997554778,
        "stack_temperature[0]": 352.9745493,
        "cell_voltage[0]": 1.780014501,
        "power_electrical[0]": 463.4321054,
        "power_thermal[0]": 74.98528519,
    }

    def configure(self):
        return stack_unit(thermal=ThermalModel.HEAT_BALANCE)


# -- config gating -----------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "option",
    [
        "n_cells",
        "cell_area",
        "voltage_intercept",
        "tafel_slope",
        "rectifier_efficiency",
        "cell_voltage_max",
        "stack_temperature_min",
        "thermal",
    ],
)
def test_constant_intensity_rejects_stack_options(option):
    """A stack option under the default detail is a loud config error."""
    values = {
        "n_cells": 100,
        "cell_area": 500 * pyunits.cm**2,
        "voltage_intercept": 1.6 * pyunits.V,
        "tafel_slope": 0.06 * pyunits.V,
        "rectifier_efficiency": 0.94,
        "cell_voltage_max": 2.0 * pyunits.V,
        "stack_temperature_min": 300.0 * pyunits.K,
        "thermal": ThermalModel.WASTE_HEAT,
    }
    m = dummy_time_block(3)
    with pytest.raises(FlexConfigError) as excinfo:
        m.unit = ElectrolysisSeparator(
            property_package=m.properties, **{option: values[option]}
        )
    assert excinfo.value.field == option


@pytest.mark.unit
def test_stack_rejects_energy_intensity():
    """``energy_intensity`` has no meaning once the stack model builds the power."""
    with pytest.raises(FlexConfigError) as excinfo:
        stack_unit(energy_intensity=40 * pyunits.kWh / pyunits.m**3)
    assert excinfo.value.field == "energy_intensity"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("options", "field"),
    [
        ({"thermal_conductance": 900.0}, "thermal_conductance"),
        ({"ambient_temperature": 290.0}, "ambient_temperature"),
        (
            {"thermal": ThermalModel.WASTE_HEAT, "thermal_conductance": 900.0},
            "thermal_conductance",
        ),
    ],
)
def test_heat_balance_options_need_the_heat_balance(options, field):
    """The conductance and ambient temperature only apply to the heat balance."""
    with pytest.raises(FlexConfigError) as excinfo:
        stack_unit(**options)
    assert excinfo.value.field == field


@pytest.mark.unit
def test_current_density_window_must_be_ordered_and_positive():
    """A non-positive or inverted operating window is rejected by name."""
    with pytest.raises(FlexConfigError) as excinfo:
        stack_unit(current_density_min=0.0)
    assert excinfo.value.field == "current_density_min"
    with pytest.raises(FlexConfigError) as excinfo:
        stack_unit(current_density_min=3.0)
    assert excinfo.value.field == "current_density_min"


@pytest.mark.unit
def test_the_operating_window_ends_are_configurable():
    """The voltage ceiling and temperature floor are options, not constants.

    They are technology choices: an alkaline stack tops out near 2.0 V and
    tolerates a higher membrane temperature than PEM.
    """
    from flexops.unit_models import electrolysis

    for name in ("CELL_VOLTAGE_MAX", "STACK_TEMPERATURE_MIN"):
        assert not hasattr(electrolysis, name), f"{name} is still a constant"
    # The thermodynamic ends stay constants -- they are not choices.
    assert pyo.value(electrolysis.REVERSIBLE_VOLTAGE) == pytest.approx(1.23)
    assert pyo.value(electrolysis.HHV_HYDROGEN) == pytest.approx(39.4)

    _, unit = stack_unit(
        cell_voltage_max=2.0 * pyunits.V,
        stack_temperature_min=310.0 * pyunits.K,
        stack_temperature_max=363.0 * pyunits.K,
    )
    assert unit.cell_voltage[0].ub == pytest.approx(2.0)
    assert unit.cell_voltage[0].lb == pytest.approx(1.23)
    assert unit.stack_temperature[0].lb == pytest.approx(310.0)
    assert unit.stack_temperature[0].ub == pytest.approx(363.0)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("options", "field"),
    [
        # A ceiling below the thermodynamic floor for splitting water.
        ({"cell_voltage_max": 1.1 * pyunits.V}, "cell_voltage_max"),
        # An inverted temperature window.
        ({"stack_temperature_min": 360.0 * pyunits.K}, "stack_temperature_min"),
        # A setpoint outside its own window, which would fix the Var at an
        # infeasible value and surface as an unexplained solver failure.
        ({"stack_temperature": 370.0 * pyunits.K}, "stack_temperature"),
        (
            {
                "stack_temperature": 300.0 * pyunits.K,
                "stack_temperature_min": 310.0 * pyunits.K,
            },
            "stack_temperature",
        ),
    ],
)
def test_operating_window_ends_are_validated(options, field):
    """Each end of each window is checked at construction, naming its option."""
    with pytest.raises(FlexConfigError) as excinfo:
        stack_unit(**options)
    assert excinfo.value.field == field


@pytest.mark.unit
def test_rectifier_efficiency_must_be_a_fraction():
    """A conversion efficiency outside (0, 1] is rejected by name.

    Raised by the ``ConfigValue`` domain, so Pyomo re-wraps it as ``ValueError``
    carrying the message (see
    ``test_persisted_config_rejects_an_unknown_enum_member``).
    """
    for bad in (0.0, 1.5):
        with pytest.raises(ValueError, match="rectifier_efficiency"):
            stack_unit(rectifier_efficiency=bad)


@pytest.mark.unit
def test_options_round_trip_through_a_persisted_config():
    """The stack model is configurable from a config file, not only from Python."""
    from flexcore.config.schema import UnitConfig
    from flexops.core.ops_block import OpsBlockData

    cfg = UnitConfig.model_validate(
        {
            "unit_model_class": "ElectrolysisSeparator",
            "construction_options": {
                "detail": "stack",
                "thermal": "heat_balance",
                "n_cells": 300,
                "cell_area": {"value": 1200.0, "units": "cm^2"},
                "voltage_intercept": {"value": 1.6, "units": "V"},
                "rectifier_efficiency": 0.94,
            },
        }
    )
    m = dummy_time_block(3)
    m.unit = OpsBlockData.build_from_config(cfg, property_package=m.properties)
    assert m.unit.config.detail is ElectrolysisDetail.STACK
    assert m.unit.config.thermal is ThermalModel.HEAT_BALANCE
    assert pyo.value(m.unit.n_cells) == 300
    assert pyo.value(m.unit.cell_area) == pytest.approx(1200.0)
    assert pyo.value(m.unit.voltage_intercept) == pytest.approx(1.6)
    assert pyo.value(m.unit.rectifier_efficiency) == pytest.approx(0.94)


@pytest.mark.unit
def test_persisted_config_rejects_an_unknown_enum_member():
    """A bad enum value names its own field and the members it accepts.

    Pyomo re-wraps a ``ConfigValue`` domain's exception as ``ValueError``, so
    the ``FlexConfigError`` the domain raises reaches the caller as its message
    rather than its type -- the field name and the allowed values are what the
    contract (conventions §4) actually requires.
    """
    from flexcore.config.schema import UnitConfig
    from flexops.core.ops_block import OpsBlockData

    cfg = UnitConfig.model_validate(
        {
            "unit_model_class": "ElectrolysisSeparator",
            "construction_options": {"detail": "polarization"},
        }
    )
    m = dummy_time_block(3)
    with pytest.raises(ValueError) as excinfo:
        m.unit = OpsBlockData.build_from_config(cfg, property_package=m.properties)
    message = str(excinfo.value)
    assert "detail" in message
    assert "'constant_intensity', 'stack'" in message
    assert "polarization" in message


# -- solving -----------------------------------------------------------------


@pytest.mark.component
@pytest.mark.needs_ipopt
def test_stack_solves_to_the_reference_operating_point():
    """Fixing the feed flow drives the stack to ~1 A/cm^2 and its sanity checks."""
    from flexcore.solvers import get_solver

    m, unit = stack_unit(thermal=ThermalModel.WASTE_HEAT)
    for t in m.time_block.time_index:
        unit.flow_in[t].fix(NOMINAL_FLOW)
    pyo.assert_optimal_termination(get_solver(model=m).solve(m))
    assert pyo.value(unit.current_density[0]) == pytest.approx(1.0, rel=1e-3)
    assert 1.70 <= pyo.value(unit.cell_voltage[0]) <= 1.80
    assert pyo.value(unit.stack_voltage[0]) == pytest.approx(
        250 * pyo.value(unit.cell_voltage[0]), rel=1e-9
    )
    specific_energy = pyo.value(unit.specific_energy_consumption[0])
    assert 50.0 <= specific_energy <= 55.0
    assert 0.70 <= 39.4 / specific_energy <= 0.78
    heat_fraction = pyo.value(unit.power_thermal[0] / unit.power_stack[0])
    assert 0.13 <= heat_fraction <= 0.18
    assert pyo.value(unit.cell_voltage[0]) > 1.48
    # The facility pays the rectifier loss on top of the stack draw.
    assert pyo.value(unit.power_electrical[0]) > pyo.value(unit.power_stack[0])


@pytest.mark.component
@pytest.mark.needs_ipopt
def test_power_following_solves_the_same_system_backward():
    """Fixing the power and freeing the flow needs no model rewrite.

    This is the point of the equation-oriented form: the renewables-coupled
    dispatch case is the same residual set with a different specification.
    """
    from flexcore.solvers import get_solver

    m, unit = stack_unit()
    for t in m.time_block.time_index:
        unit.flow_in[t].unfix()
        unit.power_electrical[t].fix(POWER_ELECTRICAL)
    pyo.assert_optimal_termination(get_solver(model=m).solve(m))
    assert pyo.value(unit.current_density[0]) == pytest.approx(1.0, rel=1e-3)
    assert pyo.value(unit.cell_voltage[0]) == pytest.approx(CELL_VOLTAGE, rel=1e-3)
    assert pyo.value(unit.flow_in[0]) == pytest.approx(NOMINAL_FLOW, rel=1e-3)
    assert pyo.value(unit.hydrogen_production[0]) == pytest.approx(HYDROGEN, rel=1e-3)


@pytest.mark.component
@pytest.mark.needs_ipopt
def test_heat_balance_solves_the_temperature():
    """The steady-state balance lands the default stack at its design temperature."""
    from flexcore.solvers import get_solver

    m, unit = stack_unit(thermal=ThermalModel.HEAT_BALANCE)
    for t in m.time_block.time_index:
        unit.flow_in[t].fix(NOMINAL_FLOW)
    pyo.assert_optimal_termination(get_solver(model=m).solve(m))
    assert pyo.value(unit.stack_temperature[0]) == pytest.approx(353.0, abs=0.5)
    rejected = pyo.value(
        unit.thermal_conductance
        * (unit.stack_temperature[0] - unit.ambient_temperature)
    )
    assert rejected == pytest.approx(
        pyo.value(unit.power_thermal[0]) * 1000.0, rel=1e-6
    )


@pytest.mark.component
@pytest.mark.needs_ipopt
def test_higher_current_density_costs_efficiency():
    """The core tradeoff: more throughput per stack, worse specific energy."""
    from flexcore.solvers import get_solver

    specific_energy = {}
    for flow in (NOMINAL_FLOW, 2 * NOMINAL_FLOW):
        m, unit = stack_unit()
        for t in m.time_block.time_index:
            unit.flow_in[t].fix(flow)
        pyo.assert_optimal_termination(get_solver(model=m).solve(m))
        specific_energy[flow] = pyo.value(unit.specific_energy_consumption[0])
        assert pyo.value(unit.hydrogen_production[0]) > 0.0
    assert specific_energy[2 * NOMINAL_FLOW] > specific_energy[NOMINAL_FLOW]
