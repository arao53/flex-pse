r"""ElectrolysisSeparator(Separator): electrolysis as a separation (§3.4, R6).

A thin physical subclass of :class:`~flexops.unit_models.separator.SeparatorData`
that adds a **thermal** duty alongside the electrical draw — the one v0 unit
that exercises ``power_thermal`` (an electrolyzer's stack heat has to be
rejected, and it is a heat duty at a temperature, never mixed with electricity).
"""

from idaes.core import declare_process_block_class
from pyomo.common.config import ConfigValue
from pyomo.environ import units as pyunits

from flexcore import nomenclature as nm
from flexops.unit_models.separator import SeparatorData


@declare_process_block_class("ElectrolysisSeparator")
class ElectrolysisSeparatorData(SeparatorData):
    r"""Electrolysis modeled as a separation, with both power kinds.

    ``outlet_a`` is the product gas stream and ``outlet_b`` the depleted
    feed. On top of the separator's electrical relation it adds

    .. math::

        P_{therm}[t] = \text{thermal\_intensity} \cdot \dot{V}_{in}[t]

    as the Constraint ``power_thermal_relation``, registered at
    ``thermal_temperature`` so the duty aggregates with other heat at the same
    temperature and never with electricity.

    Config:
        Inherits the Separator config (re-defaulted: ``split_fraction`` 0.6,
        ``energy_intensity`` 50 kWh/m^3); adds ``thermal_intensity`` (default
        5 kWh/m^3) and ``thermal_temperature`` (default 353.15 K).

    Example:
        >>> from flexops.testing import dummy_time_block
        >>> from flexops.unit_models import ElectrolysisSeparator
        >>> m = dummy_time_block(3)
        >>> m.cell = ElectrolysisSeparator(  # doctest: +SKIP
        ...     property_package=m.properties
        ... )
    """

    CONFIG = SeparatorData.CONFIG()
    CONFIG.get("split_fraction").set_default_value(0.6)
    CONFIG.get("energy_intensity").set_default_value(50.0 * pyunits.kWh / pyunits.m**3)
    CONFIG.declare(
        "thermal_intensity",
        ConfigValue(
            default=5.0 * pyunits.kWh / pyunits.m**3,
            description="Heat duty per unit volume of feed (a fixed, regressable "
            "Var once built), kWh/m^3.",
        ),
    )
    CONFIG.declare(
        "thermal_temperature",
        ConfigValue(
            default=353.15 * pyunits.K,
            description="Temperature the heat duty is rejected at; duties at "
            "different temperatures are never aggregated together.",
        ),
    )

    def build(self) -> None:
        """Build the separator, then the thermal relation on the same feed flow."""
        super().build()
        self.add_constant_intensity_relation(
            self.flow_in,
            kind=nm.PowerKind.THERMAL,
            intensity=self.config.thermal_intensity,
            temperature=self.config.thermal_temperature,
        )
