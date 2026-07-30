"""Constraint-body + relaxation tests for the UC status base (M08, §3.5)."""

import pyomo.environ as pyo
import pytest
from pyomo.environ import units as pyunits

from flexops.logic import add_status, relax, unrelax
from flexops.testing import dummy_time_block
from flexops.unit_models import Pump


def _con_satisfied(condata, tol: float = 1e-9) -> bool:
    """Whether a constraint body lies within its (lower, upper) bounds."""
    body = pyo.value(condata.body)
    lower = condata.lower
    upper = condata.upper
    ok = True
    if lower is not None:
        ok = ok and pyo.value(lower) <= body + tol
    if upper is not None:
        ok = ok and body <= pyo.value(upper) + tol
    return ok


def _pump_with_status(min_kw: float = 0.0, max_kw: float = 100.0):
    """Build a 4-step Pump and attach a status binary to its power draw."""
    m = dummy_time_block(4)
    m.unit = Pump(property_package=m.properties)
    status = add_status(
        m.unit, m.unit.power_electrical, min_kw * pyunits.kW, max_kw * pyunits.kW
    )
    return m, m.unit, status


@pytest.mark.unit
def test_add_status_returns_time_indexed_binary():
    """add_status attaches status[t] Binary over the unit's time points."""
    m, unit, status = _pump_with_status()
    assert status is unit.status
    assert status.is_indexed()
    assert set(status.index_set()) == set(m.time_block.time_index)
    for t in m.time_block.time_index:
        assert status[t].domain is pyo.Binary


@pytest.mark.unit
def test_add_status_constraint_bodies():
    """On/at-max is feasible; off-with-output and on-below-min are infeasible."""
    m, unit, status = _pump_with_status(min_kw=10.0, max_kw=100.0)
    power = unit.power_electrical

    # status=1, output=max -> both semicontinuous links satisfied.
    status[0].set_value(1)
    power[0].set_value(100.0)
    assert _con_satisfied(unit.status_min_link[0])
    assert _con_satisfied(unit.status_max_link[0])

    # status=0, output=5 (>0) -> upper link violated (off cannot produce).
    status[1].set_value(0)
    power[1].set_value(5.0)
    assert not _con_satisfied(unit.status_max_link[1])

    # status=1, output=5 (< min=10) -> lower link violated (>= min when on).
    status[2].set_value(1)
    power[2].set_value(5.0)
    assert not _con_satisfied(unit.status_min_link[2])


@pytest.mark.unit
def test_relax_unrelax_round_trip():
    """relax() flips only the tracked binaries' domain; unrelax() restores it."""
    m, unit, status = _pump_with_status()
    n_components = len(list(unit.component_objects()))
    n_constraints = len(list(unit.component_data_objects(pyo.Constraint)))

    assert all(status[t].domain is pyo.Binary for t in m.time_block.time_index)

    relax(unit)
    for t in m.time_block.time_index:
        assert status[t].domain is pyo.UnitInterval
    # Nothing else changed: no components or constraints added/removed.
    assert len(list(unit.component_objects())) == n_components
    assert len(list(unit.component_data_objects(pyo.Constraint))) == n_constraints

    unrelax(unit)
    for t in m.time_block.time_index:
        assert status[t].domain is pyo.Binary
