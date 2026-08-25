"""Manual parallel-train degeneracy handling: hierarchy over a declared group.

Symmetry among identical parallel trains (RO skids, pumps, batteries, ...)
creates solver-time degeneracy: many equal-objective solutions differ only in
*which* interchangeable unit is on, or in how a shared duty is split between
them. A unit cannot see its siblings, so this is handled **outside the unit
level**, as a transform a caller applies explicitly to a group of units it has
declared -- never inside a unit's ``build()``.

:func:`register_parallel_group` is that transform. The caller lists the group
in priority order and it is ordered **descending** along that list::

    group[0].status[t] >= group[1].status[t] >= ... >= group[-1].status[t]

so a train may not run unless its predecessor runs, and the first-listed unit
is the first one on. The same ordering is applied to any continuous Vars the
caller names, which is what breaks the degeneracy in a split duty (three RO
trains free to trade recovery among themselves have many equal-cost splits;
one canonical ordering among them does not).
"""

from collections.abc import Sequence
from typing import Any

import pyomo.environ as pyo

from flexcore.exceptions import FlexConfigError
from flexops.logic.conditional import add_conditional


def _ordered_variable(unit: Any, name: str) -> Any:
    """Resolve ``name`` on ``unit`` as a Var to be ordered across a group.

    Args:
        unit: The unit to resolve the name on.
        name: Local component name of the Var, as that unit exposes it.

    Returns:
        The resolved Var component (possibly a ``pyo.Reference`` to one).

    Raises:
        FlexConfigError: If the name does not resolve on ``unit``, or resolves
            to something other than a Var.
    """
    var = unit.find_component(name)
    if var is None:
        raise FlexConfigError(
            f"register_parallel_group found no component named {name!r} on "
            f"unit {unit.name!r}; every ordered variable must exist on every "
            "unit in the group. A unit that renames its roles exposes the "
            "renamed name (ReverseOsmosis carries 'recovery', not "
            "'split_fraction').",
            field="variables",
            value=name,
        )
    if var.ctype is not pyo.Var:
        raise FlexConfigError(
            f"register_parallel_group orders Vars only, but {name!r} on unit "
            f"{unit.name!r} is a {var.ctype.__name__}; pass the name of a Var.",
            field="variables",
            value=name,
        )
    return var


def _attach_variable_ordering(unit: Any, upper: Any, lower: Any, name: str) -> None:
    """Attach ``upper >= lower`` to ``unit`` as ``{name}_ordering``.

    Args:
        unit: The unit to attach the Constraint to (the *later* unit of the
            pair, so it lands beside that pair's ``conditional``).
        upper: The predecessor unit's Var (the >= side).
        lower: The later unit's Var (the <= side).
        name: Local component name shared by both Vars; names the Constraint.
    """
    doc = (
        f"Canonical ordering among parallel units: the predecessor's {name} "
        f"is >= this unit's {name}."
    )
    if upper.is_indexed():
        con = pyo.Constraint(
            list(upper.index_set()),
            rule=lambda _b, i: upper[i] >= lower[i],
            doc=doc,
        )
    else:
        con = pyo.Constraint(expr=upper >= lower, doc=doc)
    unit.add_component(f"{name}_ordering", con)


def register_parallel_group(
    units: list[Any], *, variables: Sequence[str] = ()
) -> list[pyo.Constraint]:
    """Declare units as a parallel/degenerate hierarchy group and order them.

    For units the caller knows to be interchangeable or otherwise
    hierarchically related — parallel skids, staged trains, a lead/lag pair.

    **Canonical direction.** ``units`` is given in priority order, lowest
    first, and the group is ordered *descending* along that list::

        units[0].status[t] >= units[1].status[t] >= ... >= units[-1].status[t]

    so a train may not run unless its predecessor runs and ``units[0]`` is the
    first one on. Implemented by chaining
    :func:`~flexops.logic.conditional.add_conditional` over every consecutive
    pair *back to front* (``add_conditional(units[i + 1], units[i])`` reads as
    "``units[i + 1]`` on implies ``units[i]`` on"), reusing its tested two-unit
    implication semantics rather than re-implementing them. Each pair's
    Constraint therefore lands on the **later** unit: ``units[0]`` carries no
    ``conditional``, ``units[-1]`` does.

    **Continuous variables.** Each name in ``variables`` is resolved on every
    unit and ordered the same way, ``units[i].<name> >= units[i + 1].<name>``,
    as a Constraint named ``{name}_ordering`` attached beside that pair's
    ``conditional``. Time-indexed Vars are ordered at every index; scalar Vars
    (a unit's regressable process parameters, e.g. ``ReverseOsmosis.recovery``)
    get a single Constraint. Such parameters are *fixed* at construction, so an
    ordering over them only binds once a caller or FlexParameterize unfixes
    them — until then it is a trivially satisfied constant.

    A group is always a hierarchy. For the unrelated "these two may not run
    together" relation, call
    :func:`~flexops.logic.conditional.add_conditional` with ``then="off"``
    directly on the pair.

    Args:
        units: >= 2 units, in the intended canonical order (highest priority
            first). Each must carry a ``status`` Var (see
            :func:`~flexops.logic.status.add_status`).
        variables: Local component names of Vars to order across the group in
            the same direction, as each unit exposes them (a renaming unit
            exposes the renamed name). Empty by default.

    Returns:
        The ``len(units) - 1`` attached ``conditional`` Constraints, one per
        consecutive pair, in order. Any ``variables`` Constraints are reachable
        on the units as ``{name}_ordering``.

    Raises:
        FlexConfigError: If fewer than two units are given, if a unit repeats
            in the list, if a variable name does not resolve to a Var on every
            unit or resolves to differently-indexed Vars across the group, or
            (raised by ``add_conditional``) if a unit lacks a ``status`` Var.
    """
    if len(units) < 2:
        raise FlexConfigError(
            f"register_parallel_group requires >= 2 units, got {len(units)}.",
            field="units",
            value=len(units),
        )
    if len({id(u) for u in units}) != len(units):
        raise FlexConfigError(
            "register_parallel_group requires distinct units; a unit was "
            "repeated in the list.",
            field="units",
            value=[u.name for u in units],
        )
    # Resolve and validate every ordered variable up front, so a bad name
    # cannot leave a half-built group behind.
    resolved = {name: [_ordered_variable(u, name) for u in units] for name in variables}
    for name, group_vars in resolved.items():
        if len({tuple(v.index_set()) for v in group_vars}) > 1:
            raise FlexConfigError(
                f"register_parallel_group requires {name!r} to carry the same "
                "index set on every unit in the group, but the units "
                f"{[u.name for u in units]} index it differently.",
                field="variables",
                value=name,
            )

    constraints = [
        add_conditional(units[i + 1], units[i], then="on")
        for i in range(len(units) - 1)
    ]
    for name, group_vars in resolved.items():
        for i in range(len(units) - 1):
            _attach_variable_ordering(
                units[i + 1], group_vars[i], group_vars[i + 1], name
            )
    return constraints
