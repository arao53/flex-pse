"""Continuous set-point dwell (fixed-grid piecewise-constant hold) tests (M08, §3.5)."""

import pyomo.environ as pyo
import pytest

from flexcore.exceptions import FlexConfigError
from flexops.logic import add_dwell
from flexops.logic.status import RollingStateKind
from flexops.testing import dummy_time_block
from flexops.unit_models import Pump

_N = 6


def _con_satisfied(condata, tol: float = 1e-9) -> bool:
    """Whether a constraint body lies within its (lower, upper) bounds."""
    body = pyo.value(condata.body)
    lower, upper = condata.lower, condata.upper
    ok = True
    if lower is not None:
        ok = ok and pyo.value(lower) <= body + tol
    if upper is not None:
        ok = ok and body <= pyo.value(upper) + tol
    return ok


def _unit_with_var():
    m = dummy_time_block(_N)
    m.unit = Pump(property_package=m.properties)
    return m, m.unit, m.unit.power_electrical


@pytest.mark.unit
@pytest.mark.parametrize("k", [2, 3, 4])
def test_dwell_holds_within_block_breaks_at_boundary(k):
    """Within each k-step block the var is constant; a mid-block change violates it."""
    m, unit, var = _unit_with_var()
    constraint = add_dwell(var, k)

    for t in range(_N):
        var[t].set_value(1.0)
    assert all(_con_satisfied(c) for c in constraint.values())

    # t=1 is never a block boundary (1 % k != 0 for every k >= 2).
    var[1].set_value(2.0)
    assert not _con_satisfied(constraint[1])


@pytest.mark.unit
@pytest.mark.parametrize("k", [2, 3, 4])
def test_dwell_index_excludes_block_boundaries(k):
    """The constraint excludes multiples of k; block starts may change freely."""
    m, unit, var = _unit_with_var()
    constraint = add_dwell(var, k)
    expected = {t for t in range(_N) if t % k != 0}
    assert set(constraint.index_set()) == expected


@pytest.mark.unit
def test_dwell_k_equals_one_is_a_no_op():
    """k=1 builds nothing and returns None -- every point is its own block."""
    m, unit, var = _unit_with_var()
    result = add_dwell(var, 1)
    assert result is None
    assert unit.find_component(f"{var.local_name}_dwell") is None
    assert getattr(unit, "_flexops_rolling_state", []) == []


@pytest.mark.unit
@pytest.mark.parametrize("k", [0, -1, -5])
def test_dwell_bad_k_raises(k):
    """k < 1 raises FlexConfigError."""
    m, unit, var = _unit_with_var()
    with pytest.raises(FlexConfigError):
        add_dwell(var, k)


@pytest.mark.unit
def test_dwell_multiple_vars_no_name_collision():
    """add_dwell on two different Vars on the same unit avoids name collisions."""
    m, unit, var = _unit_with_var()
    unit.other_var = pyo.Var(m.time_block.time_index, initialize=1.0)

    c1 = add_dwell(var, 2)
    c2 = add_dwell(unit.other_var, 2)

    assert c1 is not c2
    assert unit.find_component(f"{var.local_name}_dwell") is c1
    assert unit.find_component("other_var_dwell") is c2


@pytest.mark.unit
def test_dwell_registers_rolling_state():
    """A k>1 call registers the Var for rolling-horizon carry-over."""
    m, unit, var = _unit_with_var()
    add_dwell(var, 3)

    entries = unit._flexops_rolling_state
    assert len(entries) == 1
    assert entries[0] == {"var": var, "k": 3, "kind": RollingStateKind.DWELL}
