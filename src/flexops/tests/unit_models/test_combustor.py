"""Combustor(OpsBlockData): multi-inlet gas mixing, electrical export (§3.2, §3.4)."""

import pyomo.environ as pyo
import pytest
from idaes.core import LiquidPhase, declare_process_block_class
from pyomo.environ import units as pyunits
from pyomo.network import Port

from flexcore import nomenclature as nm
from flexcore.exceptions import FlexConfigError
from flexops.properties.simple_gas import SimpleGasFlowData
from flexops.testing import UnitModelTestHarness, dummy_gas_time_block
from flexops.unit_models import Combustor
from flexops.unit_models.powergeneration.combustor import CombustorPowerRelation

_DIGESTER_HV = 6.0 * pyunits.kWh / pyunits.m**3
_NATURAL_GAS_HV = 10.5 * pyunits.kWh / pyunits.m**3
_EFFICIENCY = 0.35


@declare_process_block_class("_TwoPhaseGasFlow")
class _TwoPhaseGasFlowData(SimpleGasFlowData):
    """Test-only stub: SimpleGasFlow plus a second phase.

    Exists only to exercise Combustor's single-phase guard -- no other
    two-phase property package exists in the repository.
    """

    def build(self) -> None:
        super().build()
        self.Liq = LiquidPhase()


# declare_process_block_class injects the constructible wrapper into this
# module's namespace at runtime; bind it explicitly (as simple_gas.py does)
# so static tools resolve the forward reference used below.
_TwoPhaseGasFlow = globals()["_TwoPhaseGasFlow"]


def _combustor(n: int = 3, **kwargs):
    """Build a Combustor on an ``n``-point ``dummy_gas_time_block``."""
    m = dummy_gas_time_block(n)
    m.unit = Combustor(property_package=m.properties, **kwargs)
    return m, m.unit


def _fix(unit, name: str, t, value: float) -> None:
    """Fix a named state var on inlet/outlet state block ``name`` at time ``t``."""
    getattr(unit, name)[t].fix(value)


class TestCombustorConstantIntensity(UnitModelTestHarness):
    """Single ``fuel`` inlet, no heating values: the constant-intensity relation.

    With the default ``energy_intensity=2.0 kWh/m^3`` and the inlet's
    ``flow_vol_phase`` at its construction-time initial value of 1.0 m^3/hr,
    ``power_electrical[t] == -(2.0 * 1.0) == -2.0`` kW.
    """

    expected_dof = 0
    expected_solution = {"power_electrical[0]": -2.0}

    def configure(self):
        return _combustor(3)


class TestCombustorHeatingValue(UnitModelTestHarness):
    """Two named inlets, both with heating values: the heating-value relation.

    Both inlet flows sit at their construction-time initial value of 1.0
    m^3/hr, so ``power_electrical[t] == -efficiency * (HV_digester + HV_natural)
    == -0.35 * (6.0 + 10.5) == -5.775`` kW.
    """

    expected_dof = 0
    expected_solution = {"power_electrical[0]": -5.775}

    def configure(self):
        return _combustor(
            3,
            inlet_names=("digester_gas", "natural_gas"),
            heating_values={
                "digester_gas": _DIGESTER_HV,
                "natural_gas": _NATURAL_GAS_HV,
            },
            efficiency=_EFFICIENCY,
        )


# -- topology and balances -------------------------------------------------


@pytest.mark.unit
def test_combustor_builds_one_port_per_inlet_name():
    """Ports are named ``inlet_<name>`` per ``inlet_names``, plus one ``outlet``."""
    _, unit = _combustor(3, inlet_names=("digester_gas", "natural_gas", "air"))

    ports = {p.local_name for p in unit.component_objects(Port, descend_into=False)}
    assert ports == {"inlet_digester_gas", "inlet_natural_gas", "inlet_air", "outlet"}
    for port in ports:
        assert len(list(getattr(unit, port).values())) > 0
    assert unit.find_component("inlet") is None


@pytest.mark.unit
def test_combustor_mixing_mass_balance_body():
    """``mixing_mass_balance`` body is 0 iff outlet flow equals the inlet sum."""
    _, unit = _combustor(3, inlet_names=("a", "b", "c"))
    for t in range(3):
        _fix(unit, "flow_in_a", t, 1.0)
        _fix(unit, "flow_in_b", t, 2.0)
        _fix(unit, "flow_in_c", t, 3.0)
        _fix(unit, "flow_out", t, 6.0)
        assert pyo.value(unit.mixing_mass_balance[t].body) == pytest.approx(
            6.0 - (1.0 + 2.0 + 3.0), abs=1e-9
        )
        unit.flow_out[t].fix(7.0)
        assert pyo.value(unit.mixing_mass_balance[t].body) == pytest.approx(
            7.0 - (1.0 + 2.0 + 3.0), abs=1e-9
        )


@pytest.mark.unit
def test_combustor_equalizes_inlet_intensive_states():
    """Non-reference inlets' pressure/temperature/density equal the reference's."""
    _, unit = _combustor(3, inlet_names=("digester_gas", "natural_gas"))
    for t in range(3):
        unit.inlet_digester_gas_state.pressure[t].fix(101325.0)
        unit.inlet_natural_gas_state.pressure[t].fix(101325.0)
        assert pyo.value(
            unit.inlet_state_equality_pressure[t, "natural_gas"].body
        ) == pytest.approx(0.0, abs=1e-9)

        unit.inlet_natural_gas_state.pressure[t].fix(150000.0)
        assert pyo.value(
            unit.inlet_state_equality_pressure[t, "natural_gas"].body
        ) == pytest.approx(150000.0 - 101325.0, abs=1e-9)


@pytest.mark.unit
def test_combustor_single_inlet_builds_no_state_equalities():
    """With one inlet, no ``inlet_state_equality_*`` constraints are built."""
    _, unit = _combustor(3)
    for state_var in ("pressure", "temperature", "dens_mass"):
        assert unit.find_component(f"inlet_state_equality_{state_var}") is None


@pytest.mark.unit
def test_combustor_arbitrary_inlet_count():
    """Four heating-value inlets build, are units-consistent, and all sum."""
    from pyomo.util.check_units import assert_units_consistent

    _, unit = _combustor(
        3,
        inlet_names=("a", "b", "c", "d"),
        heating_values={
            "a": 5.0 * pyunits.kWh / pyunits.m**3,
            "b": 6.0 * pyunits.kWh / pyunits.m**3,
            "c": 7.0 * pyunits.kWh / pyunits.m**3,
            "d": 8.0 * pyunits.kWh / pyunits.m**3,
        },
    )
    assert_units_consistent(unit)
    for t in range(3):
        for name, val in zip(("a", "b", "c", "d"), (1.0, 2.0, 3.0, 4.0), strict=True):
            _fix(unit, f"flow_in_{name}", t, val)
        unit.flow_out[t].fix(10.0)
        assert pyo.value(unit.mixing_mass_balance[t].body) == pytest.approx(
            0.0, abs=1e-9
        )


@pytest.mark.unit
def test_combustor_registers_only_the_reference_inlet_intensive_states():
    """Every inlet's flow is an input; only the reference inlet's other states are."""
    _, unit = _combustor(3, inlet_names=("digester_gas", "natural_gas"))
    inputs = {
        rec.var.parent_block().local_name + "." + rec.name
        for rec in unit._io_registry.io_variables
        if rec.role == "input"
    }
    assert "inlet_digester_gas_state.flow_vol_phase" in inputs
    assert "inlet_natural_gas_state.flow_vol_phase" in inputs
    for state_var in ("pressure", "temperature", "dens_mass"):
        assert f"inlet_digester_gas_state.{state_var}" in inputs
        assert f"inlet_natural_gas_state.{state_var}" not in inputs

    outputs = {
        rec.var.parent_block().local_name + "." + rec.name
        for rec in unit._io_registry.io_variables
        if rec.role == "output"
    }
    for state_var in ("pressure", "temperature", "dens_mass", "flow_vol_phase"):
        assert f"outlet_state.{state_var}" in outputs


# -- outlet state -----------------------------------------------------------


@pytest.mark.unit
def test_combustor_outlet_temperature_is_the_flue_gas_temperature():
    """``outlet_temperature_eq`` holds the outlet at ``flue_gas_temperature``."""
    _, unit = _combustor(3, flue_gas_temperature=800 * pyunits.K)
    assert unit.find_component("pass_through_temperature_eq") is None
    for t in range(3):
        unit.outlet_state.temperature[t].fix(800.0)
        assert pyo.value(unit.outlet_temperature_eq[t].body) == pytest.approx(
            0.0, abs=1e-9
        )
        unit.outlet_state.temperature[t].fix(750.0)
        # Pyomo orders a scalar Var (flue_gas_temperature) before an indexed
        # one (outlet_state.temperature[t]) in an equality's body regardless
        # of the write order in the rule, so only the magnitude is asserted.
        assert abs(pyo.value(unit.outlet_temperature_eq[t].body)) == pytest.approx(
            50.0, abs=1e-9
        )


@pytest.mark.unit
def test_combustor_outlet_density_follows_ideal_gas():
    """``outlet_density_eq``: outlet dens * T_flue == inlet dens * T_in."""
    _, unit = _combustor(3, flue_gas_temperature=750 * pyunits.K)
    for t in range(3):
        unit.inlet_fuel_state.dens_mass[t].fix(1.2)
        unit.inlet_fuel_state.temperature[t].fix(300.0)
        expected_outlet_dens = 1.2 * 300.0 / 750.0
        unit.outlet_state.dens_mass[t].fix(expected_outlet_dens)
        assert pyo.value(unit.outlet_density_eq[t].body) == pytest.approx(0.0, rel=1e-6)


@pytest.mark.unit
def test_combustor_passes_pressure_through():
    """``pass_through_pressure_eq`` exists exactly once and holds by hand."""
    _, unit = _combustor(3)
    assert unit.find_component("pass_through_pressure_eq") is not None
    for t in range(3):
        unit.inlet_fuel_state.pressure[t].fix(101325.0)
        unit.outlet_state.pressure[t].fix(101325.0)
        assert pyo.value(unit.pass_through_pressure_eq[t].body) == pytest.approx(
            0.0, abs=1e-9
        )


# -- relation selection and gating ------------------------------------------


@pytest.mark.unit
def test_combustor_defaults_to_constant_intensity():
    """No ``heating_values`` given -> ``CONSTANT_INTENSITY``; ``efficiency`` absent."""
    _, unit = _combustor(3)
    assert unit._power_relation is CombustorPowerRelation.CONSTANT_INTENSITY
    assert unit.find_component("energy_intensity") is not None
    assert unit.find_component("efficiency") is None


@pytest.mark.unit
def test_combustor_selects_heating_value_when_every_inlet_has_one():
    """A heating value for every inlet selects ``HEATING_VALUE``, not intensity."""
    _, unit = _combustor(
        3,
        inlet_names=("digester_gas", "natural_gas"),
        heating_values={"digester_gas": _DIGESTER_HV, "natural_gas": _NATURAL_GAS_HV},
    )
    assert unit._power_relation is CombustorPowerRelation.HEATING_VALUE
    for name in ("digester_gas", "natural_gas"):
        var = unit.find_component(f"heating_value_{name}")
        assert var is not None
        assert var.fixed
        # pyunits.get_units(...) returns a Pyomo NumericValue; comparing two
        # with == builds a relational expression rather than a bool, so units
        # equality is checked by string, matching OpsBlockData._units_str.
        assert str(pyunits.get_units(var)) == str(pyunits.kWh / pyunits.m**3)
    assert unit.find_component("energy_intensity") is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "heating_values",
    [
        {"digester_gas": _DIGESTER_HV},
        {"digester_gas": _DIGESTER_HV, "natural_gas": _NATURAL_GAS_HV, "coal": 1.0},
    ],
)
def test_combustor_rejects_a_bad_heating_value_mapping(heating_values):
    """A partial mapping, or one naming an unknown inlet, is rejected by field."""
    with pytest.raises(FlexConfigError) as excinfo:
        _combustor(
            3,
            inlet_names=("digester_gas", "natural_gas"),
            heating_values=heating_values,
        )
    assert excinfo.value.field == "heating_values"


@pytest.mark.unit
def test_combustor_rejects_efficiency_under_constant_intensity():
    """``efficiency`` set with no ``heating_values`` is a no-op the config rejects."""
    with pytest.raises(FlexConfigError) as excinfo:
        _combustor(3, efficiency=0.5)
    assert excinfo.value.field == "efficiency"


@pytest.mark.unit
def test_combustor_rejects_energy_intensity_under_heating_value():
    """``energy_intensity`` set alongside a full ``heating_values`` map is rejected."""
    with pytest.raises(FlexConfigError) as excinfo:
        _combustor(
            3,
            heating_values={"fuel": _DIGESTER_HV},
            energy_intensity=1.0 * pyunits.kWh / pyunits.m**3,
        )
    assert excinfo.value.field == "energy_intensity"


@pytest.mark.unit
@pytest.mark.parametrize("inlet_names", [(), ("a", "a")])
def test_combustor_rejects_empty_or_duplicate_inlet_names(inlet_names):
    """Empty or duplicated ``inlet_names`` raise, naming the field."""
    with pytest.raises(FlexConfigError) as excinfo:
        _combustor(3, inlet_names=inlet_names)
    assert excinfo.value.field == "inlet_names"


@pytest.mark.unit
def test_combustor_requires_a_single_phase_property_package():
    """A property package with more than one phase is rejected."""
    m = dummy_gas_time_block(3)
    m.two_phase_properties = _TwoPhaseGasFlow()
    with pytest.raises(FlexConfigError) as excinfo:
        m.unit = Combustor(property_package=m.two_phase_properties)
    assert excinfo.value.field == "property_package"


# -- power relation -----------------------------------------------------------


@pytest.mark.unit
def test_combustor_power_relation_body_heating_value():
    """The heating-value relation body is 0 at the hand-computed export."""
    _, unit = _combustor(
        3,
        inlet_names=("digester_gas", "natural_gas"),
        heating_values={"digester_gas": _DIGESTER_HV, "natural_gas": _NATURAL_GAS_HV},
        efficiency=_EFFICIENCY,
    )
    for t in range(3):
        _fix(unit, "flow_in_digester_gas", t, 2.0)
        _fix(unit, "flow_in_natural_gas", t, 3.0)
        expected = -_EFFICIENCY * (6.0 * 2.0 + 10.5 * 3.0)
        unit.power_electrical[t].fix(expected)
        assert pyo.value(unit.power_electrical_relation[t].body) == pytest.approx(
            0.0, rel=1e-6
        )


@pytest.mark.unit
def test_combustor_power_relation_body_constant_intensity():
    """The constant-intensity relation body is 0 over the total inlet flow."""
    _, unit = _combustor(
        3, inlet_names=("a", "b"), energy_intensity=1.5 * pyunits.kWh / pyunits.m**3
    )
    for t in range(3):
        _fix(unit, "flow_in_a", t, 2.0)
        _fix(unit, "flow_in_b", t, 3.0)
        expected = -1.5 * (2.0 + 3.0)
        unit.power_electrical[t].fix(expected)
        assert pyo.value(unit.power_electrical_relation[t].body) == pytest.approx(
            0.0, rel=1e-6
        )


@pytest.mark.unit
def test_combustor_power_electrical_is_an_export():
    """``power_electrical[t]`` is upper-bounded at 0."""
    _, unit = _combustor(3)
    for t in range(3):
        assert unit.power_electrical[t].ub == pytest.approx(0.0)


@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"heating_values": {"fuel": _DIGESTER_HV}},
    ],
)
def test_combustor_power_relation_constraint_is_named(kwargs):
    """Both relations build ``power_electrical_relation`` (the swap contract)."""
    _, unit = _combustor(3, **kwargs)
    assert unit.find_component("power_electrical_relation") is not None
    assert unit.find_component("power_eq") is None


@pytest.mark.unit
def test_combustor_registers_no_thermal_power():
    """Only an ELECTRICAL power record is registered; no ``power_thermal``."""
    _, unit = _combustor(3)
    kinds = {rec.kind for rec in unit._io_registry.power}
    assert kinds == {nm.PowerKind.ELECTRICAL}
    assert unit.find_component("power_thermal") is None


# -- integration with the rest of the library --------------------------------


@pytest.mark.unit
def test_combustor_registers_no_fuel_usage():
    """Fuel-cost registration is deliberately out of scope for this unit."""
    _, unit = _combustor(3)
    assert unit._io_registry.fuel == []


@pytest.mark.unit
def test_combustor_is_in_the_unit_model_registry():
    """``Combustor`` is reachable from ``flexops`` and its unit-model registry."""
    import flexops
    from flexops import unit_models

    assert "Combustor" in unit_models.__all__
    assert flexops.Combustor is Combustor


@pytest.mark.component
@pytest.mark.needs_highs
def test_combustor_exports_net_against_plant_load():
    """A combustor's export nets against a plant's load rather than adding to it."""
    from flexcore.exceptions import FlexSolverError
    from flexcore.solvers import get_solver
    from flexops import PlantBlock, SimpleAqueousFlow
    from flexops.unit_models import ConstantEnergyIntensityModel

    m = dummy_gas_time_block(3)
    m.aqueous_properties = SimpleAqueousFlow(fixed_density=True)
    m.plant = PlantBlock(time_block=m.time_block)
    m.plant.combustor = Combustor(property_package=m.properties)
    m.plant.load = ConstantEnergyIntensityModel(property_package=m.aqueous_properties)
    m.plant._build_aggregates()

    for t in m.time_block.time_index:
        m.plant.combustor.inlet_fuel_state.flow_vol_phase[t, "Vap"].fix(1.0)
        m.plant.combustor.inlet_fuel_state.pressure[t].fix(101325.0)
        m.plant.combustor.inlet_fuel_state.temperature[t].fix(300.0)
        m.plant.load.inlet_state.flow_vol_phase[t, "Liq"].fix(1.0)

    try:
        solver = get_solver(model=m)
    except FlexSolverError as exc:
        pytest.skip(f"flexcore.solvers.get_solver not available: {exc}")
    results = solver.solve(m)
    pyo.assert_optimal_termination(results)

    for t in m.time_block.time_index:
        assert pyo.value(m.plant.total_electrical_power[t]) == pytest.approx(
            pyo.value(m.plant.load.power_electrical[t])
            + pyo.value(m.plant.combustor.power_electrical[t]),
            rel=1e-6,
        )
        assert pyo.value(m.plant.combustor.power_electrical[t]) < 0.0
