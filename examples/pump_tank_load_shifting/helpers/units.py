"""Parse "<value> <units>" config strings into unit-carrying Pyomo quantities.

Every physical config field in this example is a plain string like
``"300 m**3/hr"`` (the same convention documented on
``flexcore.config.schema.TimeConfig.time_step``); this module is the one place
that turns such a string into the ``value * pyunits.<expr>`` object flexops
unit-model constructors expect.
"""

from pyomo.environ import units as pyunits

from flexcore.exceptions import FlexConfigError


class _UnitsNamespace(dict):
    """``eval()`` locals mapping: an unresolved name resolves via ``pyunits``."""

    def __missing__(self, key):
        return getattr(pyunits, key)


def parse_quantity(spec: str):
    """Parse a ``"<value> <units>"`` string into a Pyomo quantity.

    Args:
        spec: A number followed by a ``pyomo.environ.units`` expression, e.g.
            ``"0.5 kWh/m**3"`` or ``"1 hr"``.

    Returns:
        The quantity ``value * units_expression``.

    Raises:
        FlexConfigError: If ``spec`` is not ``"<value> <units>"``, or the
            units expression does not name valid ``pyomo.environ.units``
            attributes.
    """
    try:
        value_str, units_str = spec.split(" ", 1)
        value = float(value_str)
    except ValueError as exc:
        raise FlexConfigError(
            f"Expected '<value> <units>' (e.g. '300 m**3/hr'), got {spec!r}.",
            value=spec,
        ) from exc
    try:
        units = eval(units_str, {"__builtins__": {}}, _UnitsNamespace())
    except (AttributeError, NameError, SyntaxError) as exc:
        raise FlexConfigError(
            f"Unrecognized units {units_str!r} in quantity {spec!r}.",
            value=spec,
        ) from exc
    return value * units
