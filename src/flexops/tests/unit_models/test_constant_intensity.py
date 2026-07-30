"""ConstantEnergyIntensityModel: harness + the swap-contract constraint name."""

import pytest
from pyomo.environ import units as pyunits

from flexops.testing import UnitModelTestHarness, dummy_time_block
from flexops.unit_models import ConstantEnergyIntensityModel


def _surrogate(n: int = 3, **kwargs):
    """Build a ConstantEnergyIntensityModel on an ``n``-point dummy model."""
    m = dummy_time_block(n)
    m.unit = ConstantEnergyIntensityModel(property_package=m.properties, **kwargs)
    return m, m.unit


class TestConstantEnergyIntensityModel(UnitModelTestHarness):
    """Generic energy-factor-times-flow unit; inlet flow is the only input."""

    expected_dof = 0

    def configure(self):
        return _surrogate(3, energy_intensity=0.5 * pyunits.kWh / pyunits.m**3)


@pytest.mark.unit
def test_power_electrical_relation_constraint_is_named():
    """The energy relation carries the documented swappable name (R11, M10)."""
    _, unit = _surrogate(3)
    assert unit.find_component("power_electrical_relation") is not None
