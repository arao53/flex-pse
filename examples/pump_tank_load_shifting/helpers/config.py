"""The config schema for the pump + tank (+ battery) load-shifting example.

One JSON file (``config.json``) is the single source of truth for the whole
model: the time horizon, the tariff, and every unit's construction parameters
and connections. Every unit-carrying quantity is a ``"<value> <units>"``
string parsed by :func:`helpers.units.parse_quantity` at build time; plain
fractions and flags are ordinary fields. Reuses
``flexcore.config.schema``'s ``TimeConfig``, ``UnitCommitmentConfig``, and
``ArcSpec`` directly rather than inventing parallel ones.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from flexcore.config.schema import ArcSpec, TimeConfig, UnitCommitmentConfig
from flexcore.exceptions import FlexConfigError

SCHEMA_VERSION = "0.0.1"


class _StrictModel(BaseModel):
    """Base for every config model here: reject undocumented keys."""

    model_config = ConfigDict(extra="forbid")


class TariffConfig(_StrictModel):
    """The EECO tariff this run costs electricity against."""

    path: str = Field(
        description="Tariff JSON file, relative to this config file's directory."
    )
    include_demand_charges: bool = Field(
        default=True, description="Keep the tariff's demand-charge rate rows."
    )


class FacilityConfig(_StrictModel):
    """Facility-level (non-unit) parameters."""

    draw: str = Field(
        default="100 m**3/hr",
        description="Fixed volumetric draw the tank must meet every step.",
    )


class PumpConfig(_StrictModel):
    """The pump's sizing, energy relation, and optional unit commitment."""

    max_flow: str = Field(
        description="Pump inlet flow upper bound, e.g. '300 m**3/hr'."
    )
    energy_intensity: str = Field(
        default="0.5 kWh/m**3",
        description="Electrical energy per unit volume pumped.",
    )
    unit_commitment: UnitCommitmentConfig = Field(
        default_factory=lambda: UnitCommitmentConfig(
            status=False, startup_shutdown=False, min_up=3, min_down=3
        ),
        description="Pump on/off status + startup/shutdown transition logic "
        "(flexops.logic.add_status/add_startup_shutdown); status=False "
        "skips unit-commitment logic entirely.",
    )
    relax: bool = Field(
        default=True,
        description="Relax the unit-commitment binaries to an LP "
        "(flexops.logic.relax); ignored when unit_commitment.status is False.",
    )


class TankConfig(_StrictModel):
    """The storage tank's sizing."""

    max_volume: str = Field(description="Maximum tank volume, e.g. '1000 m**3'.")
    initial_volume: str = Field(description="Initial tank volume, e.g. '200 m**3'.")


class BatteryConfig(_StrictModel):
    """The behind-the-meter battery's sizing and efficiencies."""

    enabled: bool = Field(default=True, description="Include the battery at all.")
    capacity: str = Field(default="2000 kWh", description="Battery energy capacity.")
    power_charge_max: str = Field(
        default="500 kW", description="Maximum charging power."
    )
    power_discharge_max: str = Field(
        default="500 kW", description="Maximum discharging power."
    )
    eta_charge: float = Field(
        default=0.95, description="Charging efficiency, a fraction in (0, 1]."
    )
    eta_discharge: float = Field(
        default=0.95, description="Discharging efficiency, a fraction in (0, 1]."
    )
    soc_min: float = Field(
        default=0.10, description="Minimum state of charge, a fraction of capacity."
    )
    soc_max: float = Field(
        default=0.90, description="Maximum state of charge, a fraction of capacity."
    )


class ExampleConfig(_StrictModel):
    """The full pump + tank (+ battery) model and run, one JSON artifact."""

    schema_version: str = Field(default=SCHEMA_VERSION)
    time: TimeConfig = Field(description="The discrete-time horizon.")
    tariff: TariffConfig = Field(description="Tariff source and options.")
    facility: FacilityConfig = Field(default_factory=FacilityConfig)
    pump: PumpConfig = Field(description="Pump sizing, energy relation, and UC.")
    tank: TankConfig = Field(description="Tank sizing.")
    battery: BatteryConfig = Field(default_factory=BatteryConfig)
    arcs: list[ArcSpec] = Field(
        default_factory=lambda: [
            ArcSpec(source="pump.outlet", destination="tank.inlet")
        ],
        description="Connections between unit ports, as 'unit.port' endpoints.",
    )


def load_config(path) -> ExampleConfig:
    """Load and validate the example config from a JSON file.

    Args:
        path: Path to the ``.json`` config file.

    Returns:
        The validated :class:`ExampleConfig`.

    Raises:
        FlexConfigError: If the file is not JSON or fails schema validation.
    """
    path = Path(path)
    try:
        return ExampleConfig.model_validate_json(path.read_text())
    except ValueError as exc:
        raise FlexConfigError(
            f"Invalid example config at {path}: {exc}", value=str(path)
        ) from exc


def save_config(config: ExampleConfig, path) -> None:
    """Write the example config to disk as indented JSON.

    Args:
        config: The :class:`ExampleConfig` to serialize.
        path: Destination ``.json`` path.
    """
    Path(path).write_text(config.model_dump_json(indent=2) + "\n")
