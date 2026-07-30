"""Separator and its derived units: harness subclasses (§3.4, R6).

``ElectrolysisSeparator`` has its own module-mirroring test file,
``test_electrolysis.py``.
"""

import pyomo.environ as pyo
import pytest
from pyomo.environ import units as pyunits

from flexops.testing import UnitModelTestHarness, dummy_time_block
from flexops.unit_models import ReverseOsmosisSkid, Separator


class TestSeparator(UnitModelTestHarness):
    """One feed split into two product streams, with an electrical draw."""

    expected_dof = 0

    def configure(self):
        m = dummy_time_block(3)
        m.unit = Separator(
            property_package=m.properties,
            split_fraction=0.6,
            energy_intensity=0.4 * pyunits.kWh / pyunits.m**3,
        )
        return m, m.unit


class TestReverseOsmosisSkid(UnitModelTestHarness):
    """RO skid: feed -> permeate (outlet_a) + concentrate (outlet_b)."""

    expected_dof = 0

    def configure(self):
        m = dummy_time_block(3)
        m.unit = ReverseOsmosisSkid(property_package=m.properties)
        return m, m.unit


@pytest.mark.unit
def test_no_electrolyzer_class():
    """There is no ``Electrolyzer``: it is ``Separator`` (R6)."""
    import flexops
    import flexops.unit_models

    assert not hasattr(flexops, "Electrolyzer")
    assert not hasattr(flexops.unit_models, "Electrolyzer")


@pytest.mark.unit
def test_ro_skid_outlet_semantics():
    """The skid's split fraction is its permeate (outlet_a) recovery."""
    m = dummy_time_block(3)
    m.unit = ReverseOsmosisSkid(property_package=m.properties, split_fraction=0.75)
    for t in m.time_block.time_index:
        m.unit.flow_in[t].fix(4.0)
        m.unit.flow_out_a[t].fix(3.0)
        m.unit.flow_out_b[t].fix(1.0)
        assert pyo.value(m.unit.split_definition[t].body) == pytest.approx(
            3.0 - 0.75 * 4.0, abs=1e-9
        )
        assert pyo.value(m.unit.split_mass_balance[t].body) == pytest.approx(
            0.0, abs=1e-9
        )
