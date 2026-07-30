"""PlantBlock: unit aggregation, construction order, TimeBlock auto-discovery (§3.3)."""

import pyomo.environ as pyo
import pytest
from pyomo.environ import units as pyunits

from flexcore.exceptions import FlexConfigError
from flexops import PlantBlock
from flexops.core.time_block import TimeBlock
from flexops.testing import dummy_time_block
from flexops.unit_models import BatteryModel, ConstantEnergyIntensityModel

_SURROGATE_KW = 2.0
_BATTERY_KW = 0.5


def _plant_with_two_units(n: int = 3):
    """Build a plant holding a ConstantEnergyIntensityModel and a BatteryModel."""
    m = dummy_time_block(n)
    m.plant = PlantBlock(time_block=m.time_block)
    m.plant.surrogate = ConstantEnergyIntensityModel(property_package=m.properties)
    m.plant.battery = BatteryModel(capacity=10 * pyunits.kWh)
    return m


def _fix_power(plant, time_index) -> None:
    """Fix every unit's power draw to a hand-picked value."""
    for t in time_index:
        plant.surrogate.power_electrical[t].fix(_SURROGATE_KW)
        plant.battery.power_electrical[t].fix(_BATTERY_KW)


@pytest.mark.unit
def test_aggregation_two_units():
    """total_electrical/thermal_power equal the hand sum over the plant's units."""
    m = _plant_with_two_units()
    m.plant._build_aggregates()
    _fix_power(m.plant, m.time_block.time_index)

    for t in m.time_block.time_index:
        assert pyo.value(m.plant.total_electrical_power[t]) == pytest.approx(
            _SURROGATE_KW + _BATTERY_KW, rel=1e-6
        )
        assert pyo.value(m.plant.total_thermal_power[t]) == pytest.approx(0.0, abs=1e-9)


@pytest.mark.unit
def test_aggregates_survive_units_added_after_the_first_call():
    """Aggregating before the units exist, then again after, still sums them all.

    The frozen api-freeze script builds the plant (and costing) before its
    units, so aggregation must be re-entrant rather than one-shot (pitfall 3).
    """
    m = dummy_time_block(3)
    m.plant = PlantBlock(time_block=m.time_block)
    m.plant._build_aggregates()
    for t in m.time_block.time_index:
        assert pyo.value(m.plant.total_electrical_power[t]) == pytest.approx(
            0.0, abs=1e-9
        )

    m.plant.surrogate = ConstantEnergyIntensityModel(property_package=m.properties)
    m.plant.battery = BatteryModel(capacity=10 * pyunits.kWh)
    m.plant._build_aggregates()
    _fix_power(m.plant, m.time_block.time_index)

    for t in m.time_block.time_index:
        assert pyo.value(m.plant.total_electrical_power[t]) == pytest.approx(
            _SURROGATE_KW + _BATTERY_KW, rel=1e-6
        )


@pytest.mark.unit
def test_plant_is_steady_state_and_shares_the_time_block_set():
    """dynamic=False and the flowsheet time domain IS the TimeBlock's set (R2)."""
    m = _plant_with_two_units()
    assert m.plant.config.dynamic is False
    assert m.plant.time is m.time_block.time_index


@pytest.mark.unit
def test_time_block_autodiscovery():
    """Omitting time_block= works with one TimeBlock and errors on two."""
    m = dummy_time_block(3)
    m.plant = PlantBlock()
    assert m.plant.time is m.time_block.time_index

    m2 = dummy_time_block(3)
    m2.other_time_block = TimeBlock(
        start_date="2025-02-01", end_date="2025-02-02", time_step=1 * pyunits.hr
    )
    with pytest.raises(FlexConfigError):
        m2.plant = PlantBlock()
