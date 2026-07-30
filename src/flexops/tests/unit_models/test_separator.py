"""Separator and its derived units: harness subclasses (§3.4, R6)."""

import pyomo.environ as pyo
import pytest
from pyomo.environ import units as pyunits

from flexcore.exceptions import FlexConfigError
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
    """The skid's ``recovery`` is its permeate (outlet_a) fraction of the feed."""
    m = dummy_time_block(3)
    m.unit = ReverseOsmosisSkid(property_package=m.properties, recovery=0.45)
    for t in m.time_block.time_index:
        m.unit.flow_in[t].fix(4.0)
        m.unit.flow_out_a[t].fix(1.8)
        m.unit.flow_out_b[t].fix(2.2)
        assert pyo.value(m.unit.split_definition[t].body) == pytest.approx(
            1.8 - 0.45 * 4.0, abs=1e-9
        )
        assert pyo.value(m.unit.split_mass_balance[t].body) == pytest.approx(
            0.0, abs=1e-9
        )


@pytest.mark.unit
def test_ro_skid_renames_the_split_fraction_to_recovery():
    """``recovery`` replaces the inherited ``split_fraction`` — it is not an alias."""
    m = dummy_time_block(3)
    m.unit = ReverseOsmosisSkid(property_package=m.properties)

    assert pyo.value(m.unit.recovery) == pytest.approx(0.45)
    assert m.unit.find_component("split_fraction") is None
    with pytest.raises(ValueError, match="split_fraction"):
        m.rejected = ReverseOsmosisSkid(
            property_package=m.properties, split_fraction=0.45
        )


@pytest.mark.unit
def test_ro_skid_recovery_window_bounds_the_recovery():
    """``recovery_min``/``_max`` override the default seawater window."""
    m = dummy_time_block(3)
    m.unit = ReverseOsmosisSkid(
        property_package=m.properties,
        recovery=0.4,
        recovery_min=0.3,
        recovery_max=0.6,
    )

    assert m.unit.recovery.bounds == (0.3, 0.6)
    assert m.unit.recovery.fixed
    assert pyo.value(m.unit.recovery) == pytest.approx(0.4)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("options", "field"),
    [
        ({"recovery": 0.9, "recovery_max": 0.6}, "recovery"),
        ({"recovery_min": 0.7, "recovery_max": 0.6}, "recovery_min"),
    ],
)
def test_ro_skid_rejects_an_unusable_recovery_window(options, field):
    """An inverted window, or a setpoint outside it, is rejected by name."""
    m = dummy_time_block(3)
    with pytest.raises(FlexConfigError) as excinfo:
        m.unit = ReverseOsmosisSkid(property_package=m.properties, **options)
    assert excinfo.value.field == field
