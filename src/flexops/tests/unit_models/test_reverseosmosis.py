"""ReverseOsmosis: feed -> permeate + brine (§3.4, R6)."""

import pyomo.environ as pyo
import pytest
from pyomo.environ import units as pyunits

from flexcore.config.schema import SurrogateSpec, SurrogateType
from flexcore.exceptions import FlexConfigError
from flexops import SimpleAqueousFlow, TimeBlock
from flexops.surrogates import surrogate_from_spec
from flexops.testing import UnitModelTestHarness, dummy_time_block
from flexops.unit_models import ReverseOsmosis
from flexops.unit_models.reverseosmosis import FeedWaterType, MembraneModel


class TestReverseOsmosis(UnitModelTestHarness):
    """RO skid: feed -> permeate (outlet_a) + brine (outlet_b)."""

    expected_dof = 0

    def configure(self):
        m = dummy_time_block(3)
        m.unit = ReverseOsmosis(property_package=m.properties)
        return m, m.unit


@pytest.mark.unit
def test_reverseosmosis_outlet_semantics():
    """The skid's ``recovery`` is its permeate fraction of the feed."""
    m = dummy_time_block(3)
    m.unit = ReverseOsmosis(property_package=m.properties, recovery=0.45)
    for t in m.time_block.time_index:
        m.unit.feed[t].fix(4.0)
        m.unit.permeate[t].fix(1.8)
        m.unit.brine[t].fix(2.2)
        assert pyo.value(m.unit.split_definition[t].body) == pytest.approx(
            1.8 - 0.45 * 4.0, abs=1e-9
        )
        assert pyo.value(m.unit.split_mass_balance[t].body) == pytest.approx(
            0.0, abs=1e-9
        )


@pytest.mark.component
def test_recovery_relation_is_swappable():
    """A richer recovery model replaces the split definition in place.

    ``split_definition`` (determining ``permeate``, RO's name for
    ``flow_out_a``) is registered as a swappable relation (M10, extended
    beyond energy relationships). Conservation (``split_mass_balance``) is
    never registered and so is untouched by the swap.
    """
    m = pyo.ConcreteModel()
    m.time_block = TimeBlock(
        start_date="2025-01-01", end_date="2025-01-01T00:45", time_step=15 * pyunits.min
    )
    m.properties = SimpleAqueousFlow(has_pressure=True)
    m.unit = ReverseOsmosis(property_package=m.properties)

    m.unit.swap_relation(
        "split_definition",
        surrogate_from_spec(
            SurrogateSpec(
                surrogate_type=SurrogateType.MULTILINEAR,
                data={
                    "input_variables": {
                        "feed": "m^3/hr",
                        "inlet_state.pressure": "Pa",
                    },
                    "output_variables": {"permeate": "m^3/hr"},
                    "coefficients": {
                        "intercept": 0.3,
                        "feed": 0.01,
                        "inlet_state.pressure": 1e-6,
                        "feed*inlet_state.pressure": 1e-7,
                    },
                },
            )
        ),
    )

    m.unit.inlet_state.pressure[0].set_value(3.0e5)
    m.unit.feed[0].set_value(10.0)
    m.unit.permeate[0].set_value(0.0)

    assert m.unit.split_definition[0].active is False
    assert m.unit.split_mass_balance[0].active is True
    # permeate - (0.3 + 0.01*10 + 1e-6*3e5 + 1e-7*10*3e5) == -(0.3+0.1+0.3+0.3)
    assert pyo.value(m.unit.surrogate_split_definition.fitted[0].body) == pytest.approx(
        -1.0
    )


@pytest.mark.unit
def test_reverseosmosis_renames_the_split_fraction_to_recovery():
    """``recovery`` replaces the inherited ``split_fraction`` — it is not an alias."""
    m = dummy_time_block(3)
    m.unit = ReverseOsmosis(property_package=m.properties)

    assert pyo.value(m.unit.recovery) == pytest.approx(0.45)
    assert m.unit.find_component("split_fraction") is None
    with pytest.raises(ValueError, match="split_fraction"):
        m.rejected = ReverseOsmosis(property_package=m.properties, split_fraction=0.45)


@pytest.mark.unit
def test_reverseosmosis_renames_the_flows_to_feed_permeate_brine():
    """``feed``/``permeate``/``brine`` replace ``flow_in``/``flow_out_a``/``_b``."""
    m = dummy_time_block(3)
    m.unit = ReverseOsmosis(property_package=m.properties)

    assert m.unit.find_component("flow_in") is None
    assert m.unit.find_component("flow_out_a") is None
    assert m.unit.find_component("flow_out_b") is None
    for name in ("feed", "permeate", "brine"):
        assert m.unit.find_component(name) is not None

    # Ports are unaffected by the flow rename.
    for port in ("inlet", "outlet_a", "outlet_b"):
        assert m.unit.find_component(port) is not None


@pytest.mark.unit
def test_reverseosmosis_recovery_window_bounds_the_recovery():
    """``recovery_min``/``_max`` override the default seawater window."""
    m = dummy_time_block(3)
    m.unit = ReverseOsmosis(
        property_package=m.properties,
        recovery=0.4,
        recovery_min=0.3,
        recovery_max=0.6,
    )

    assert m.unit.recovery.bounds == (0.3, 0.6)
    assert m.unit.recovery.fixed
    assert pyo.value(m.unit.recovery) == pytest.approx(0.4)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("options", "field"),
    [
        ({"recovery": 0.9, "recovery_max": 0.6}, "recovery"),
        ({"recovery_min": 0.7, "recovery_max": 0.6}, "recovery_min"),
    ],
)
def test_reverseosmosis_rejects_an_unusable_recovery_window(options, field):
    """An inverted window, or a setpoint outside it, is rejected by name."""
    m = dummy_time_block(3)
    with pytest.raises(FlexConfigError) as excinfo:
        m.unit = ReverseOsmosis(property_package=m.properties, **options)
    assert excinfo.value.field == field


@pytest.mark.unit
def test_reverseosmosis_registers_its_naming_convention_at_build():
    """The mapping RO passes up to SIDO's build is the one the unit resolves through."""
    m = dummy_time_block(3)
    m.unit = ReverseOsmosis(property_package=m.properties)

    assert m.unit._component_names == {
        "flow_in": "feed",
        "flow_out_a": "permeate",
        "flow_out_b": "brine",
        "split_fraction": "recovery",
    }


@pytest.mark.unit
def test_reverseosmosis_energy_intensity_is_per_permeate():
    """``energy_intensity`` is per m^3 of permeate, not of feed."""
    m = dummy_time_block(3)
    m.unit = ReverseOsmosis(
        property_package=m.properties,
        recovery=0.5,
        energy_intensity=4.0 * pyunits.kWh / pyunits.m**3,
    )
    t = m.time_block.time_index.first()
    m.unit.feed[t].fix(100.0)
    m.unit.permeate[t].fix(50.0)
    m.unit.power_electrical[t].fix(200.0)  # 4.0 * permeate, not 4.0 * feed

    assert pyo.value(m.unit.power_electrical_relation[t].body) == pytest.approx(
        0.0, abs=1e-9
    )


def _linearized_ro(**options):
    """Build a 3-period seawater RO in linearized mode on 1000 m^2 of membrane."""
    m = dummy_time_block(3)
    m.unit = ReverseOsmosis(
        property_package=m.properties,
        membrane_model=MembraneModel.LINEARIZED,
        membrane_area=1000.0 * pyunits.m**2,
        **options,
    )
    return m


def _select_vertex(unit, t, i, j):
    """Put all of period t's interpolation weight on vertex (i, j)."""
    for index in unit.pwl_weight:
        if index[0] == t:
            unit.pwl_weight[index].set_value(0.0)
    unit.pwl_weight[t, i, j].set_value(1.0)
    unit.membrane_off[t].set_value(0)
    for k in unit.pwl_triangles:
        unit.pwl_triangle[t, k].set_value(0)
    containing = [
        k for k in unit.pwl_triangles if (i, j) in unit.pwl_triangle_vertices[k]
    ]
    unit.pwl_triangle[t, containing[0]].set_value(1)


def _holds(con):
    """Whether a ConstraintData holds at the Vars' current values."""
    body = pyo.value(con.body)
    lower = -float("inf") if con.lower is None else pyo.value(con.lower)
    upper = float("inf") if con.upper is None else pyo.value(con.upper)
    return lower - 1e-6 <= body <= upper + 1e-6


@pytest.mark.unit
@pytest.mark.parametrize(
    ("feed_water", "recovery", "window", "flux"),
    [
        (FeedWaterType.SEAWATER, 0.45, (0.35, 0.5), (12.0, 19.0)),
        (FeedWaterType.BRACKISH, 0.75, (0.65, 0.85), (20.0, 34.0)),
    ],
)
def test_reverseosmosis_feed_water_presets_set_the_literature_windows(
    feed_water, recovery, window, flux
):
    """Each feed water preset supplies its own recovery and flux defaults."""
    m = dummy_time_block(3)
    m.unit = ReverseOsmosis(
        property_package=m.properties,
        feed_water=feed_water,
        membrane_area=1000.0 * pyunits.m**2,
    )

    assert pyo.value(m.unit.recovery) == pytest.approx(recovery)
    assert m.unit.recovery.bounds == pytest.approx(window)
    assert (pyo.value(m.unit.flux_min), pyo.value(m.unit.flux_max)) == pytest.approx(
        flux
    )


@pytest.mark.unit
def test_reverseosmosis_explicit_option_overrides_the_preset():
    """An option given explicitly wins over the feed water preset."""
    m = dummy_time_block(3)
    m.unit = ReverseOsmosis(
        property_package=m.properties,
        feed_water=FeedWaterType.BRACKISH,
        recovery=0.6,
        recovery_min=0.5,
    )

    assert m.unit.recovery.bounds == pytest.approx((0.5, 0.85))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("option", "value"),
    [("feed_water", "lake"), ("membrane_model", "spiral")],
)
def test_reverseosmosis_rejects_an_unknown_enum_value(option, value):
    """An unknown feed water or membrane model is rejected by name."""
    m = dummy_time_block(3)
    with pytest.raises(ValueError, match=option):
        m.unit = ReverseOsmosis(property_package=m.properties, **{option: value})


@pytest.mark.unit
def test_reverseosmosis_flux_limits_need_a_membrane_area():
    """Without ``membrane_area`` no flux limit is built."""
    m = dummy_time_block(3)
    m.unit = ReverseOsmosis(property_package=m.properties)

    assert m.unit.find_component("flux_min_limit") is None
    assert m.unit.find_component("flux_max_limit") is None


@pytest.mark.unit
def test_reverseosmosis_flux_limits_bound_permeate_per_area():
    """Seawater flux 12-19 LMH on 1000 m^2 keeps permeate within 12-19 m^3/h."""
    m = dummy_time_block(3)
    m.unit = ReverseOsmosis(
        property_package=m.properties, membrane_area=1000.0 * pyunits.m**2
    )
    t = m.time_block.time_index.first()

    m.unit.permeate[t].set_value(15.0)
    assert _holds(m.unit.flux_min_limit[t]) and _holds(m.unit.flux_max_limit[t])
    m.unit.permeate[t].set_value(20.0)
    assert not _holds(m.unit.flux_max_limit[t])
    m.unit.permeate[t].set_value(11.0)
    assert not _holds(m.unit.flux_min_limit[t])


@pytest.mark.unit
def test_reverseosmosis_linearized_requires_a_membrane_area():
    """The linearized membrane model needs an area for its flux equation."""
    m = dummy_time_block(3)
    with pytest.raises(FlexConfigError) as excinfo:
        m.unit = ReverseOsmosis(
            property_package=m.properties, membrane_model=MembraneModel.LINEARIZED
        )
    assert excinfo.value.field == "membrane_area"


@pytest.mark.unit
def test_reverseosmosis_linearized_replaces_the_fixed_recovery_split():
    """Linearized mode deactivates the split and registers its power relation."""
    m = _linearized_ro()

    assert not any(con.active for con in m.unit.split_definition.values())
    assert all(con.active for con in m.unit.split_mass_balance.values())
    relations = {record.name for record in m.unit._io_registry.relations}
    assert "power_electrical_relation" in relations
    assert m.unit.find_component("energy_intensity") is None


@pytest.mark.unit
def test_reverseosmosis_linearized_is_unit_consistent():
    """Every linearized-mode expression and constraint is dimensionally consistent."""
    from pyomo.util.check_units import assert_units_consistent

    m = _linearized_ro()
    assert_units_consistent(m.unit)


@pytest.mark.unit
@pytest.mark.parametrize("erd_efficiency", [0.96, 0.0])
def test_reverseosmosis_linearized_is_exact_at_a_vertex(erd_efficiency):
    """At a grid vertex the interpolation equals the nonlinear membrane model."""
    m = _linearized_ro(erd_efficiency=erd_efficiency)
    unit = m.unit
    t = m.time_block.time_index.first()
    i, j = 2, 3
    _select_vertex(unit, t, i, j)
    y = unit.pwl_recovery[i]
    feed = unit.pwl_feed_flow[j]
    permeate = y * feed

    # Seawater preset: A = 1.2 LMH/bar, pi_f = 27 bar, eta_pump = 0.8.
    delta_h = permeate * 1000.0 / (1.2 * 1000.0) + 27.0 / (1.0 - y)
    boost = (1.0 - erd_efficiency) * delta_h + erd_efficiency * 1.5
    bar_m3_per_h_to_kw = 1e5 / 3600.0 / 1000.0
    hp = permeate * delta_h / 0.8 * bar_m3_per_h_to_kw
    circulation = (feed - permeate) * boost / 0.8 * bar_m3_per_h_to_kw

    unit.permeate[t].set_value(permeate)
    unit.feed[t].set_value(feed)
    assert _holds(unit.pwl_permeate_definition[t])
    assert _holds(unit.pwl_feed_definition[t])
    assert pyo.value(unit.osmotic_pressure[t]) == pytest.approx(27.0e5 / (1.0 - y))
    assert pyo.value(unit.feed_pressure[t]) == pytest.approx(delta_h * 1e5)
    assert pyo.value(unit.power_hp_pump[t]) == pytest.approx(hp)
    assert pyo.value(unit.power_circulation_pump[t]) == pytest.approx(circulation)
    for con in (
        unit.pwl_triangle_choice[t],
        unit.pwl_convexity[t],
        *(unit.pwl_adjacency[t, a, b] for (a, b) in unit.pwl_vertices),
    ):
        assert _holds(con)


@pytest.mark.unit
def test_reverseosmosis_linearized_off_state_has_no_flow_or_power():
    """The off vertex zeroes feed, permeate, pressure, and pump power."""
    m = _linearized_ro()
    unit = m.unit
    t = m.time_block.time_index.first()
    for index in unit.pwl_weight:
        unit.pwl_weight[index].set_value(0.0)
    for k in unit.pwl_triangles:
        unit.pwl_triangle[t, k].set_value(0)
    unit.membrane_off[t].set_value(1)
    unit.feed[t].set_value(0.0)
    unit.permeate[t].set_value(0.0)

    assert _holds(unit.pwl_triangle_choice[t])
    assert _holds(unit.pwl_convexity[t])
    assert _holds(unit.pwl_feed_definition[t])
    assert _holds(unit.pwl_permeate_definition[t])
    assert _holds(unit.flux_min_limit[t])
    assert pyo.value(unit.feed_pressure[t]) == pytest.approx(0.0)
    assert pyo.value(unit.power_hp_pump[t] + unit.power_circulation_pump[t]) == 0.0


@pytest.mark.unit
def test_reverseosmosis_linearized_binaries_follow_relax():
    """``relax``/``unrelax`` switch the triangle and off binaries."""
    from flexops.logic.status import relax, unrelax

    m = _linearized_ro()
    t = m.time_block.time_index.first()
    relax(m.unit)
    assert m.unit.membrane_off[t].domain is pyo.UnitInterval
    assert m.unit.pwl_triangle[t, 0].domain is pyo.UnitInterval
    unrelax(m.unit)
    assert m.unit.membrane_off[t].domain is pyo.Binary


@pytest.mark.component
@pytest.mark.needs_highs
def test_reverseosmosis_linearized_dispatch_solves_as_a_milp():
    """With feed fixed, minimizing power picks the lowest feasible permeate."""
    from flexcore.solvers import get_solver

    m = _linearized_ro()
    unit = m.unit
    times = list(m.time_block.time_index)
    for t, feed in zip(times, (40.0, 40.0, 0.0), strict=True):
        unit.feed[t].fix(feed)
    m.obj = pyo.Objective(expr=sum(unit.power_electrical[t] for t in times))

    assert_optimal = pyo.assert_optimal_termination
    assert_optimal(get_solver(model=m).solve(m))

    # Flux floor 12 LMH * 1000 m^2 = 12 m^3/h, but recovery >= 0.35 forces 14.
    assert pyo.value(unit.permeate[times[0]]) == pytest.approx(14.0, rel=1e-4)
    assert pyo.value(unit.membrane_off[times[0]]) == pytest.approx(0.0)
    assert pyo.value(unit.membrane_off[times[2]]) == pytest.approx(1.0)
    assert pyo.value(unit.power_electrical[times[2]]) == pytest.approx(0.0, abs=1e-6)

    # PWL power stays near the nonlinear model at the solved point.
    y, permeate = 0.35, 14.0
    delta_h = permeate / 1.2 + 27.0 / (1.0 - y)
    boost = 0.04 * delta_h + 0.96 * 1.5
    exact = (permeate * delta_h + (40.0 - permeate) * boost) / 0.8 / 36.0
    assert pyo.value(unit.power_electrical[times[0]]) == pytest.approx(exact, rel=0.05)
