"""ReverseOsmosis: feed -> permeate + brine (§3.4, R6)."""

import pyomo.environ as pyo
import pytest

from flexcore.exceptions import FlexConfigError
from flexops.testing import UnitModelTestHarness, dummy_time_block
from flexops.unit_models import ReverseOsmosis


class TestReverseOsmosis(UnitModelTestHarness):
    """RO skid: feed -> permeate (outlet_a) + brine (outlet_b)."""

    expected_dof = 0

    def configure(self):
        m = dummy_time_block(3)
        m.unit = ReverseOsmosis(property_package=m.properties)
        return m, m.unit


@pytest.mark.unit
def test_reverseosmosis_outlet_semantics():
    """The skid's ``recovery`` is its permeate fraction of the feed."""
    m = dummy_time_block(3)
    m.unit = ReverseOsmosis(property_package=m.properties, recovery=0.45)
    for t in m.time_block.time_index:
        m.unit.feed[t].fix(4.0)
        m.unit.permeate[t].fix(1.8)
        m.unit.brine[t].fix(2.2)
        assert pyo.value(m.unit.split_definition[t].body) == pytest.approx(
            1.8 - 0.45 * 4.0, abs=1e-9
        )
        assert pyo.value(m.unit.split_mass_balance[t].body) == pytest.approx(
            0.0, abs=1e-9
        )


@pytest.mark.unit
def test_reverseosmosis_renames_the_split_fraction_to_recovery():
    """``recovery`` replaces the inherited ``split_fraction`` — it is not an alias."""
    m = dummy_time_block(3)
    m.unit = ReverseOsmosis(property_package=m.properties)

    assert pyo.value(m.unit.recovery) == pytest.approx(0.45)
    assert m.unit.find_component("split_fraction") is None
    with pytest.raises(ValueError, match="split_fraction"):
        m.rejected = ReverseOsmosis(property_package=m.properties, split_fraction=0.45)


@pytest.mark.unit
def test_reverseosmosis_renames_the_flows_to_feed_permeate_brine():
    """``feed``/``permeate``/``brine`` replace ``flow_in``/``flow_out_a``/``_b``."""
    m = dummy_time_block(3)
    m.unit = ReverseOsmosis(property_package=m.properties)

    assert m.unit.find_component("flow_in") is None
    assert m.unit.find_component("flow_out_a") is None
    assert m.unit.find_component("flow_out_b") is None
    for name in ("feed", "permeate", "brine"):
        assert m.unit.find_component(name) is not None

    # Ports are unaffected by the flow rename.
    for port in ("inlet", "outlet_a", "outlet_b"):
        assert m.unit.find_component(port) is not None


@pytest.mark.unit
def test_reverseosmosis_recovery_window_bounds_the_recovery():
    """``recovery_min``/``_max`` override the default seawater window."""
    m = dummy_time_block(3)
    m.unit = ReverseOsmosis(
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
def test_reverseosmosis_rejects_an_unusable_recovery_window(options, field):
    """An inverted window, or a setpoint outside it, is rejected by name."""
    m = dummy_time_block(3)
    with pytest.raises(FlexConfigError) as excinfo:
        m.unit = ReverseOsmosis(property_package=m.properties, **options)
    assert excinfo.value.field == field
