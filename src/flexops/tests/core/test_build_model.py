"""build_model: the config-driven path equals the imperative one (§2.3, R3)."""

from pathlib import Path

import pyomo.environ as pyo
import pytest
from pyomo.environ import units as pyunits
from pyomo.network import Arc
from pyomo.opt import assert_optimal_termination

from flexcore.config.io import load_model_config
from flexcore.exceptions import FlexConfigError
from flexcore.solvers import get_solver
from flexops import (
    BatteryModel,
    ConstantEnergyIntensityModel,
    FlexCosting,
    PlantBlock,
    SimpleAqueousFlow,
    Tank,
    TimeBlock,
    build_model,
)

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_CONFIG = _FIXTURES / "plant_config_demo.json"


def _hand_built() -> pyo.ConcreteModel:
    """Build the twin of ``plant_config_demo.json`` imperatively."""
    m = pyo.ConcreteModel()
    m.time_block = TimeBlock(
        start_date="2025-07-08", end_date="2025-07-09", time_step=1 * pyunits.hr
    )
    m.properties = SimpleAqueousFlow(fixed_density=True)
    m.costing = FlexCosting(
        time_block=m.time_block, tariff_file=str(_FIXTURES / "tariff_tou_demo.json")
    )
    m.demo = PlantBlock(time_block=m.time_block)
    m.demo.tank = Tank(
        property_package=m.properties,
        max_volume=1000.0 * pyunits.m**3,
        initial_volume=500.0 * pyunits.m**3,
    )
    m.demo.surrogate = ConstantEnergyIntensityModel(
        property_package=m.properties,
        energy_intensity=0.5 * pyunits.kWh / pyunits.m**3,
        costing_package=m.costing,
    )
    m.demo.battery = BatteryModel(
        capacity=10.0 * pyunits.kWh, costing_package=m.costing
    )
    m.demo.arc_0 = Arc(source=m.demo.tank.outlet, destination=m.demo.surrogate.inlet)
    m.costing.cost_process()
    m.objective = pyo.Objective(expr=m.costing.aggregate_operating_cost)
    return m


def _solve(model) -> float:
    """Expand arcs, solve, and return the objective value."""
    pyo.TransformationFactory("network.expand_arcs").apply_to(model)
    results = get_solver(model=model, prefer="highs").solve(model)
    assert_optimal_termination(results)
    return pyo.value(model.objective)


@pytest.mark.component
@pytest.mark.needs_highs
def test_build_model_matches_hand_built(monkeypatch):
    """Config-driven and imperative builds solve to the same objective."""
    monkeypatch.chdir(_FIXTURES)
    from_config = build_model(load_model_config(_CONFIG))
    assert from_config.demo.tank.find_component("holdup") is not None
    assert from_config.demo.surrogate.find_component("power_electrical") is not None

    assert _solve(from_config) == pytest.approx(_solve(_hand_built()), rel=1e-6)


@pytest.mark.unit
def test_build_model_bad_config_raises():
    """A malformed unit config errors with the offending field path in the message."""
    bad = {
        "schema_version": "0.0.1",
        "time": {
            "start_date": "2025-07-08",
            "end_date": "2025-07-09",
            "time_step": "1 hr",
        },
        "costing": {
            "energy_prices": {"electrical": {"value": 0.1, "units": "USD/kWh"}}
        },
        "plant": {
            "name": "demo",
            "units": {"surrogate": {"construction_options": {}}},
        },
    }
    with pytest.raises(FlexConfigError) as excinfo:
        build_model(bad)
    assert "plant.units.surrogate.unit_model_class" in str(excinfo.value)
