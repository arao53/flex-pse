"""ReverseOsmosis(SIDOBlock): feed -> permeate + brine."""

import enum

import pyomo.environ as pyo
from idaes.core import declare_process_block_class
from pyomo.common.config import ConfigValue
from pyomo.environ import units as pyunits

from flexcore import nomenclature as nm
from flexcore.exceptions import FlexConfigError
from flexops.logic.status import _track_binary
from flexops.unit_models.base.sido import SIDOBlockData


class MembraneModel(enum.StrEnum):
    """Which permeate relation a :class:`ReverseOsmosis` enforces."""

    CONSTANT_RECOVERY = "constant_recovery"
    LINEARIZED = "linearized"


class FeedWaterType(enum.StrEnum):
    """Which feed water's literature defaults a :class:`ReverseOsmosis` uses."""

    SEAWATER = "seawater"
    BRACKISH = "brackish"

    def defaults(self) -> dict[str, float]:
        """Return this feed water's default for each preset-governed option.

        Returns:
            Option name -> default, in the units that option's config documents.
        """
        if self is FeedWaterType.SEAWATER:
            return {
                "recovery": 0.45,
                "recovery_min": 0.35,
                "recovery_max": 0.5,
                "flux_min": 12.0,
                "flux_max": 19.0,
                "water_permeability": 1.2,
                "feed_osmotic_pressure": 27.0,
                "erd_efficiency": 0.96,
            }
        return {
            "recovery": 0.75,
            "recovery_min": 0.65,
            "recovery_max": 0.85,
            "flux_min": 20.0,
            "flux_max": 34.0,
            "water_permeability": 3.0,
            "feed_osmotic_pressure": 2.3,
            "erd_efficiency": 0.0,
        }


def _enum_domain(enum_type: type[enum.StrEnum], field: str):
    """Return a ConfigValue domain coercing a value to ``enum_type``.

    Args:
        enum_type: The StrEnum to coerce to.
        field: The config option name, reported on error.

    Returns:
        The domain callable.
    """

    def domain(value):
        try:
            return enum_type(value)
        except ValueError as exc:
            allowed = ", ".join(repr(member.value) for member in enum_type)
            raise FlexConfigError(
                f"{field} must be one of {allowed}, got {value!r}.",
                field=field,
                value=value,
            ) from exc

    return domain


@declare_process_block_class("ReverseOsmosis")
class ReverseOsmosisData(SIDOBlockData):
    """A reverse-osmosis train: feed split into permeate and brine.

    Config:
        Inherits the SIDO/OpsBlock config with ``split_fraction`` renamed to
        ``recovery``; adds ``membrane_model``, ``feed_water``, the recovery and
        flux windows, ``membrane_area``, ``energy_intensity``, and the
        linearized-mode physics options. Options left ``None`` take the
        ``feed_water`` preset.
    """

    CONFIG = SIDOBlockData.CONFIG()
    del CONFIG["split_fraction"]  # renamed to "recovery", declared just below
    CONFIG.declare(
        "membrane_model",
        ConfigValue(
            default=MembraneModel.CONSTANT_RECOVERY,
            domain=_enum_domain(MembraneModel, "membrane_model"),
            description="Permeate relation: 'constant_recovery' (permeate = "
            "recovery * feed, power = energy_intensity * permeate) or "
            "'linearized' (piecewise-linear membrane, high-pressure pump, "
            "circulation pump and energy recovery device; an MILP).",
        ),
    )
    CONFIG.declare(
        "feed_water",
        ConfigValue(
            default=FeedWaterType.SEAWATER,
            domain=_enum_domain(FeedWaterType, "feed_water"),
            description="Feed water type, 'seawater' or 'brackish', whose "
            "literature values fill every preset option left None.",
        ),
    )
    CONFIG.declare(
        "recovery",
        ConfigValue(
            default=None,
            description="Water recovery: the permeate fraction of the feed "
            "(a fixed, regressable Var once built). None takes the preset.",
        ),
    )
    CONFIG.declare(
        "recovery_min",
        ConfigValue(
            default=None,
            description="Lowest recovery the train can run or be fitted at. "
            "None takes the preset.",
        ),
    )
    CONFIG.declare(
        "recovery_max",
        ConfigValue(
            default=None,
            description="Highest recovery the train can run or be fitted at. "
            "None takes the preset.",
        ),
    )
    CONFIG.declare(
        "membrane_area",
        ConfigValue(
            default=None,
            description="Total active membrane area, m^2. Enables the flux "
            "limits; required by membrane_model='linearized'. None builds no "
            "flux limit.",
        ),
    )
    CONFIG.declare(
        "flux_min",
        ConfigValue(
            default=None,
            description="Lowest average permeate flux while running, "
            "L/(m^2 h). None takes the preset.",
        ),
    )
    CONFIG.declare(
        "flux_max",
        ConfigValue(
            default=None,
            description="Highest average permeate flux, L/(m^2 h). None takes "
            "the preset.",
        ),
    )
    CONFIG.declare(
        "energy_intensity",
        ConfigValue(
            default=3.0 * pyunits.kWh / pyunits.m**3,
            description="Electrical energy per unit volume of permeate (a "
            "fixed, regressable Var once built), kWh/m^3. Used only when "
            "membrane_model='constant_recovery'.",
        ),
    )
    CONFIG.declare(
        "water_permeability",
        ConfigValue(
            default=None,
            description="Membrane water permeability, L/(m^2 h bar). Used "
            "only when membrane_model='linearized'. None takes the preset.",
        ),
    )
    CONFIG.declare(
        "feed_osmotic_pressure",
        ConfigValue(
            default=None,
            description="Osmotic pressure of the feed, bar. Used only when "
            "membrane_model='linearized'. None takes the preset.",
        ),
    )
    CONFIG.declare(
        "pump_efficiency",
        ConfigValue(
            default=0.8,
            description="Efficiency of the high-pressure and circulation "
            "pumps, a fraction in (0, 1]. Used only when "
            "membrane_model='linearized'.",
        ),
    )
    CONFIG.declare(
        "erd_efficiency",
        ConfigValue(
            default=None,
            description="Energy recovery device efficiency, a fraction in "
            "[0, 1); 0 means no device. Used only when "
            "membrane_model='linearized'. None takes the preset.",
        ),
    )
    CONFIG.declare(
        "membrane_pressure_drop",
        ConfigValue(
            default=1.5 * pyunits.bar,
            description="Feed-to-brine pressure drop across the membranes, "
            "bar. Used only when membrane_model='linearized'.",
        ),
    )
    CONFIG.declare(
        "breakpoints",
        ConfigValue(
            default=5,
            domain=int,
            description="Grid points on each of the recovery and feed axes of "
            "the piecewise-linear membrane, at least 2. Used only when "
            "membrane_model='linearized'.",
        ),
    )

    def build(self) -> None:
        """Build the SIDO base in RO vocabulary, then the membrane relation.

        Raises:
            FlexConfigError: If ``membrane_model='linearized'`` has no
                ``membrane_area``.
        """
        super().build(
            naming_dict={
                **SIDOBlockData._component_names,
                "flow_in": "feed",
                "flow_out_a": "permeate",
                "flow_out_b": "brine",
                "split_fraction": "recovery",
            }
        )
        linearized = self.config.membrane_model == MembraneModel.LINEARIZED
        if linearized and self.config.membrane_area is None:
            raise FlexConfigError(
                "membrane_model='linearized' requires membrane_area.",
                field="membrane_area",
                value=None,
            )
        permeate = self.find_component(self._named("flow_out_a"))
        if self.config.membrane_area is not None:
            self._declare_flux_window()
        if linearized:
            self._build_linearized_membrane(
                self.find_component(self._named("flow_in")), permeate
            )
        else:
            self.add_constant_intensity_relation(
                permeate,
                kind=nm.PowerKind.ELECTRICAL,
                intensity=self.config.energy_intensity,
            )
        if self.config.membrane_area is not None:
            self._build_flux_limits(permeate)

    def _option(self, name: str):
        """Return config option ``name``, or its ``feed_water`` preset if None.

        Args:
            name: A preset-governed config option.

        Returns:
            The configured value, else the preset default.
        """
        value = self.config[name]
        return self.config.feed_water.defaults()[name] if value is None else value

    def _split_parameter_value(self) -> float:
        """Return the configured recovery, RO's name for the split parameter.

        Returns:
            The water recovery.
        """
        return self._option("recovery")

    def _split_parameter_bounds(self) -> tuple[float, float]:
        """Return the recovery window, rejecting an unusable one.

        Returns:
            The ``(recovery_min, recovery_max)`` bounds of the recovery Var.

        Raises:
            FlexConfigError: If the window is inverted, or if the recovery
                falls outside it.
        """
        low, high = self._option("recovery_min"), self._option("recovery_max")
        recovery = self._option("recovery")
        if low > high:
            raise FlexConfigError(
                f"recovery_min ({low}) exceeds recovery_max ({high}); set "
                "recovery_min <= recovery_max.",
                field="recovery_min",
                value=low,
            )
        if not low <= recovery <= high:
            raise FlexConfigError(
                f"recovery ({recovery}) lies outside the window [{low}, "
                f"{high}]; move recovery inside the window or widen "
                "recovery_min/recovery_max.",
                field="recovery",
                value=recovery,
            )
        return low, high

    def _declare_flux_window(self) -> None:
        """Declare the membrane area and the flux window as fixed parameters."""
        lmh = pyunits.L / pyunits.m**2 / pyunits.hr
        self.declare_process_parameter(
            "membrane_area",
            self.config.membrane_area,
            pyunits.m**2,
            "Total active membrane area.",
            bounds=(0.0, None),
            regressable=False,
        )
        for name, sense in (("flux_min", "Lowest"), ("flux_max", "Highest")):
            self.declare_process_parameter(
                name,
                self._option(name),
                lmh,
                f"{sense} average permeate flux.",
                bounds=(0.0, None),
                regressable=False,
            )

    def _build_flux_limits(self, permeate) -> None:
        """Bound permeate by flux * area; the floor holds only while running.

        Args:
            permeate: The time-indexed permeate flow.
        """
        tb = self._find_time_block()
        flow_units = pyunits.m**3 / pyunits.hr
        off = self.find_component("membrane_off")

        @self.Constraint(tb.time_index, doc="permeate >= flux_min * area when on.")
        def flux_min_limit(b, t):
            on = 1 if off is None else 1 - off[t]
            floor = pyunits.convert(b.flux_min * b.membrane_area, flow_units)
            return permeate[t] >= floor * on

        @self.Constraint(tb.time_index, doc="permeate <= flux_max * area.")
        def flux_max_limit(b, t):
            return permeate[t] <= pyunits.convert(
                b.flux_max * b.membrane_area, flow_units
            )

    def _build_linearized_membrane(self, feed, permeate) -> None:
        """Build the piecewise-linear membrane, pumps, and ERD power relation.

        Args:
            feed: The time-indexed feed flow.
            permeate: The time-indexed permeate flow.
        """
        tb = self._find_time_block()
        flow_units = pyunits.m**3 / pyunits.hr
        for name, value, units, doc, bounds in (
            (
                "water_permeability",
                self._option("water_permeability"),
                pyunits.L / pyunits.m**2 / pyunits.hr / pyunits.bar,
                "Membrane water permeability.",
                (0.0, None),
            ),
            (
                "feed_osmotic_pressure",
                self._option("feed_osmotic_pressure"),
                pyunits.bar,
                "Osmotic pressure of the feed.",
                (0.0, None),
            ),
            (
                "pump_efficiency",
                self.config.pump_efficiency,
                pyunits.dimensionless,
                "High-pressure and circulation pump efficiency.",
                (0.0, 1.0),
            ),
            (
                "erd_efficiency",
                self._option("erd_efficiency"),
                pyunits.dimensionless,
                "Energy recovery device efficiency; 0 means no device.",
                (0.0, 1.0),
            ),
            (
                "membrane_pressure_drop",
                self.config.membrane_pressure_drop,
                pyunits.bar,
                "Feed-to-brine pressure drop across the membranes.",
                (0.0, None),
            ),
        ):
            self.declare_process_parameter(name, value, units, doc, bounds=bounds)

        # Grid: recovery rays x feed levels spanning the flux and recovery windows.
        n = self.config.breakpoints
        r_lo, r_hi = self.recovery.bounds
        p_lo, p_hi = (
            pyo.value(pyunits.convert(flux * self.membrane_area, flow_units))
            for flux in (self.flux_min, self.flux_max)
        )
        f_lo, f_hi = p_lo / r_hi, p_hi / r_lo
        self.pwl_points = pyo.RangeSet(0, n - 1)
        self.pwl_recovery = pyo.Param(
            self.pwl_points,
            initialize=lambda b, i: r_lo + (r_hi - r_lo) * i / (n - 1),
            doc="Recovery of each grid ray.",
        )
        self.pwl_feed_flow = pyo.Param(
            self.pwl_points,
            initialize=lambda b, j: f_lo + (f_hi - f_lo) * j / (n - 1),
            doc="Feed flow of each grid level, m^3/hr.",
        )
        self.pwl_vertices = self.pwl_points * self.pwl_points
        triangles = [
            triangle
            for i in range(n - 1)
            for j in range(n - 1)
            for triangle in (
                ((i, j), (i + 1, j), (i + 1, j + 1)),
                ((i, j), (i, j + 1), (i + 1, j + 1)),
            )
        ]
        self.pwl_triangles = pyo.RangeSet(0, len(triangles) - 1)
        self.pwl_triangle_vertices = pyo.Set(
            self.pwl_triangles,
            dimen=2,
            initialize=lambda b, k: triangles[k],
            doc="The three grid vertices of each triangle.",
        )

        self.pwl_weight = pyo.Var(
            tb.time_index,
            self.pwl_vertices,
            bounds=(0.0, 1.0),
            initialize=0.0,
            doc="Interpolation weight of each grid vertex.",
        )
        self.pwl_triangle = pyo.Var(
            tb.time_index,
            self.pwl_triangles,
            domain=pyo.Binary,
            initialize=0,
            doc="1 for the triangle holding the operating point.",
        )
        self.membrane_off = pyo.Var(
            tb.time_index,
            domain=pyo.Binary,
            initialize=0,
            doc="1 when the train is off: no feed, permeate, or power.",
        )
        _track_binary(self, self.pwl_triangle)
        _track_binary(self, self.membrane_off)

        y, w = self.pwl_recovery, self.pwl_feed_flow

        def osmotic_at(i, j):
            return pyunits.convert(self.feed_osmotic_pressure, pyunits.Pa) / (1 - y[i])

        def feed_pressure_at(i, j):
            flux = y[i] * w[j] * flow_units / self.membrane_area
            return pyunits.convert(
                flux / self.water_permeability, pyunits.Pa
            ) + osmotic_at(i, j)

        def hp_power_at(i, j):
            work = y[i] * w[j] * flow_units * feed_pressure_at(i, j)
            return pyunits.convert(work / self.pump_efficiency, pyunits.kW)

        def circulation_power_at(i, j):
            drop = pyunits.convert(self.membrane_pressure_drop, pyunits.Pa)
            boost = (1 - self.erd_efficiency) * feed_pressure_at(i, j)
            boost += self.erd_efficiency * drop
            work = (1 - y[i]) * w[j] * flow_units * boost
            return pyunits.convert(work / self.pump_efficiency, pyunits.kW)

        def interpolate(t, vertex_value):
            return sum(
                self.pwl_weight[t, i, j] * vertex_value(i, j)
                for i, j in self.pwl_vertices
            )

        for name, vertex_value, doc in (
            ("osmotic_pressure", osmotic_at, "Brine-end osmotic pressure."),
            ("feed_pressure", feed_pressure_at, "Transmembrane feed pressure."),
            ("power_hp_pump", hp_power_at, "High-pressure pump power."),
            (
                "power_circulation_pump",
                circulation_power_at,
                "Circulation (booster) pump power.",
            ),
        ):
            self.add_component(
                name,
                pyo.Expression(
                    tb.time_index,
                    rule=lambda b, t, f=vertex_value: interpolate(t, f),
                    doc=f"{doc} Interpolated on the membrane grid.",
                ),
            )

        @self.Constraint(tb.time_index, doc="One triangle, or off.")
        def pwl_triangle_choice(b, t):
            return (
                sum(b.pwl_triangle[t, k] for k in b.pwl_triangles) + (b.membrane_off[t])
                == 1
            )

        @self.Constraint(tb.time_index, doc="Weights sum to 1 when on, 0 when off.")
        def pwl_convexity(b, t):
            return sum(b.pwl_weight[t, v] for v in b.pwl_vertices) == (
                1 - b.membrane_off[t]
            )

        @self.Constraint(
            tb.time_index,
            self.pwl_vertices,
            doc="A vertex carries weight only if its triangle is chosen.",
        )
        def pwl_adjacency(b, t, i, j):
            return b.pwl_weight[t, i, j] <= sum(
                b.pwl_triangle[t, k]
                for k in b.pwl_triangles
                if (i, j) in b.pwl_triangle_vertices[k]
            )

        @self.Constraint(tb.time_index, doc="Feed interpolated on the grid.")
        def pwl_feed_definition(b, t):
            return feed[t] == interpolate(t, lambda i, j: w[j] * flow_units)

        @self.Constraint(tb.time_index, doc="Permeate interpolated on the grid.")
        def pwl_permeate_definition(b, t):
            return permeate[t] == interpolate(t, lambda i, j: y[i] * w[j] * flow_units)

        self.split_definition.deactivate()
        power = self.declare_power(nm.PowerKind.ELECTRICAL)
        self.register_io_variable(power, role="output")

        @self.Constraint(tb.time_index, doc="power == HP pump + circulation pump.")
        def power_electrical_relation(b, t):
            return power[t] == b.power_hp_pump[t] + b.power_circulation_pump[t]

        self.register_relation(self.power_electrical_relation, target=power)
