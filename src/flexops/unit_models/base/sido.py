"""SIDOBlock: the single-inlet/double-outlet (split) IO-topology base (§3.4).

The second IO-topology base class: owns port construction (via the inherited
:meth:`~flexops.core.ops_block.OpsBlockData.add_stream_ports`) and the split
mass balance, so physical subclasses
(:class:`~flexops.unit_models.separator.Separator` and the units derived from
it) only add the flow-to-energy relationship. Registers no power itself.

**The mass balance is two constraints.** Conservation
``flow_in[t] == flow_out_a[t] + flow_out_b[t]`` alone leaves the split
undetermined, so the base also fixes *where* the feed goes with
``flow_out_a[t] == split_fraction * flow_in[t]``. ``split_fraction`` is a fixed,
regressable scalar Var (not time-indexed), which keeps both constraints linear —
the topology stays LP-representable — and makes the split the natural regression
target for FlexParameterize. Everything a stream carries other than flow (e.g.
pressure/temperature, when the property package has them) passes straight from
the inlet to **both** outlets.
"""

import pyomo.environ as pyo
from idaes.core import declare_process_block_class
from pyomo.common.config import ConfigValue
from pyomo.environ import units as pyunits

from flexops.core.ops_block import OpsBlockData


@declare_process_block_class("SIDOBlock")
class SIDOBlockData(OpsBlockData):
    """One inlet, two outlet ports with a split mass balance (module docstring).

    Config:
        Inherits the OpsBlock config; adds ``split_fraction`` (default 0.5).

    Example:
        >>> from flexops.testing import dummy_time_block
        >>> from flexops.unit_models.base import SIDOBlock
        >>> m = dummy_time_block(3)
        >>> m.unit = SIDOBlock(property_package=m.properties)  # doctest: +SKIP
    """

    CONFIG = OpsBlockData.CONFIG()
    CONFIG.get("allow_pass_through").set_default_value(True)
    CONFIG.declare(
        "split_fraction",
        ConfigValue(
            default=0.5,
            domain=float,
            description="Fraction of the inlet flow leaving through outlet_a "
            "(a fixed, regressable Var once built); the remainder leaves "
            "through outlet_b.",
        ),
    )

    def build(self) -> None:
        """Build the inlet/two-outlet ports and the split mass balance."""
        super().build()
        self.add_stream_ports(outlet_ports=("outlet_a", "outlet_b"))
        self._build_mass_balance()

    def _build_mass_balance(self) -> None:
        """Build ``split_fraction``, the split definition, and conservation."""
        tb = self._find_time_block()
        self.flow_in = pyo.Reference(self.inlet_state.flow_vol_phase[:, "Liq"])
        self.flow_out_a = pyo.Reference(self.outlet_a_state.flow_vol_phase[:, "Liq"])
        self.flow_out_b = pyo.Reference(self.outlet_b_state.flow_vol_phase[:, "Liq"])

        self.split_fraction = pyo.Var(
            initialize=self.config.split_fraction,
            bounds=(0.0, 1.0),
            units=pyunits.dimensionless,
            doc="Fraction of the inlet flow leaving through outlet_a. Fixed at "
            "the configured value; FlexParameterize may regress it.",
        )
        self.split_fraction.fix(self.config.split_fraction)
        self.register_process_parameter(self.split_fraction, regressable=True)

        @self.Constraint(
            tb.time_index,
            doc="Split definition: outlet_a flow == split_fraction * inlet flow.",
        )
        def split_definition(b, t):
            return b.flow_out_a[t] == b.split_fraction * b.flow_in[t]

        @self.Constraint(
            tb.time_index,
            doc="Conservation: inlet flow == outlet_a flow + outlet_b flow.",
        )
        def split_mass_balance(b, t):
            return b.flow_in[t] == b.flow_out_a[t] + b.flow_out_b[t]

        # Flow is governed above; everything else the streams carry passes
        # through to BOTH outlets (a distinct name_prefix per outlet, or the
        # two calls would collide on one component name).
        flow_name = self.config.property_package.get_flow_basis_var_name()
        for suffix, outlet in (("a", self.outlet_a), ("b", self.outlet_b)):
            self.add_pass_through_constraints(
                self.inlet,
                outlet,
                exclude_vars=[flow_name],
                name_prefix=f"pass_through_{suffix}",
            )
