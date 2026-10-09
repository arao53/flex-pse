"""The ordered build stages build_model runs, and apply_stages for live models."""

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import pyomo.environ as pyo
from pyomo.network import Arc

from flexcore import nomenclature as nm
from flexcore.config.io import resolve_source_path
from flexcore.config.schema import (
    ModelConfig,
    PlantConfig,
    SurrogateSpec,
    SurrogateType,
    UnitConfig,
)
from flexcore.exceptions import FlexConfigError
from flexops.core.network_block import NetworkBlock
from flexops.core.ops_block import OpsBlockData
from flexops.core.plant_block import PlantBlock
from flexops.core.time_block import TimeBlock
from flexops.core.units import parse_units
from flexops.costing import FlexCosting
from flexops.properties import PROPERTY_PACKAGES
from flexops.surrogates import surrogate_from_spec

STAGES: tuple[str, ...] = (
    "declare",
    "topology",
    "surrogates",
    "degradation",
    "ramping",
    "logic",
    "state",
    "extensions",
    "costing",
)
"""Build order. Changing it is a schema-contract change (see CONTRIBUTING.md)."""

POST_TOPOLOGY_STAGES = STAGES[2:]
"""Stages that can run on an already-built model."""

ENERGY_RELATION = f"{nm.POWER_ELECTRICAL}_relation"
"""Name of the relation holding a unit's electrical energy relationship."""

INTENSITY_PARAMETER = nm.INTENSITY_VARS[nm.PowerKind.ELECTRICAL]
"""Name of the registered parameter a constant-intensity relationship fixes."""


@dataclass
class BuildContext:
    """What the build stages share.

    Attributes:
        base_dir: The config file's directory, for relative source paths.
        expand_arcs: Whether the topology stage expands arcs.
        units: Each unit's config path (``"plant.unit"``, or
            ``"network.plant.unit"`` for a network) mapped to its built block.
    """

    base_dir: Path | None
    expand_arcs: bool
    units: dict[str, OpsBlockData]


def parse_quantity(value, *, strict: bool = True):
    """Turn a persisted units-carrying value into a Pyomo expression.

    Args:
        value: Either a ``{"value": ..., "units": ...}`` mapping, a
            ``"<number> <units>"`` string (the form ``TimeConfig.time_step``
            uses), or any other value, which is returned unchanged.
        strict: Whether a string with no numeric magnitude is an error. Pass
            ``False`` where a plain string is itself a legal value — a
            construction option naming an enum member (``"polarization"``) is
            not a botched quantity — and it is returned unchanged instead.

    Returns:
        A units-carrying Pyomo expression, or ``value`` itself.

    Raises:
        FlexConfigError: If ``strict`` and a quantity string has no numeric
            magnitude.
    """
    if isinstance(value, dict) and set(value) == {"value", "units"}:
        return value["value"] * parse_units(value["units"])
    if isinstance(value, str):
        magnitude, _, units = value.strip().partition(" ")
        try:
            return float(magnitude) * parse_units(units)
        except ValueError as exc:
            if not strict:
                return value
            raise FlexConfigError(
                f"Could not read {value!r} as a quantity; write it as a number "
                "and its units, e.g. '15 min'.",
                value=value,
            ) from exc
    return value


def _unit_configs(cfg: ModelConfig) -> Iterator[tuple[str, UnitConfig]]:
    """Yield each unit's config path and its ``UnitConfig``."""
    if cfg.network is None:
        plants = {cfg.plant.name: cfg.plant}
        prefix = ""
    else:
        plants = cfg.network.plants
        prefix = f"{cfg.network.name}."
    for plant_name, plant_cfg in plants.items():
        for unit_name, unit_cfg in plant_cfg.units.items():
            yield f"{prefix}{plant_name}.{unit_name}", unit_cfg


def _resolve_units(model, cfg: ModelConfig) -> dict[str, OpsBlockData]:
    """Map each config unit path to its block on ``model``.

    Args:
        model: The model holding the units.
        cfg: The config naming them.

    Returns:
        Unit config path to built block.

    Raises:
        FlexConfigError: If a unit path does not resolve on the model.
    """
    units = {}
    for path, _ in _unit_configs(cfg):
        unit = model.find_component(path)
        if unit is None:
            raise FlexConfigError(
                f"Config unit {path!r} is not on the model.", field=path, value=path
            )
        units[path] = unit
    return units


def stage_declare(model, cfg: ModelConfig, ctx: BuildContext) -> None:
    """Add the TimeBlock, property packages and unprocessed costing block.

    Args:
        model: The empty model to populate.
        cfg: The validated config.
        ctx: The shared build context.

    Raises:
        FlexConfigError: If a property package class is unknown.
    """
    model.time_block = TimeBlock(
        start_date=cfg.time.start_date,
        end_date=cfg.time.end_date,
        time_step=parse_quantity(cfg.time.time_step),
    )
    for name, spec in cfg.properties.items():
        package_class = PROPERTY_PACKAGES.get(spec.property_class)
        if package_class is None:
            raise FlexConfigError(
                f"Unknown property_class {spec.property_class!r}. Known property "
                f"packages: {', '.join(sorted(PROPERTY_PACKAGES))}.",
                field=f"properties.{name}.property_class",
                value=spec.property_class,
            )
        model.add_component(name, package_class(**spec.options))
    model.costing = _build_costing(model, cfg)


def stage_topology(model, cfg: ModelConfig, ctx: BuildContext) -> None:
    """Build the units, plants, network and arcs, expanding arcs if requested.

    Args:
        model: The model with its declared blocks.
        cfg: The validated config.
        ctx: The shared build context; its ``units`` is filled here.

    Raises:
        FlexConfigError: If an arc endpoint is not a port.
    """
    packages = {name: model.find_component(name) for name in cfg.properties}
    if cfg.network is not None:
        model.add_component(cfg.network.name, NetworkBlock(time_block=model.time_block))
        network = model.find_component(cfg.network.name)
        for name, plant_cfg in cfg.network.plants.items():
            _build_plant(network, name, plant_cfg, model, packages)
        _build_arcs(network, cfg.network.arcs)
    else:
        _build_plant(model, cfg.plant.name, cfg.plant, model, packages)
    ctx.units = _resolve_units(model, cfg)
    if ctx.expand_arcs:
        pyo.TransformationFactory("network.expand_arcs").apply_to(model)


def stage_surrogates(model, cfg: ModelConfig, ctx: BuildContext) -> None:
    """Reserved: swaps registered relations from config (schema 0.1.0, PR 4)."""


def stage_degradation(model, cfg: ModelConfig, ctx: BuildContext) -> None:
    """Reserved: builds degradation terms from config (schema 0.1.0, PR 4)."""


def stage_ramping(model, cfg: ModelConfig, ctx: BuildContext) -> None:
    """Reserved: builds ramp limits from config (schema 0.1.0, PR 4)."""


def stage_logic(model, cfg: ModelConfig, ctx: BuildContext) -> None:
    """Reserved: builds status, startup, shutdown logic (schema 0.1.0, PR 4)."""


def stage_state(model, cfg: ModelConfig, ctx: BuildContext) -> None:
    """Apply each unit's external dispatch.

    Args:
        model: The model with built units.
        cfg: The validated config.
        ctx: The shared build context.

    Raises:
        FlexConfigError: If a dispatch variable or source is invalid.
    """
    for path, unit_cfg in _unit_configs(cfg):
        _apply_external_dispatch(ctx.units[path], unit_cfg, ctx.base_dir)


def stage_extensions(model, cfg: ModelConfig, ctx: BuildContext) -> None:
    """Reserved: runs user-supplied constraint builders (schema 0.1.0, PR 4)."""


def stage_costing(model, cfg: ModelConfig, ctx: BuildContext) -> None:
    """Process the costing block and add the objective.

    Args:
        model: The model with every cost term already registered.
        cfg: The validated config.
        ctx: The shared build context.
    """
    model.costing.cost_process()
    model.objective = pyo.Objective(expr=model.costing.aggregate_operating_cost)


STAGE_FUNCTIONS = {name: globals()[f"stage_{name}"] for name in STAGES}


def apply_relation_spec(
    unit, spec: SurrogateSpec, relation_name: str = ENERGY_RELATION
) -> tuple[bool, dict[str, float]]:
    """Write a relationship spec into a live unit as ``relation_name``.

    A ``constant_intensity`` spec fixes the unit's intensity parameter; any
    richer form swaps the named relation in place.

    Args:
        unit: The built unit to mutate.
        spec: The relationship to attach.
        relation_name: The relation a richer spec replaces.

    Returns:
        ``(swapped, fixed values)``: whether the Constraint was swapped, and the
        parameters that were fixed.

    Raises:
        FlexConfigError: If a ``constant_intensity`` spec carries no
            coefficient, or the unit does not register one as a regressable
            process parameter.
    """
    registry = unit._io_registry
    if spec.surrogate_type is not SurrogateType.CONSTANT_INTENSITY:
        surrogate_block = unit.swap_relation(relation_name, surrogate_from_spec(spec))
        coefficients = getattr(surrogate_block, "coefficients", None)
        if coefficients is not None and hasattr(coefficients, "items"):
            coef_names = {name for name, _ in coefficients.items()}
            already_registered = any(
                p.relation_name == relation_name and p.name in coef_names
                for p in registry.parameters
            )
            if not already_registered:
                unit.register_surrogate_coefficients(relation_name)
            for coef_name, coef_value in spec.data["coefficients"].items():
                var = coefficients[coef_name]
                var.set_value(coef_value)
                var.fix()
        return True, dict(spec.data.get("coefficients", {}))

    given = spec.data.get("coefficients", {})
    if INTENSITY_PARAMETER not in given:
        raise FlexConfigError(
            f"A 'constant_intensity' relationship must carry its coefficient "
            f"under data['coefficients'][{INTENSITY_PARAMETER!r}]; got "
            f"{sorted(given)}.",
            field="data",
            value=sorted(given),
        )
    coefficient = given[INTENSITY_PARAMETER]
    regressable = {
        record.name: record.param
        for record in registry.parameters
        if record.regressable
    }
    if INTENSITY_PARAMETER not in regressable:
        raise FlexConfigError(
            f"A 'constant_intensity' relationship determines "
            f"{INTENSITY_PARAMETER!r}, which {unit.name!r} does not register as a "
            f"regressable process parameter (it registers "
            f"{sorted(regressable)}). Supply this unit's relationship through "
            "surrogates= instead.",
            field=INTENSITY_PARAMETER,
            value=unit.name,
        )

    unit.update_parameters({INTENSITY_PARAMETER: coefficient})
    parameter = regressable[INTENSITY_PARAMETER]
    if parameter.is_variable_type():
        parameter.fix()
    return False, {INTENSITY_PARAMETER: coefficient}


def apply_stages(
    model, cfg: ModelConfig, stages: Sequence[str] = POST_TOPOLOGY_STAGES
) -> None:
    """Run build stages on a model that already exists, without rebuilding it.

    The model must have been built by ``build_model`` (it carries
    ``model._flex_build_context``), or have the same component layout the
    config describes. Stages always run in ``STAGES`` order, whatever order
    they are passed in.

    Args:
        model: The built model to update in place.
        cfg: The validated config describing the stages' inputs.
        stages: The stage names to run.

    Raises:
        FlexConfigError: If ``stages`` names ``declare`` or ``topology`` (those
            build the model, they can't be re-run on it); if a name is not in
            ``STAGES``; if a config unit path is not on the model; or if
            ``costing`` is requested on a model whose costing block has
            already been processed.
    """
    unknown = sorted(set(stages) - set(STAGES))
    if unknown:
        raise FlexConfigError(
            f"Unknown stage(s) {unknown}. Known stages: {', '.join(STAGES)}.",
            field="stages",
            value=unknown,
        )
    building = sorted(set(stages) & set(STAGES[:2]))
    if building:
        raise FlexConfigError(
            f"Stage(s) {building} build the model and cannot be re-run on an "
            f"existing one; apply_stages accepts {', '.join(POST_TOPOLOGY_STAGES)}.",
            field="stages",
            value=building,
        )
    if (
        "costing" in stages
        and model.costing.find_component("aggregate_operating_cost") is not None
    ):
        raise FlexConfigError(
            "Stage 'costing' already ran on this model; running cost_process() "
            "twice would double-register costs.",
            field="stages",
            value="costing",
        )
    ctx = getattr(model, "_flex_build_context", None)
    if ctx is None:
        ctx = BuildContext(
            base_dir=cfg._base_dir, expand_arcs=False, units=_resolve_units(model, cfg)
        )
    for name in STAGES:
        if name in stages:
            STAGE_FUNCTIONS[name](model, cfg, ctx)


def _resolve_source(source, base_dir):
    """Resolve a file path against the config directory, passing tags through.

    Args:
        source: A path or tag as written in the config, or None.
        base_dir: The config file's directory, or None.

    Returns:
        The resolved path string, or ``source`` unchanged when it is None or not
        a ``.json``/``.csv`` file name.
    """
    # A source may name a historian tag rather than a file; only resolve files.
    if source is None or not source.lower().endswith((".json", ".csv")):
        return source
    return str(resolve_source_path(source, base_dir))


def _resolve_tariff_source(source, base_dir):
    """Resolve every file path in a ``tariff_source`` of any shape.

    Args:
        source: A string, list of strings, mapping of utility to string, or None.
        base_dir: The config file's directory, or None.

    Returns:
        ``source`` with the same shape and each file path resolved.
    """
    if isinstance(source, list):
        return [_resolve_source(item, base_dir) for item in source]
    if isinstance(source, dict):
        return {key: _resolve_source(item, base_dir) for key, item in source.items()}
    return _resolve_source(source, base_dir)


def _build_costing(model, cfg: ModelConfig):
    """Build the FlexCosting block from a ``CostingConfig``.

    Args:
        model: The model being built (supplies the TimeBlock).
        cfg: The validated whole-model config.

    Returns:
        The constructible ``FlexCosting`` block.
    """
    costing = cfg.costing
    prices = {
        name: parse_quantity({"value": spec.value, "units": spec.units})
        for name, spec in (costing.energy_prices or {}).items()
    }
    base_dir = cfg._base_dir
    return FlexCosting(
        time_block=model.time_block,
        tariff_file=_resolve_tariff_source(costing.tariff_source, base_dir),
        energy_prices=prices or None,
        currency=costing.currency,
        dr_event_file=_resolve_source(
            None if costing.dr is None else costing.dr.events_source, base_dir
        ),
        consumption_estimate=costing.consumption_estimate,
        fixed_operating_cost=costing.fixed_operating_cost,
        prorate_monthly_charges=costing.prorate_monthly_charges,
        lifetime_years=costing.lifetime_years,
        discount_rate=costing.discount_rate,
        interest_rate=costing.interest_rate,
    )


def _build_plant(
    parent, name: str, plant_cfg: PlantConfig, model, packages: dict
) -> None:
    """Attach a PlantBlock named ``name`` to ``parent`` and populate it.

    Args:
        parent: The model or NetworkBlock the plant is attached to.
        name: The plant's attribute name on ``parent``.
        plant_cfg: The validated plant config.
        model: The whole model, supplying the TimeBlock, properties, costing.
        packages: The built property packages, keyed by their config name.
    """
    parent.add_component(name, PlantBlock(time_block=model.time_block))
    plant = parent.find_component(name)
    for unit_name, unit_cfg in plant_cfg.units.items():
        runtime = {}
        if unit_cfg.property_package is not None:
            key = unit_cfg.property_package
            # The config validator guarantees 'auto' means exactly one package.
            runtime["property_package"] = (
                next(iter(packages.values())) if key == "auto" else packages[key]
            )
        if unit_cfg.costing:
            runtime["costing_package"] = model.costing
        plant.add_component(
            unit_name, OpsBlockData.build_from_config(unit_cfg, **runtime)
        )
    _build_arcs(plant, plant_cfg.arcs)


def _build_arcs(block, arcs) -> None:
    """Build the declared arcs on ``block`` as ``arc_0``, ``arc_1``, ....

    Args:
        block: The plant or network the arcs belong to.
        arcs: The validated :class:`~flexcore.config.schema.ArcSpec` list.

    Raises:
        FlexConfigError: If an endpoint does not resolve to a port on ``block``.
    """
    for index, arc in enumerate(arcs):
        endpoints = {}
        for role, path in (("source", arc.source), ("destination", arc.destination)):
            port = block.find_component(path)
            if port is None:
                raise FlexConfigError(
                    f"Arc {role} {path!r} is not a port on {block.name!r}. Write "
                    "it as 'unit.port' relative to the plant (or "
                    "'plant.unit.port' relative to the network).",
                    field=role,
                    value=path,
                )
            endpoints[role] = port
        block.add_component(f"arc_{index}", Arc(**endpoints))


def _apply_external_dispatch(unit, unit_cfg, base_dir=None) -> None:
    """Fix a unit's actuator to a declared external (DERMS) command series.

    Args:
        unit: The built unit block.
        unit_cfg: Its validated ``UnitConfig``.
        base_dir: The config file's directory, for a relative source.

    Raises:
        FlexConfigError: If the declared variable is not on the unit, or the
            source file cannot be read as a time-indexed series.
    """
    spec = unit_cfg.external_dispatch
    if spec is None:
        return
    var = unit.find_component(spec.variable)
    if var is None:
        raise FlexConfigError(
            f"external_dispatch names variable {spec.variable!r}, which is not "
            f"on {unit.name!r}.",
            field="external_dispatch.variable",
            value=spec.variable,
        )
    try:
        raw = json.loads(resolve_source_path(spec.source, base_dir).read_text())
    except (OSError, ValueError) as exc:
        raise FlexConfigError(
            f"Could not read external-dispatch series {spec.source!r}: {exc}. "
            "Provide a JSON mapping of time index (or timestamp) to value.",
            field="external_dispatch.source",
            value=spec.source,
        ) from exc
    # JSON keys are always strings; integer time indices come back as "0".
    series = {
        int(key) if key.lstrip("-").isdigit() else key: value
        for key, value in raw.items()
    }
    unit.set_external_dispatch(var, series, fix=spec.fix)
