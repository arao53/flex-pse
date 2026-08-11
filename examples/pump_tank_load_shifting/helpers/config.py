"""The config schema for the pump + tank (+ battery) load-shifting example.

The model itself is an ordinary :class:`~flexcore.config.schema.ModelConfig`,
built by :func:`flexops.core.build.build_model` exactly like any other
flex-pse model ("the config-driven twin", ``docs/how_to/build_a_plant.md``).
This wrapper adds only the handful of knobs that config cannot express today
(see ``helpers/build.py``): the facility's fixed draw, the pump's flow cap,
and whether its unit-commitment binaries are relaxed to an LP.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from flexcore.config.schema import ModelConfig
from flexcore.exceptions import FlexConfigError


class ExampleConfig(BaseModel):
    """A :class:`ModelConfig` plus the physics it cannot express."""

    model_config = ConfigDict(extra="forbid")

    model: ModelConfig = Field(description="The flex-pse model to build.")
    facility_draw: str = Field(
        default="100 m^3/hr",
        description="Fixed volumetric draw the tank must meet every step.",
    )
    pump_max_flow: str = Field(
        default="300 m^3/hr", description="Pump inlet flow upper bound."
    )
    pump_relax: bool = Field(
        default=True,
        description="Relax the pump's unit-commitment binaries to an LP "
        "(flexops.logic.relax); ignored when the pump's unit_commitment.status "
        "is False.",
    )


def load_config(path) -> ExampleConfig:
    """Load and validate the example config, resolving its tariff path.

    A relative ``costing.tariff_source`` is rewritten relative to ``path``'s
    directory, so the config stays valid regardless of the caller's working
    directory (``flexops.core.build.build_model`` reads ``tariff_source``
    as-is, with no such lookup of its own).

    Args:
        path: Path to the ``.json`` config file.

    Returns:
        The validated :class:`ExampleConfig`.

    Raises:
        FlexConfigError: If the file is not JSON or fails schema validation.
    """
    path = Path(path)
    try:
        config = ExampleConfig.model_validate_json(path.read_text())
    except ValueError as exc:
        raise FlexConfigError(
            f"Invalid example config at {path}: {exc}", value=str(path)
        ) from exc
    tariff_source = config.model.costing.tariff_source
    if isinstance(tariff_source, str) and not Path(tariff_source).is_absolute():
        config.model.costing.tariff_source = str(path.parent / tariff_source)
    return config


def save_config(config: ExampleConfig, path) -> None:
    """Write the example config to disk as indented JSON.

    Args:
        config: The :class:`ExampleConfig` to serialize.
        path: Destination ``.json`` path.
    """
    Path(path).write_text(config.model_dump_json(indent=2) + "\n")
