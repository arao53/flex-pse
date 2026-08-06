"""Model-level parallel-train degeneracy detection + symmetry breaking (M08, R8).

Symmetry among identical parallel trains (pumps, batteries, ...) creates
solver-time degeneracy: many equal-objective MILP solutions differ only in
*which* interchangeable unit is on. A unit cannot see its siblings, so this is
implemented **outside the unit level**, as a diagnostic/transform pass invoked
explicitly by a caller over a ``PlantBlock``/``NetworkBlock`` (a bare
``pyo.Block`` in v0, since ``PlantBlock`` itself lands in M09) -- never inside
``build()`` (pitfall 8).

**v0 interchangeability predicate** (documented choice): two units are
interchangeable if they are the same class and every config entry that is a
plain scalar (``int``/``float``/``str``/``bool``), an enum, a pydantic sub-config
(``UnitCommitmentConfig``, ``ExternalDispatchSpec``), or a Pyomo units-carrying
numeric quantity (e.g. ``capacity``, ``power_charge_max``) compares equal
(numeric quantities are compared by converted value, not object identity).
Object-reference config entries (``property_package``, ``costing_package``,
and similar Pyomo components/blocks) are **not** compared -- sibling trains
typically share the same property/costing package instance, and comparing
Pyomo component objects with ``==`` would build a constraint expression rather
than a boolean. "Same connectivity" (the milestone's smallest v0 form) is
approximated as: the same set of named Ports.
"""

import enum
import math
from typing import Any

import pyomo.environ as pyo
from pydantic import BaseModel
from pyomo.environ import units as pyunits
from pyomo.network import Port

from flexops.core.ops_block import OpsBlockData

_SKIPPED_CONFIG_KEYS = {
    "property_package",
    "costing_package",
    "flexops_config",
    "dynamic",
    "has_holdup",
}


def _config_value_matches(a: Any, b: Any) -> bool:
    """Whether two config values are equal under the v0 interchangeability predicate.

    Plain scalars/enums/pydantic sub-configs compare with ``==``. Pyomo
    units-carrying numeric quantities compare by converted value. Anything else
    (Pyomo components/blocks held by reference) is treated as non-disqualifying
    -- sibling trains legitimately share the same property/costing package.
    """
    if a is None or b is None:
        return a is b
    if isinstance(a, (int, float, str, bool)):
        return type(a) is type(b) and a == b
    if isinstance(a, enum.Enum):
        return a == b
    if isinstance(a, BaseModel):  # UnitCommitmentConfig, ExternalDispatchSpec, ...
        return type(a) is type(b) and a == b
    try:
        units_a = pyunits.get_units(a)
        val_b = pyo.value(pyunits.convert(b, units_a))
        return math.isclose(pyo.value(a), val_b, rel_tol=1e-9, abs_tol=1e-12)
    except (TypeError, ValueError, AttributeError):
        return True  # not a comparable scalar/quantity -> don't disqualify


def _port_names(unit: Any) -> set[str]:
    """Names of every Port a unit exposes ("same connectivity", v0 smallest form)."""
    return {c.local_name for c in unit.component_objects(Port, descend_into=False)}


def _unit_class(unit: Any) -> type:
    """The unit's stable "Data" class (e.g. ``PumpData``), for identity comparison.

    ``type(unit)`` is unusable here: ``declare_process_block_class`` mints a
    fresh synthetic ``_ScalarPump``-style wrapper class **per instance**, so
    two separately-built, otherwise-identical ``Pump`` instances never share
    ``type(a) is type(b)``. The module-level ``*Data`` class one MRO step up
    (``PumpData``, ``TankData``, ...) is defined once and shared by
    every instance, so it is the correct class identity to compare.
    """
    return type(unit).__mro__[1]


def _interchangeable(a: Any, b: Any) -> bool:
    """Whether two units are interchangeable under the v0 predicate."""
    if _unit_class(a) is not _unit_class(b):
        return False
    if _port_names(a) != _port_names(b):
        return False
    a_keys = set(a.config.keys()) - _SKIPPED_CONFIG_KEYS
    b_keys = set(b.config.keys()) - _SKIPPED_CONFIG_KEYS
    if a_keys != b_keys:
        return False
    return all(_config_value_matches(a.config[key], b.config[key]) for key in a_keys)


def _child_units(block: Any) -> list[Any]:
    """Every ``OpsBlockData`` instance under ``block``, in declaration order."""
    seen: set[int] = set()
    units = []
    for data in block.component_data_objects(pyo.Block, descend_into=True):
        if isinstance(data, OpsBlockData) and id(data) not in seen:
            seen.add(id(data))
            units.append(data)
    return units


def detect_parallel_trains(block: Any) -> list[list[Any]]:
    """Group ``block``'s child units into interchangeable equivalence classes.

    Walks every unit under ``block`` and groups units pairwise interchangeable
    under the v0 predicate (module docstring) into classes of size >= 2;
    singleton units are not degenerate and are omitted.

    Args:
        block: A ``PlantBlock``/``NetworkBlock`` (or, pre-M09, any ``pyo.Block``)
            whose child units to inspect.

    Returns:
        A list of groups (each a list of >= 2 interchangeable units), in
        declaration order.
    """
    units = _child_units(block)
    assigned: set[int] = set()
    groups: list[list[Any]] = []
    for i, unit in enumerate(units):
        if id(unit) in assigned:
            continue
        group = [unit]
        for other in units[i + 1 :]:
            if id(other) not in assigned and _interchangeable(unit, other):
                group.append(other)
                assigned.add(id(other))
        if len(group) >= 2:
            assigned.add(id(unit))
            groups.append(group)
    return groups


def break_parallel_symmetry(block: Any) -> int:
    """Add canonical-ordering constraints for every interchangeable group.

    Calls :func:`detect_parallel_trains` and, for each group of >= 2
    interchangeable units ``[u0, u1, ...]``, adds ``u_i.status[t] >=
    u_{i+1}.status[t]`` for every consecutive pair and every ``t`` (a train may
    not be on unless its predecessor is). The constraints are attached to
    ``block`` itself as ``train_ordering`` -- never to a single unit (R8,
    pitfall 8). A no-op (returns 0, adds no component) if no group has size
    >= 2.

    Args:
        block: The ``PlantBlock``/``NetworkBlock`` (or bare ``pyo.Block``) to
            scan and attach ordering constraints to.

    Returns:
        The number of ordering constraints added.
    """
    groups = detect_parallel_trains(block)
    pairs: dict[tuple[int, int, int], tuple[Any, Any]] = {}
    for g_idx, group in enumerate(groups):
        tb = group[0]._find_time_block()
        for pos in range(len(group) - 1):
            for t in tb.time_index:
                pairs[(g_idx, pos, t)] = (group[pos], group[pos + 1])

    if not pairs:
        return 0

    def _rule(_b, g_idx, pos, t):
        upper, lower = pairs[(g_idx, pos, t)]
        return upper.status[t] >= lower.status[t]

    block.train_ordering = pyo.Constraint(
        list(pairs),
        rule=_rule,
        doc="Canonical ordering among interchangeable parallel trains: "
        "train[i].status[t] >= train[i+1].status[t] (a train may not be on "
        "unless its predecessor is).",
    )
    return len(pairs)
