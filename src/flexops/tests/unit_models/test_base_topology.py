"""SIDOBlock/DIDOBlock topology bases: ports and mass-balance bodies (§3.4)."""

import pyomo.environ as pyo
import pytest
from pyomo.network import Port

from flexops.testing import dummy_time_block
from flexops.unit_models.base import DIDOBlock, SIDOBlock


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
