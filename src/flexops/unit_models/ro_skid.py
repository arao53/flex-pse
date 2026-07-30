"""ReverseOsmosisSkid(Separator): feed -> permeate + concentrate (§3.4).

A thin physical subclass of :class:`~flexops.unit_models.separator.SeparatorData`
that only renames the split into RO vocabulary, bounds it, and re-defaults the
energy intensity: no new Pyomo components, so the whole model is the separator's.
"""

from idaes.core import declare_process_block_class
from pyomo.common.config import ConfigValue
from pyomo.environ import units as pyunits

from flexcore.exceptions import FlexConfigError
from flexops.unit_models.separator import SeparatorData


@declare_process_block_class("ReverseOsmosisSkid")
class ReverseOsmosisSkidData(SeparatorData):
    r"""A reverse-osmosis skid: a separator read as recovery + brine.

    ``outlet_a`` is the **permeate** and ``outlet_b`` the **concentrate**
    (brine), so the inherited split *is* the skid's water recovery and is
    exposed under that name: both the config option and the Var are
    ``recovery``, and ``split_fraction`` does not exist on this unit. The
    inherited ``energy_intensity`` is its specific energy consumption per unit
    of feed:

    .. math::

        \dot{V}_{perm}[t] &= \text{recovery} \cdot \dot{V}_{feed}[t] \\
        P_{elec}[t] &= \text{energy\_intensity} \cdot \dot{V}_{feed}[t]

    ``recovery_min``/``recovery_max`` are the recovery Var's bounds, defaulted
    to the seawater-RO window. They bind once the Var is unfixed — by a design
    mode or a regression — where they keep the fitted recovery inside what the
    membrane train can actually deliver; a brackish or high-recovery train
    raises ``recovery_max``. An inverted window, or one that cannot hold the
    configured ``recovery``, raises ``FlexConfigError`` at build time rather
    than producing a Var whose bounds contradict its value.

    Config:
        Inherits the Separator config with ``split_fraction`` renamed to
        ``recovery`` (default 0.45); adds ``recovery_min`` (0.3) and
        ``recovery_max`` (0.6); re-defaults ``energy_intensity`` to
        3.0 kWh/m^3 of feed.

    Example:
        >>> from flexops.testing import dummy_time_block
        >>> from flexops.unit_models import ReverseOsmosisSkid
        >>> m = dummy_time_block(3)
        >>> m.ro = ReverseOsmosisSkid(property_package=m.properties)  # doctest: +SKIP
    """

    CONFIG = SeparatorData.CONFIG()
    del CONFIG["split_fraction"]  # renamed to "recovery", declared just below
    CONFIG.declare(
        "recovery",
        ConfigValue(
            default=0.45,
            domain=float,
            description="Water recovery: the permeate (outlet_a) fraction of "
            "the feed (a fixed, regressable Var once built); the remainder "
            "leaves as concentrate through outlet_b.",
        ),
    )
    CONFIG.declare(
        "recovery_min",
        ConfigValue(
            default=0.3,
            domain=float,
            description="Lower bound on the recovery Var: the lowest recovery "
            "the membrane train can be operated or fitted at.",
        ),
    )
    CONFIG.declare(
        "recovery_max",
        ConfigValue(
            default=0.6,
            domain=float,
            description="Upper bound on the recovery Var: the highest recovery "
            "the membrane train can be operated or fitted at.",
        ),
    )
    CONFIG.get("energy_intensity").set_default_value(3.0 * pyunits.kWh / pyunits.m**3)

    _split_parameter_name = "recovery"

    def _split_parameter_bounds(self) -> tuple[float, float]:
        """Return the configured recovery window, rejecting an unusable one.

        Returns:
            The ``(recovery_min, recovery_max)`` bounds of the recovery Var.

        Raises:
            FlexConfigError: If the window is inverted, or if the configured
                ``recovery`` falls outside it.
        """
        low, high = self.config.recovery_min, self.config.recovery_max
        if low > high:
            raise FlexConfigError(
                f"recovery_min ({low}) exceeds recovery_max ({high}); set "
                "recovery_min <= recovery_max.",
                field="recovery_min",
                value=low,
            )
        if not low <= self.config.recovery <= high:
            raise FlexConfigError(
                f"recovery ({self.config.recovery}) lies outside the window "
                f"[{low}, {high}]; move recovery inside the window or widen "
                "recovery_min/recovery_max.",
                field="recovery",
                value=self.config.recovery,
            )
        return low, high
