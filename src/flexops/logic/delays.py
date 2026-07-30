"""Optional UC piece: startup/response delay tied to an upstream unit (M08, §3.5).

The single-hop primitive behind the chemical-stabilization delay *chains* of
Rao et al. 2024 (full chain templates are post-v0, PLAN.md §4): a unit may not
start (or be on) until ``k`` steps after an upstream unit's status.
"""

from typing import Any

import pyomo.environ as pyo

from flexcore.exceptions import FlexConfigError
from flexops.logic.status import RollingStateKind, _register_rolling_state


def add_startup_delay(unit: Any, upstream: Any, k: int) -> pyo.Constraint:
    """Tie ``unit``'s on/off status to an upstream unit's status, delayed by ``k``.

    For each time point ``t``:

    * ``t < k``: ``unit.status[t] == 0`` (cannot be on before the delay has
      elapsed at all).
    * ``t >= k``: ``unit.status[t] <= upstream.status[t - k]`` (cannot be on
      unless the upstream unit was on ``k`` steps earlier).

    Args:
        unit: The downstream unit block; must already carry a ``status`` Var
            (via :func:`~flexops.logic.status.add_status`).
        upstream: The upstream unit block whose ``status`` Var gates ``unit``.
        k: Number of steps the downstream start is delayed behind the upstream.

    Registers ``unit``'s ``status`` Var as rolling-horizon state (trailing
    ``k`` steps) via
    :func:`~flexops.logic.status._register_rolling_state` -- consumption is
    M12's job, not built here.

    Returns:
        The attached ``startup_delay`` Constraint.

    Raises:
        FlexConfigError: If ``upstream`` has no ``status`` Var.
    """
    if not hasattr(upstream, "status"):
        raise FlexConfigError(
            f"add_startup_delay requires the upstream unit {upstream.name!r} to "
            "carry a status Var; attach one via add_status first.",
            field="upstream",
            value=upstream.name,
        )
    down_status = unit.status
    up_status = upstream.status
    tb = unit._find_time_block()

    @unit.Constraint(
        tb.time_index,
        doc=f"Startup delay ({k} steps behind {upstream.name}): status[t]==0 "
        "for t<k; status[t] <= upstream.status[t-k] for t>=k.",
    )
    def startup_delay(_b, t):
        if t < k:
            return down_status[t] == 0
        return down_status[t] <= up_status[t - k]

    _register_rolling_state(unit, down_status, k, RollingStateKind.STARTUP_DELAY)
    return unit.startup_delay
