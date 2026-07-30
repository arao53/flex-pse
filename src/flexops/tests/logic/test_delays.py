"""Startup-delay (upstream-linked) constraint-body tests (M08, §3.5)."""

import pyomo.environ as pyo
import pytest

from flexcore.exceptions import FlexConfigError
from flexops.logic import add_startup_delay, add_status
from flexops.logic.status import RollingStateKind
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


@pytest.mark.unit
def test_startup_delay_blocks_early_start():
    """Downstream cannot be on until k steps after the upstream unit's status."""
    k = 2
    m = dummy_time_block(6)
    m.up = Pump(property_package=m.properties)
    m.down = Pump(property_package=m.properties)
    up_status = add_status(m.up, m.up.power_electrical, 0.0, 100.0)
    down_status = add_status(m.down, m.down.power_electrical, 0.0, 100.0)
    add_startup_delay(m.down, m.up, k)

    # Upstream: off for steps 0-2, on from step 3 onward.
    for t, val in enumerate((0, 0, 0, 1, 1, 1)):
        up_status[t].set_value(val)

    # t < k: down.status[t] == 0 is the only feasible value.
    down_status[0].set_value(0)
    assert _con_satisfied(m.down.startup_delay[0])
    down_status[0].set_value(1)
    assert not _con_satisfied(m.down.startup_delay[0])

    down_status[1].set_value(0)
    assert _con_satisfied(m.down.startup_delay[1])

    # t=2: up.status[t-k] = up.status[0] = 0 -> down cannot be on yet.
    down_status[2].set_value(1)
    assert not _con_satisfied(m.down.startup_delay[2])
    down_status[2].set_value(0)
    assert _con_satisfied(m.down.startup_delay[2])

    # t=5: up.status[t-k] = up.status[3] = 1 -> down may now be on.
    down_status[5].set_value(1)
    assert _con_satisfied(m.down.startup_delay[5])


@pytest.mark.unit
def test_startup_delay_requires_upstream_status():
    """An upstream unit with no status Var raises FlexConfigError."""
    m = dummy_time_block(4)
    m.up = Pump(property_package=m.properties)  # no add_status call
    m.down = Pump(property_package=m.properties)
    add_status(m.down, m.down.power_electrical, 0.0, 100.0)

    with pytest.raises(FlexConfigError):
        add_startup_delay(m.down, m.up, 2)


@pytest.mark.unit
def test_startup_delay_registers_rolling_state():
    """add_startup_delay registers downstream status for rolling-horizon carry-over."""
    k = 2
    m = dummy_time_block(6)
    m.up = Pump(property_package=m.properties)
    m.down = Pump(property_package=m.properties)
    add_status(m.up, m.up.power_electrical, 0.0, 100.0)
    down_status = add_status(m.down, m.down.power_electrical, 0.0, 100.0)
    add_startup_delay(m.down, m.up, k)

    entries = m.down._flexops_rolling_state
    assert len(entries) == 1
    assert entries[0] == {
        "var": down_status,
        "k": k,
        "kind": RollingStateKind.STARTUP_DELAY,
    }
