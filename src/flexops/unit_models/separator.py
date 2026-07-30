r"""Separator(SIDOBlock): one feed split into two product streams (§3.4, R6).

This is the class that used to be called ``Electrolyzer``: separation is the
topology, and the physical process (electrolysis, reverse osmosis, combustion)
is a thin subclass of it. Inherits its ports, ``split_fraction``, and the split
mass balance from :class:`~flexops.unit_models.base.sido.SIDOBlockData`; adds
only the electrical draw.
"""

from idaes.core import declare_process_block_class
from pyomo.common.config import ConfigValue
from pyomo.environ import units as pyunits

from flexcore import nomenclature as nm
from flexops.unit_models.base.sido import SIDOBlockData


@declare_process_block_class("Separator")
class SeparatorData(SIDOBlockData):
    r"""A separator: one feed, two products, constant electrical intensity.

    .. math::

        \dot{V}_{out,a}[t] &= \text{split\_fraction} \cdot \dot{V}_{in}[t] \\
        \dot{V}_{in}[t] &= \dot{V}_{out,a}[t] + \dot{V}_{out,b}[t] \\
        P_{elec}[t] &= \text{energy\_intensity} \cdot \dot{V}_{in}[t]

    The energy relation is the Constraint ``power_electrical_relation`` (the
    swap contract; see
    :meth:`~flexops.core.ops_block.OpsBlockData.add_constant_intensity_relation`).

    Config:
        Inherits the SIDO/OpsBlock config (``split_fraction``); adds
        ``energy_intensity`` (default 0.5 kWh/m^3).

    Example:
        >>> from flexops.testing import dummy_time_block
        >>> from flexops.unit_models import Separator
        >>> m = dummy_time_block(3)
        >>> m.unit = Separator(property_package=m.properties)  # doctest: +SKIP
    """

    CONFIG = SIDOBlockData.CONFIG()
    CONFIG.declare(
        "energy_intensity",
        ConfigValue(
            default=0.5 * pyunits.kWh / pyunits.m**3,
            description="Electrical energy per unit volume of feed separated (a "
            "fixed, regressable Var once built), kWh/m^3.",
        ),
    )

    def build(self) -> None:
        """Build the SIDO base, then the constant-intensity electrical relation."""
        super().build()
        self.add_constant_intensity_relation(
            self.flow_in,
            kind=nm.PowerKind.ELECTRICAL,
            intensity=self.config.energy_intensity,
        )
