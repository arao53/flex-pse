"""SpaceCooling and Chiller: cooling load to electrical draw through a COP."""

import pyomo.environ as pyo
import pytest
from pyomo.environ import units as pyunits

from flexcore.exceptions import FlexConfigError
from flexcore.solvers import ProblemClass, classify
from flexops.testing import UnitModelTestHarness, dummy_time_block
from flexops.unit_models import Chiller, SpaceCooling


def with_load(unit, load_kw: float = 100.0):
    """Set every step of ``unit.cooling_load`` to ``load_kw`` and return the unit."""
    for t in unit.cooling_load:
        unit.cooling_load[t].set_value(load_kw)
    return unit


class TestSpaceCooling(UnitModelTestHarness):
    """100 kW of load at COP 4 with 0.05 kW/kW ancillary draws 30 kW."""

    expected_dof = 0
    expected_solution = {f"power_electrical[{t}]": 30.0 for t in range(3)}

    def configure(self):
        m = dummy_time_block(3)
        m.unit = SpaceCooling(cop=4.0, ancillary_intensity=0.05)
        return m, with_load(m.unit)


class TestChiller(UnitModelTestHarness):
    """100 kW of load at the default constant COP 5 and 0.05 ancillary draws 25 kW."""

    expected_dof = 0
    expected_solution = {f"power_electrical[{t}]": 25.0 for t in range(3)}

    def configure(self):
        m = dummy_time_block(3)
        m.unit = Chiller()
        return m, with_load(m.unit)


@pytest.mark.unit
def test_space_cooling_cooling_load_is_a_registered_input():
    m = dummy_time_block(3)
    m.unit = SpaceCooling()
    inputs = {r.name for r in m.unit._io_registry.io_variables if r.role == "input"}
    assert "cooling_load" in inputs


@pytest.mark.unit
def test_space_cooling_is_lp():
    m = dummy_time_block(3)
    m.unit = SpaceCooling()
    assert classify(m) is ProblemClass.LP


@pytest.mark.unit
def test_chiller_constant_cop_is_lp_and_part_load_is_nlp():
    m = dummy_time_block(3)
    m.unit = Chiller()
    assert classify(m) is ProblemClass.LP
    m.part_load = Chiller(
        cop_coefficients=(0.0, 0.0, 1.0, 0.5), rated_load=50 * pyunits.kW
    )
    assert classify(m) is ProblemClass.NLP


@pytest.mark.unit
def test_chiller_part_load_requires_rated_load():
    m = dummy_time_block(3)
    with pytest.raises(FlexConfigError) as err:
        m.unit = Chiller(cop_coefficients=(0.0, 0.0, 1.0, 0.0))
    assert err.value.field == "rated_load"


@pytest.mark.unit
def test_chiller_wet_bulb_coefficient_requires_wet_bulb():
    m = dummy_time_block(3)
    with pytest.raises(FlexConfigError) as err:
        m.unit = Chiller(cop_coefficients=(0.0, 0.05, 0.0, 0.0))
    assert err.value.field == "wet_bulb_temperature"


@pytest.mark.unit
def test_chiller_rejects_wet_bulb_of_wrong_length():
    m = dummy_time_block(3)
    with pytest.raises(FlexConfigError) as err:
        m.unit = Chiller(
            cop_coefficients=(0.0, 0.05, 0.0, 0.0), wet_bulb_temperature=[290.0]
        )
    assert err.value.field == "wet_bulb_temperature"


@pytest.mark.unit
def test_chiller_rejects_wrong_number_of_cop_coefficients():
    m = dummy_time_block(3)
    with pytest.raises(FlexConfigError) as err:
        m.unit = Chiller(cop_coefficients=(0.0, 0.0, 0.0))
    assert err.value.field == "cop_coefficients"


@pytest.mark.unit
def test_chiller_cop_uses_ambient_temperatures():
    m = dummy_time_block(3)
    m.unit = Chiller(
        cop=2.0,
        cop_coefficients=(0.15, 0.06, 0.0, 0.0),
        wet_bulb_temperature=[290.0, 295.0, 300.0],
    )
    expected = 2.0 + 0.15 * 280.15 - 0.06 * 295.0
    assert pyo.value(m.unit.operating_cop[1]) == pytest.approx(expected)
