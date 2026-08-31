"""Predefined surrogate-structure classes (architecture §3.4/§5).

Every relationship a unit's energy draw (or any other registered relation,
see :meth:`~flexops.core.ops_block.OpsBlockData.register_relation`) can be
swapped to is one of these classes: it validates its
:class:`~flexcore.config.schema.SurrogateSpec`'s ``data`` mapping in
``__init__`` and builds the Pyomo objects in :meth:`~base.Surrogate.build`. A
leaf subpackage of ``flexops`` (imports only ``flexcore`` and Pyomo), so
``flexops.core.ops_block`` can import :data:`SURROGATES` with no cross-package
import -- this is what lets a config's surrogate be realized at unit
construction time (``build_model``), with no ``flexparameterize`` import
anywhere in ``flexops``.

Only :class:`~multilinear.MultilinearSurrogate` is implemented; the rest raise
``NotImplementedError`` at construction, naming it as the implemented
alternative. ``SurrogateType.CONSTANT_INTENSITY`` has no class here at all --
it fixes a process parameter rather than swapping a Constraint, so
``flexparameterize.apply.apply_to_model`` handles it directly.
"""

from flexcore.config.schema import SurrogateSpec, SurrogateType
from flexcore.exceptions import FlexConfigError
from flexops.surrogates.arima import ArimaSurrogate
from flexops.surrogates.base import Surrogate
from flexops.surrogates.exponential import ExponentialSurrogate
from flexops.surrogates.multilinear import MultilinearSurrogate
from flexops.surrogates.neural_network import NeuralNetworkSurrogate
from flexops.surrogates.quadratic import QuadraticSurrogate

SURROGATES: dict[SurrogateType, type[Surrogate]] = {
    SurrogateType.MULTILINEAR: MultilinearSurrogate,
    SurrogateType.QUADRATIC: QuadraticSurrogate,
    SurrogateType.EXPONENTIAL: ExponentialSurrogate,
    SurrogateType.ARIMA: ArimaSurrogate,
    SurrogateType.NEURAL_NETWORK: NeuralNetworkSurrogate,
}
"""dict: SurrogateType -> the class that implements it. The extension point
for a new relationship shape: add the member to
:class:`~flexcore.config.schema.SurrogateType`, a class here, and an entry.
Deliberately excludes ``SurrogateType.CONSTANT_INTENSITY`` (see module
docstring)."""


def surrogate_from_spec(spec: SurrogateSpec) -> Surrogate:
    """Construct and validate the surrogate ``spec`` names.

    Args:
        spec: The :class:`~flexcore.config.schema.SurrogateSpec` to realize.

    Returns:
        The constructed, validated :class:`~base.Surrogate`.

    Raises:
        FlexConfigError: If ``spec.surrogate_type`` is
            ``SurrogateType.CONSTANT_INTENSITY`` (which has no class; see
            module docstring) or is otherwise not in :data:`SURROGATES` (not
            reachable through the enum today, but guarded for a future
            member added without a registry entry).
        NotImplementedError: If the named class is not yet implemented.
    """
    if spec.surrogate_type is SurrogateType.CONSTANT_INTENSITY:
        raise FlexConfigError(
            "'constant_intensity' has no surrogate class: it fixes a "
            "process parameter rather than swapping a Constraint. Fix the "
            "parameter directly instead of calling surrogate_from_spec.",
            field="surrogate_type",
            value=spec.surrogate_type,
        )
    surrogate_class = SURROGATES.get(spec.surrogate_type)
    if surrogate_class is None:
        known = ", ".join(repr(name.value) for name in SURROGATES)
        raise FlexConfigError(
            f"No surrogate class is registered for surrogate_type "
            f"{spec.surrogate_type!r}. Known: {known}.",
            field="surrogate_type",
            value=spec.surrogate_type,
        )
    return surrogate_class(spec.data)


__all__ = [
    "SURROGATES",
    "ArimaSurrogate",
    "ExponentialSurrogate",
    "MultilinearSurrogate",
    "NeuralNetworkSurrogate",
    "QuadraticSurrogate",
    "Surrogate",
    "surrogate_from_spec",
]
