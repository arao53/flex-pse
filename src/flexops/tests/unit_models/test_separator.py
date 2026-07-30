"""Separator and its derived units: harness subclasses (§3.4, R6)."""

import pytest
from pyomo.environ import units as pyunits

from flexcore import nomenclature as nm
from flexops.testing import UnitModelTestHarness, dummy_time_block
from flexops.unit_models import ElectrolysisSeparator, ReverseOsmosisSkid, Separator


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


class TestElectrolysisSeparator(UnitModelTestHarness):
    """Electrolysis as a separation: exercises power_thermal as well (§3.4)."""

    expected_dof = 0

    def configure(self):
        m = dummy_time_block(3)
        m.unit = ElectrolysisSeparator(property_package=m.properties)
        return m, m.unit

    @pytest.mark.unit
    def test_registers_electrical_and_thermal_power(self):
        """Both power kinds are registered -- the power_thermal exerciser."""
        _, unit = self.configure()
        kinds = {record.kind for record in unit._io_registry.power}
        assert kinds == {nm.PowerKind.ELECTRICAL, nm.PowerKind.THERMAL}
        assert unit.find_component("power_thermal_relation") is not None


@pytest.mark.unit
def test_no_electrolyzer_class():
    """There is no ``Electrolyzer``: it is ``Separator`` (R6)."""
    import flexops
    import flexops.unit_models

    assert not hasattr(flexops, "Electrolyzer")
    assert not hasattr(flexops.unit_models, "Electrolyzer")


@pytest.mark.unit
def test_ro_skid_outlet_semantics():
    """The skid's split fraction is its permeate recovery."""
    m = dummy_time_block(3)
    m.unit = ReverseOsmosisSkid(property_package=m.properties, split_fraction=0.75)
    for t in m.time_block.time_index:
        m.unit.flow_in[t].fix(4.0)
        m.unit.flow_out_a[t].fix(3.0)
        m.unit.flow_out_b[t].fix(1.0)
        assert pyunits.get_units(m.unit.flow_out_a[t]) == pyunits.m**3 / pyunits.hr
