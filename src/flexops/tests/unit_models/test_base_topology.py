"""SIDOBlock/DIDOBlock topology bases: ports and mass-balance bodies (§3.4)."""

import pyomo.environ as pyo
import pytest
from pyomo.network import Port

from flexops.testing import dummy_time_block
from flexops.unit_models.base import DIDOBlock, SIDOBlock, SISOBlock


@pytest.mark.unit
def test_sido_mass_balance_bodies():
    """1 inlet / 2 outlet ports; the split balance is satisfied by a hand split."""
    m = dummy_time_block(3)
    m.unit = SIDOBlock(property_package=m.properties, split_fraction=0.25)

    ports = {p.local_name for p in m.unit.component_objects(Port)}
    assert ports == {"inlet", "outlet_a", "outlet_b"}

    for t in m.time_block.time_index:
        m.unit.flow_in[t].fix(8.0)
        m.unit.flow_out_a[t].fix(2.0)
        m.unit.flow_out_b[t].fix(6.0)
        assert pyo.value(m.unit.split_mass_balance[t].body) == pytest.approx(
            8.0 - 2.0 - 6.0, abs=1e-9
        )
        assert pyo.value(m.unit.split_definition[t].body) == pytest.approx(
            2.0 - 0.25 * 8.0, abs=1e-9
        )


@pytest.mark.unit
def test_sido_split_fraction_is_a_fixed_physical_fraction():
    """The generic topology bounds the split only to a physical fraction."""
    m = dummy_time_block(3)
    m.unit = SIDOBlock(property_package=m.properties, split_fraction=0.4)

    assert m.unit.split_fraction.bounds == (0.0, 1.0)
    assert m.unit.split_fraction.fixed
    assert pyo.value(m.unit.split_fraction) == pytest.approx(0.4)


@pytest.mark.unit
def test_sido_takes_no_split_window_options():
    """A narrower window is a physical subclass's business, not the base's."""
    m = dummy_time_block(3)
    with pytest.raises(ValueError, match="split_fraction_min"):
        m.unit = SIDOBlock(property_package=m.properties, split_fraction_min=0.3)


@pytest.mark.unit
def test_dido_mass_balance_bodies():
    """2 inlet / 2 outlet ports; both coupled per-stream balances check by hand."""
    m = dummy_time_block(3)
    m.unit = DIDOBlock(property_package=m.properties, transfer_fraction=0.1)

    ports = {p.local_name for p in m.unit.component_objects(Port)}
    assert ports == {"inlet_a", "inlet_b", "outlet_a", "outlet_b"}

    for t in m.time_block.time_index:
        m.unit.flow_in_a[t].fix(10.0)
        m.unit.flow_in_b[t].fix(4.0)
        m.unit.flow_out_a[t].fix(9.0)
        m.unit.flow_out_b[t].fix(5.0)
        assert pyo.value(m.unit.mass_balance_a[t].body) == pytest.approx(
            9.0 - (10.0 - 0.1 * 10.0), abs=1e-9
        )
        assert pyo.value(m.unit.mass_balance_b[t].body) == pytest.approx(
            5.0 - (4.0 + 0.1 * 10.0), abs=1e-9
        )


@pytest.mark.unit
def test_component_names_default_to_the_topology_vocabulary():
    """Passing no override leaves each base's generic role names in place."""
    m = dummy_time_block(3)
    m.siso = SISOBlock(property_package=m.properties)
    m.sido = SIDOBlock(property_package=m.properties)
    m.dido = DIDOBlock(property_package=m.properties)

    assert {"flow_in", "flow_out"} <= set(m.siso.config.component_names)
    for name in ("flow_in", "flow_out"):
        assert m.siso.find_component(name) is not None
    for name in ("flow_in", "flow_out_a", "flow_out_b", "split_fraction"):
        assert m.sido.find_component(name) is not None
    for name in ("flow_in_a", "flow_in_b", "flow_out_a", "flow_out_b"):
        assert m.dido.find_component(name) is not None


@pytest.mark.unit
def test_component_names_override_renames_a_subset_at_build_time():
    """A partial override renames only the named roles; the rest keep defaults."""
    m = dummy_time_block(3)
    m.unit = SIDOBlock(
        property_package=m.properties,
        component_names={"flow_in": "raw", "split_fraction": "recovery"},
    )

    for name in ("raw", "recovery", "flow_out_a", "flow_out_b"):
        assert m.unit.find_component(name) is not None
    assert m.unit.find_component("flow_in") is None
    assert m.unit.find_component("split_fraction") is None

    # Ports are never renamed by component_names.
    ports = {p.local_name for p in m.unit.component_objects(Port)}
    assert ports == {"inlet", "outlet_a", "outlet_b"}


@pytest.mark.unit
def test_component_names_rejects_an_unknown_role():
    """An unknown role key is a config error, not a silently ignored entry."""
    m = dummy_time_block(3)
    with pytest.raises(ValueError, match="bogus_role"):
        m.unit = SISOBlock(
            property_package=m.properties,
            component_names={"bogus_role": "whatever"},
        )


@pytest.mark.unit
def test_component_names_rejects_a_non_mapping():
    """component_names must be a mapping of role to component name."""
    m = dummy_time_block(3)
    with pytest.raises(ValueError, match="component_names must be a mapping"):
        m.unit = SISOBlock(
            property_package=m.properties, component_names=["flow_in", "flow_out"]
        )
