"""GenericRenewables(OpsBlockData): capacity_factor-driven electrical export."""

import pyomo.environ as pyo
import pytest
from pyomo.environ import units as pyunits

from flexcore import nomenclature as nm
from flexcore.exceptions import FlexConfigError
from flexops.testing import UnitModelTestHarness, dummy_time_block
from flexops.unit_models import GenericRenewables
from flexops.unit_models.powergeneration.generic_renewables import RenewableTechnology


def _generic_renewables(n: int = 3, **kwargs):
    """Build a GenericRenewables on an ``n``-point ``dummy_time_block``."""
    m = dummy_time_block(n)
    kwargs.setdefault("capacity", 10 * pyunits.kW)
    kwargs.setdefault("capacity_factor", [0.2, 0.5, 0.8][:n])
    m.unit = GenericRenewables(**kwargs)
    return m, m.unit


class TestGenericRenewablesArrayCapacityFactor(UnitModelTestHarness):
    """A plain list capacity_factor; no fluid ports, no dispatch inputs."""

    expected_dof = 0
    expected_solution = {"power_electrical[1]": -5.0}

    def configure(self):
        return _generic_renewables(
            3, capacity=10 * pyunits.kW, capacity_factor=[0.2, 0.5, 0.8]
        )


class TestGenericRenewablesIndexedCapacityFactor(UnitModelTestHarness):
    """An indexed Var capacity_factor, fixed here (not IO-registered)."""

    expected_dof = 0
    expected_solution = {"power_electrical[1]": -6.0}

    def configure(self):
        m = dummy_time_block(3)
        m.cf = pyo.Var(m.time_block.time_index, initialize=0.0, bounds=(0.0, 1.0))
        for t, value in zip(m.time_block.time_index, (0.1, 0.6, 0.9), strict=True):
            m.cf[t].fix(value)
        m.unit = GenericRenewables(capacity=10 * pyunits.kW, capacity_factor=m.cf)
        return m, m.unit


# -- power relation body -----------------------------------------------------


@pytest.mark.unit
def test_generic_renewables_power_relation_body_array_capacity_factor():
    """power_electrical_relation body is 0 at -capacity_factor[t] * capacity."""
    _, unit = _generic_renewables(
        3, capacity=20 * pyunits.kW, capacity_factor=[0.1, 0.4, 1.0]
    )
    for t, cf in zip(range(3), (0.1, 0.4, 1.0), strict=True):
        unit.power_electrical[t].fix(-cf * 20.0)
        assert pyo.value(unit.power_electrical_relation[t].body) == pytest.approx(
            0.0, abs=1e-9
        )


@pytest.mark.unit
def test_generic_renewables_indexed_capacity_factor_is_a_live_reference():
    """Changing an external capacity_factor Var after construction moves the body."""
    m = dummy_time_block(3)
    m.cf = pyo.Var(m.time_block.time_index, initialize=0.3, bounds=(0.0, 1.0))
    for t in m.time_block.time_index:
        m.cf[t].fix(0.3)
    m.unit = GenericRenewables(capacity=10 * pyunits.kW, capacity_factor=m.cf)

    m.unit.power_electrical[0].fix(-3.0)
    assert pyo.value(m.unit.power_electrical_relation[0].body) == pytest.approx(
        0.0, abs=1e-9
    )

    m.cf[0].fix(0.7)
    assert pyo.value(m.unit.power_electrical_relation[0].body) == pytest.approx(
        3.0 - 7.0, abs=1e-9
    )


@pytest.mark.unit
def test_generic_renewables_power_electrical_is_an_export():
    """power_electrical[t] is upper-bounded at 0."""
    _, unit = _generic_renewables(3)
    for t in range(3):
        assert unit.power_electrical[t].ub == pytest.approx(0.0)


@pytest.mark.unit
def test_generic_renewables_power_relation_constraint_is_named():
    """power_electrical_relation exists (the swap contract); power_eq does not."""
    _, unit = _generic_renewables(3)
    assert unit.find_component("power_electrical_relation") is not None
    assert unit.find_component("power_eq") is None


@pytest.mark.unit
def test_generic_renewables_registers_no_thermal_power():
    """Only an ELECTRICAL power record is registered; no power_thermal."""
    _, unit = _generic_renewables(3)
    kinds = {rec.kind for rec in unit._io_registry.power}
    assert kinds == {nm.PowerKind.ELECTRICAL}
    assert unit.find_component("power_thermal") is None


# -- capacity (sizing Var) ----------------------------------------------------


@pytest.mark.unit
def test_generic_renewables_capacity_is_fixed_and_registered():
    """capacity is a fixed Var, registered as a non-regressable process parameter."""
    _, unit = _generic_renewables(3, capacity=15 * pyunits.kW)
    assert unit.capacity.fixed
    assert pyo.value(unit.capacity) == pytest.approx(15.0)
    records = {rec.name: rec for rec in unit._io_registry.parameters}
    assert "capacity" in records
    assert records["capacity"].regressable is False


# -- config validation --------------------------------------------------------


@pytest.mark.unit
def test_generic_renewables_rejects_missing_capacity():
    """Omitting capacity raises FlexConfigError naming the field."""
    with pytest.raises(FlexConfigError) as excinfo:
        _generic_renewables(3, capacity=None)
    assert excinfo.value.field == "capacity"


@pytest.mark.unit
def test_generic_renewables_rejects_missing_capacity_factor():
    """Omitting capacity_factor raises FlexConfigError naming the field."""
    with pytest.raises(FlexConfigError) as excinfo:
        _generic_renewables(3, capacity_factor=None)
    assert excinfo.value.field == "capacity_factor"


@pytest.mark.unit
def test_generic_renewables_rejects_bare_scalar_capacity_factor():
    """A bare number is rejected -- capacity_factor must be a timeseries."""
    with pytest.raises(FlexConfigError) as excinfo:
        _generic_renewables(3, capacity_factor=0.5)
    assert excinfo.value.field == "capacity_factor"


@pytest.mark.unit
def test_generic_renewables_rejects_scalar_pyomo_component_capacity_factor():
    """An unindexed Pyomo Var is rejected -- capacity_factor must be time-indexed."""
    m = dummy_time_block(3)
    m.scalar_cf = pyo.Var(initialize=0.5)
    with pytest.raises(FlexConfigError) as excinfo:
        m.unit = GenericRenewables(
            capacity=10 * pyunits.kW, capacity_factor=m.scalar_cf
        )
    assert excinfo.value.field == "capacity_factor"


@pytest.mark.unit
def test_generic_renewables_rejects_wrong_length_array_capacity_factor():
    """An array-like of the wrong length raises FlexConfigError at build()."""
    with pytest.raises(FlexConfigError) as excinfo:
        _generic_renewables(3, capacity_factor=[0.2, 0.5])
    assert excinfo.value.field == "capacity_factor"


@pytest.mark.unit
def test_generic_renewables_rejects_wrong_length_indexed_capacity_factor():
    """An indexed Var over the wrong-size Set raises FlexConfigError at build()."""
    m = dummy_time_block(3)
    m.short_index = pyo.Set(initialize=[0, 1])
    m.cf = pyo.Var(m.short_index, initialize=0.5)
    with pytest.raises(FlexConfigError) as excinfo:
        m.unit = GenericRenewables(capacity=10 * pyunits.kW, capacity_factor=m.cf)
    assert excinfo.value.field == "capacity_factor"


# -- technology (placeholder) -------------------------------------------------


@pytest.mark.unit
def test_generic_renewables_technology_defaults_to_none():
    """technology is None unless configured -- a pure placeholder in v0."""
    _, unit = _generic_renewables(3)
    assert unit.config.technology is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "technology, expected",
    [("solar", RenewableTechnology.SOLAR), ("wind", RenewableTechnology.WIND)],
)
def test_generic_renewables_technology_accepts_solar_and_wind(technology, expected):
    """A valid technology string coerces to the RenewableTechnology enum."""
    _, unit = _generic_renewables(3, technology=technology)
    assert unit.config.technology is expected


@pytest.mark.unit
def test_generic_renewables_technology_rejects_invalid_value():
    """An unknown technology is a config error naming the valid choices."""
    with pytest.raises(ValueError, match="'solar', 'wind'"):
        _generic_renewables(3, technology="hydro")


@pytest.mark.unit
def test_generic_renewables_technology_is_not_yet_used_in_the_power_relation():
    """technology is a placeholder: it does not change power_electrical_relation."""
    _, solar_unit = _generic_renewables(
        3, capacity=10 * pyunits.kW, capacity_factor=[0.2, 0.5, 0.8], technology="solar"
    )
    _, wind_unit = _generic_renewables(
        3, capacity=10 * pyunits.kW, capacity_factor=[0.2, 0.5, 0.8], technology="wind"
    )
    for t, cf in zip(range(3), (0.2, 0.5, 0.8), strict=True):
        for unit in (solar_unit, wind_unit):
            unit.power_electrical[t].fix(-cf * 10.0)
            assert pyo.value(unit.power_electrical_relation[t].body) == pytest.approx(
                0.0, abs=1e-9
            )


# -- integration with the rest of the library --------------------------------


@pytest.mark.unit
def test_generic_renewables_is_in_the_unit_model_registry():
    """GenericRenewables is reachable from flexops and its unit-model registry."""
    import flexops
    from flexops import unit_models

    assert "GenericRenewables" in unit_models.__all__
    assert flexops.GenericRenewables is GenericRenewables


@pytest.mark.component
@pytest.mark.needs_highs
def test_generic_renewables_exports_net_against_plant_load():
    """A GenericRenewables export nets against a plant's load, not adding to it."""
    from flexcore.exceptions import FlexSolverError
    from flexcore.solvers import get_solver
    from flexops import PlantBlock
    from flexops.unit_models import ConstantEnergyIntensityModel

    m = dummy_time_block(3)
    m.plant = PlantBlock(time_block=m.time_block)
    m.plant.solar = GenericRenewables(
        capacity=10 * pyunits.kW, capacity_factor=[0.2, 0.5, 0.8]
    )
    m.plant.load = ConstantEnergyIntensityModel(property_package=m.properties)
    m.plant._build_aggregates()

    for t in m.time_block.time_index:
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
            + pyo.value(m.plant.solar.power_electrical[t]),
            rel=1e-6,
        )
        assert pyo.value(m.plant.solar.power_electrical[t]) <= 0.0
