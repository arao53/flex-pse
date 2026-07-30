"""ReverseOsmosisSkid(Separator): feed -> permeate + concentrate (§3.4, R6).

A thin physical subclass of :class:`~flexops.unit_models.separator.SeparatorData`
that only fixes the split semantics and typical RO energy intensity: no new
components, so the whole model is the separator's.
"""

from idaes.core import declare_process_block_class
from pyomo.environ import units as pyunits

from flexops.unit_models.separator import SeparatorData


@declare_process_block_class("ReverseOsmosisSkid")
class ReverseOsmosisSkidData(SeparatorData):
    r"""A reverse-osmosis skid: a separator read as recovery + brine.

    ``outlet_a`` is the **permeate** and ``outlet_b`` the **concentrate**
    (brine), so the inherited ``split_fraction`` is the skid's water recovery
    and the inherited ``energy_intensity`` its specific energy consumption per
    unit of feed:

    .. math::

        \dot{V}_{perm}[t] &= \text{recovery} \cdot \dot{V}_{feed}[t] \\
        P_{elec}[t] &= \text{energy\_intensity} \cdot \dot{V}_{feed}[t]

    Config:
        Inherits the Separator config, re-defaulted to seawater-RO figures:
        ``split_fraction`` 0.45 (recovery) and ``energy_intensity``
        3.0 kWh/m^3 of feed.

    Example:
        >>> from flexops.testing import dummy_time_block
        >>> from flexops.unit_models import ReverseOsmosisSkid
        >>> m = dummy_time_block(3)
        >>> m.ro = ReverseOsmosisSkid(property_package=m.properties)  # doctest: +SKIP
    """

    CONFIG = SeparatorData.CONFIG()
    CONFIG.get("split_fraction").set_default_value(0.45)
    CONFIG.get("energy_intensity").set_default_value(3.0 * pyunits.kWh / pyunits.m**3)
