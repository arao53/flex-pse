"""Customizable unit-commitment layer (M08, architecture §3.5).

A composable set of optional constraint pieces applied per unit via its
``unit_commitment`` config: :func:`add_status` is the base (present whenever a
unit can be shut off); :func:`add_startup_shutdown` (transition logic plus
built-in minimum uptime/downtime, via its ``min_uptime``/``min_downtime``
args), :func:`add_dwell`, :func:`add_startup_delay`, :func:`add_conditional`,
and :func:`add_bypass` are all optional. :func:`detect_parallel_trains`/
:func:`break_parallel_symmetry` are **model-level** (over a PlantBlock/
NetworkBlock, never a per-unit method -- R8).

Note: :func:`add_dwell` is a distinct, unrelated concept from
``add_startup_shutdown``'s minimum uptime/downtime -- it holds a **continuous**
process variable steady, not a unit's on/off status. Any piece that creates
state needing rolling-horizon carry-over (minimum uptime/downtime, dwell, or a
startup delay) registers it via
:func:`~flexops.logic.status._register_rolling_state`; M12 is what will later
consume that registry.
"""

from flexops.logic.bypass import add_bypass
from flexops.logic.conditional import add_conditional
from flexops.logic.degeneracy import break_parallel_symmetry, detect_parallel_trains
from flexops.logic.delays import add_startup_delay
from flexops.logic.dwell import add_dwell
from flexops.logic.status import add_status, relax, unrelax
from flexops.logic.unit_commitment import add_startup_shutdown

__all__ = [
    "add_status",
    "relax",
    "unrelax",
    "add_startup_shutdown",
    "add_dwell",
    "add_startup_delay",
    "add_conditional",
    "detect_parallel_trains",
    "break_parallel_symmetry",
    "add_bypass",
]
