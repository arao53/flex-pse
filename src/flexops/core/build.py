"""build_model: construct a whole flex-pse model from one config.

The config-driven entry point. A single validated
:class:`~flexcore.config.schema.ModelConfig` yields the whole model by running
the ordered stages in :mod:`flexops.core.stages`. The frozen-API fixtures
``api_freeze.py`` and ``api_freeze_config.json`` (under
``flexops/tests/fixtures/api_freeze/``) are the same model built each way.

**Units in a persisted config are data, not code.** A units-carrying quantity
is written as ``{"value": 15, "units": "min"}`` (or, for the time step, the
string ``"15 min"``) and :func:`parse_quantity` turns it into a Pyomo
expression at build time.
"""

import pyomo.environ as pyo

from flexcore.config.io import load_model_config
from flexcore.config.schema import ModelConfig
from flexops.core.stages import (
    STAGE_FUNCTIONS,
    STAGES,
    BuildContext,
    _apply_external_dispatch,
    _build_arcs,
    parse_quantity,
)
from flexops.core.units import parse_units

__all__ = [
    "_apply_external_dispatch",
    "_build_arcs",
    "build_model",
    "parse_quantity",
    "parse_units",
]


def build_model(config, *, expand_arcs: bool = False) -> pyo.ConcreteModel:
    """Build the whole Pyomo model described by a config.

    Args:
        config: A :class:`~flexcore.config.schema.ModelConfig`, or a path or
            mapping round-tripped through
            :func:`~flexcore.config.io.load_model_config` first (never used
            raw -- anything a user configures must go through the validated
            schema, not an ad hoc dict).
        expand_arcs: Whether to apply ``network.expand_arcs`` after the
            topology is built.

    Returns:
        The constructed ``ConcreteModel``, carrying ``time_block``, the named
        property packages, ``costing``, the plant or network tree, and
        ``objective``. Arcs are expanded only if ``expand_arcs`` is true.

    Raises:
        FlexConfigError: If the config fails validation (the message names the
            offending field path, and ``__cause__`` is the underlying pydantic
            ``ValidationError``), or names an unknown unit-model class.
    """
    cfg = config if isinstance(config, ModelConfig) else load_model_config(config)
    model = pyo.ConcreteModel(name=(cfg.network or cfg.plant).name)
    ctx = BuildContext(base_dir=cfg._base_dir, expand_arcs=expand_arcs, units={})
    for name in STAGES:
        STAGE_FUNCTIONS[name](model, cfg, ctx)
    model._flex_build_context = ctx  # used by apply_stages
    return model
