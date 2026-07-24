"""Conditional (if-x-then-y) implication constraint-body tests (M08, §3.5)."""

import pyomo.environ as pyo
import pytest

from flexcore.exceptions import FlexConfigError
from flexops.logic import add_conditional, add_status
from flexops.testing import dummy_time_block
from flexops.unit_models import Pump


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


def _two_units_with_status(n: int = 4):
    m = dummy_time_block(n)
    m.x = Pump(property_package=m.properties)
    m.y = Pump(property_package=m.properties)
    x_status = add_status(m.x, m.x.power_electrical, 0.0, 100.0)
    y_status = add_status(m.y, m.y.power_electrical, 0.0, 100.0)
    return m, x_status, y_status


@pytest.mark.unit
def test_conditional_on_implication_bodies():
    """then='on': y.status[t] >= x.status[t]; violated only at x=1,y=0."""
    m, x_status, y_status = _two_units_with_status()
    add_conditional(m.x, m.y, then="on")

    for x_val, y_val, expect_ok in [
        (0, 0, True),
        (0, 1, True),
        (1, 1, True),
        (1, 0, False),
    ]:
        x_status[0].set_value(x_val)
        y_status[0].set_value(y_val)
        assert _con_satisfied(m.x.conditional[0]) == expect_ok


@pytest.mark.unit
def test_conditional_off_implication_bodies():
    """then='off': y.status[t] <= 1 - x.status[t]; violated only at x=1,y=1."""
    m, x_status, y_status = _two_units_with_status()
    add_conditional(m.x, m.y, then="off")

    for x_val, y_val, expect_ok in [
        (0, 0, True),
        (0, 1, True),
        (1, 0, True),
        (1, 1, False),
    ]:
        x_status[0].set_value(x_val)
        y_status[0].set_value(y_val)
        assert _con_satisfied(m.x.conditional[0]) == expect_ok


@pytest.mark.unit
def test_conditional_bad_args_raise():
    """Missing status or a bad `then` raises FlexConfigError."""
    m, x_status, y_status = _two_units_with_status()

    with pytest.raises(FlexConfigError):
        add_conditional(m.x, m.y, then="sideways")

    m2 = dummy_time_block(4)
    m2.x = Pump(property_package=m2.properties)  # no status attached
    m2.y = Pump(property_package=m2.properties)
    add_status(m2.y, m2.y.power_electrical, 0.0, 100.0)
    with pytest.raises(FlexConfigError):
        add_conditional(m2.x, m2.y, then="on")
