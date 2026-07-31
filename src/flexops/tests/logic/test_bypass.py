"""Bypass-stream constraint-body tests (M08, §3.5)."""

import pyomo.environ as pyo
import pytest
from pyomo.environ import units as pyunits

from flexops.logic import add_bypass
from flexops.testing import dummy_time_block
from flexops.unit_models import Pump


@pytest.mark.unit
def test_bypass_constraint_bodies():
    """treated_flow == flow_var - bypass_flow, checked by hand at fixed values."""
    m = dummy_time_block(3)
    m.unit = Pump(property_package=m.properties)
    flow_var = pyo.Reference(m.unit.inlet_state.flow_vol_phase[:, "Liq"])
    bypass_max = 20.0 * pyunits.m**3 / pyunits.hr

    add_bypass(m.unit, flow_var, bypass_max)

    assert m.unit.bypass_flow[0].lb == pytest.approx(0.0)
    assert m.unit.bypass_flow[0].ub == pytest.approx(20.0)

    # Fix flow=100, bypass at its bound (20) -> treated_flow=80.
    flow_var[0].set_value(100.0)
    m.unit.bypass_flow[0].set_value(20.0)
    m.unit.treated_flow[0].set_value(80.0)
    assert pyo.value(m.unit.treated_flow_eq[0].body) == pytest.approx(0.0, abs=1e-9)

    # Fix flow=100, bypass=0 -> treated_flow=100.
    m.unit.bypass_flow[1].set_value(0.0)
    m.unit.treated_flow[1].set_value(100.0)
    flow_var[1].set_value(100.0)
    assert pyo.value(m.unit.treated_flow_eq[1].body) == pytest.approx(0.0, abs=1e-9)

    # A mismatched treated_flow makes the body nonzero (sign is Pyomo's
    # internal normalization choice; only the magnitude matters here).
    m.unit.treated_flow[2].set_value(999.0)
    flow_var[2].set_value(100.0)
    m.unit.bypass_flow[2].set_value(0.0)
    assert abs(pyo.value(m.unit.treated_flow_eq[2].body)) == pytest.approx(
        899.0, abs=1e-6
    )
