"""Model-level parallel-train degeneracy detection tests (M08, §3.5, R8).

``PlantBlock`` itself lands in M09; degeneracy operates over any ``pyo.Block``
holding unit children (duck-typed on ``OpsBlockData``), so a bare ``pyo.Block``
stands in for "a PlantBlock/NetworkBlock" here.
"""

import pyomo.environ as pyo
import pytest
from pyomo.environ import units as pyunits
from pyomo.network import Arc

from flexcore.exceptions import FlexConfigError
from flexops.logic import (
    add_status,
    break_parallel_symmetry,
    detect_parallel_trains,
    register_parallel_group,
)
from flexops.testing import dummy_time_block
from flexops.unit_models import Pump, ReverseOsmosis


def _plant_with_units(n: int = 4):
    """Two identical Pumps + one Pump with different energy_intensity."""
    m = dummy_time_block(n)
    m.plant = pyo.Block()
    m.plant.u0 = Pump(
        property_package=m.properties, energy_intensity=0.5 * pyunits.kWh / pyunits.m**3
    )
    m.plant.u1 = Pump(
        property_package=m.properties, energy_intensity=0.5 * pyunits.kWh / pyunits.m**3
    )
    m.plant.u2 = Pump(
        property_package=m.properties, energy_intensity=0.9 * pyunits.kWh / pyunits.m**3
    )
    return m


def _plant_pump_and_ro_trains(n: int = 4):
    """One Pump feeding three parallel ReverseOsmosis trains via Arcs."""
    m = dummy_time_block(n)
    m.plant = pyo.Block()
    m.plant.pump = Pump(property_package=m.properties)
    for i in range(3):
        m.plant.add_component(f"ro{i}", ReverseOsmosis(property_package=m.properties))
    for i in range(3):
        ro = m.plant.component(f"ro{i}")
        m.plant.add_component(
            f"pump_to_ro{i}", Arc(source=m.plant.pump.outlet, destination=ro.inlet)
        )
    pyo.TransformationFactory("network.expand_arcs").apply_to(m)
    return m


@pytest.mark.unit
def test_detect_two_identical_trains():
    """Two same-class/same-config units group; the different one does not."""
    m = _plant_with_units()
    groups = detect_parallel_trains(m.plant)

    assert len(groups) == 1
    (group,) = groups
    assert {id(u) for u in group} == {id(m.plant.u0), id(m.plant.u1)}
    assert id(m.plant.u2) not in {id(u) for u in group}


@pytest.mark.unit
def test_detect_no_groups_below_two():
    """A block with only singleton (non-matching) units yields no groups."""
    m = dummy_time_block(4)
    m.plant = pyo.Block()
    m.plant.u0 = Pump(
        property_package=m.properties, energy_intensity=0.5 * pyunits.kWh / pyunits.m**3
    )
    m.plant.u1 = Pump(
        property_package=m.properties, energy_intensity=0.9 * pyunits.kWh / pyunits.m**3
    )
    assert detect_parallel_trains(m.plant) == []


@pytest.mark.unit
def test_break_symmetry_adds_ordering_constraints():
    """Ordering constraints land on the block, encode u0.status[t] >= u1.status[t]."""
    m = _plant_with_units()
    for unit in (m.plant.u0, m.plant.u1, m.plant.u2):
        add_status(unit, unit.power_electrical, 0.0, 100.0)

    count = break_parallel_symmetry(m.plant)
    assert count > 0
    assert hasattr(m.plant, "train_ordering")

    # Find the (group, pos, t) constraint for t=0 tying u0 >= u1.
    con = m.plant.train_ordering[0, 0, 0]

    def _satisfied():
        body = pyo.value(con.body)
        lower, upper = con.lower, con.upper
        ok = True
        if lower is not None:
            ok = ok and pyo.value(lower) <= body + 1e-9
        if upper is not None:
            ok = ok and body <= pyo.value(upper) + 1e-9
        return ok

    # u0 on, u1 off: satisfies the canonical ordering (u0 >= u1).
    m.plant.u0.status[0].set_value(1)
    m.plant.u1.status[0].set_value(0)
    assert _satisfied()

    # u0 off, u1 on: violates the canonical ordering.
    m.plant.u0.status[0].set_value(0)
    m.plant.u1.status[0].set_value(1)
    assert not _satisfied()


@pytest.mark.unit
def test_break_symmetry_single_unit_group_adds_nothing():
    """No group of size >= 2 -> break_parallel_symmetry adds no constraints."""
    m = dummy_time_block(4)
    m.plant = pyo.Block()
    m.plant.u0 = Pump(
        property_package=m.properties, energy_intensity=0.5 * pyunits.kWh / pyunits.m**3
    )
    m.plant.u1 = Pump(
        property_package=m.properties, energy_intensity=0.9 * pyunits.kWh / pyunits.m**3
    )

    count = break_parallel_symmetry(m.plant)
    assert count == 0
    assert not hasattr(m.plant, "train_ordering")


@pytest.mark.component
@pytest.mark.needs_highs
def test_symmetry_breaking_makes_solve_deterministic():
    """With symmetry broken, the canonical (lower-indexed) train is selected."""
    from flexcore.solvers import get_solver

    m = dummy_time_block(1)
    m.plant = pyo.Block()
    m.plant.u0 = Pump(property_package=m.properties)
    m.plant.u1 = Pump(property_package=m.properties)

    # Status gates each train's own (free) inlet flow, min_output == the full
    # demand: at most one train can be on and still satisfy demand == 50, so
    # the MILP has two solver-equivalent optima (u0 on, or u1 on) absent
    # symmetry-breaking.
    flows = {}
    for name in ("u0", "u1"):
        unit = getattr(m.plant, name)
        flow = pyo.Reference(unit.inlet_state.flow_vol_phase[:, "Liq"])
        flows[name] = flow
        add_status(
            unit,
            flow,
            50.0 * pyunits.m**3 / pyunits.hr,
            300.0 * pyunits.m**3 / pyunits.hr,
        )

    break_parallel_symmetry(m.plant)

    m.demand = pyo.Constraint(expr=flows["u0"][0] + flows["u1"][0] == 50.0)

    @m.Objective(sense=pyo.minimize)
    def total_power(b):
        return b.plant.u0.power_electrical[0] + b.plant.u1.power_electrical[0]

    results = get_solver(model=m, prefer="highs").solve(m)
    pyo.assert_optimal_termination(results)

    assert pyo.value(m.plant.u0.status[0]) == pytest.approx(1.0)
    assert pyo.value(m.plant.u1.status[0]) == pytest.approx(0.0)


@pytest.mark.unit
def test_register_parallel_group_chains_conditionals():
    """Registering 3 RO trains chains on-implications: ro0 -> ro1 -> ro2."""
    m = _plant_pump_and_ro_trains()
    for i in range(3):
        ro = m.plant.component(f"ro{i}")
        add_status(ro, ro.power_electrical, 0.0, 100.0)

    cons = register_parallel_group([m.plant.ro0, m.plant.ro1, m.plant.ro2])

    assert len(cons) == 2
    assert cons[0] is m.plant.ro0.conditional
    assert cons[1] is m.plant.ro1.conditional
    assert not hasattr(m.plant.ro2, "conditional")

    def _satisfied(con):
        body = pyo.value(con.body)
        lower, upper = con.lower, con.upper
        ok = True
        if lower is not None:
            ok = ok and pyo.value(lower) <= body + 1e-9
        if upper is not None:
            ok = ok and body <= pyo.value(upper) + 1e-9
        return ok

    # ro0 on forces ro1 on; ro1 on forces ro2 on.
    m.plant.ro0.status[0].set_value(1)
    m.plant.ro1.status[0].set_value(1)
    m.plant.ro2.status[0].set_value(1)
    assert _satisfied(m.plant.ro0.conditional[0])
    assert _satisfied(m.plant.ro1.conditional[0])

    # ro0 on, ro1 off: violates the ro0 -> ro1 link.
    m.plant.ro1.status[0].set_value(0)
    assert not _satisfied(m.plant.ro0.conditional[0])


@pytest.mark.unit
def test_register_parallel_group_then_off():
    """then='off': registering two RO trains ties ro0 on to ro1 off."""
    m = _plant_pump_and_ro_trains()
    for i in range(2):
        ro = m.plant.component(f"ro{i}")
        add_status(ro, ro.power_electrical, 0.0, 100.0)

    register_parallel_group([m.plant.ro0, m.plant.ro1], then="off")

    m.plant.ro0.status[0].set_value(1)
    m.plant.ro1.status[0].set_value(1)
    body = pyo.value(m.plant.ro0.conditional[0].body)
    upper = pyo.value(m.plant.ro0.conditional[0].upper)
    assert body > upper + 1e-9


@pytest.mark.unit
def test_register_parallel_group_requires_at_least_two_units():
    """A single-unit list is not a group -> FlexConfigError."""
    m = _plant_pump_and_ro_trains()
    add_status(m.plant.ro0, m.plant.ro0.power_electrical, 0.0, 100.0)

    with pytest.raises(FlexConfigError):
        register_parallel_group([m.plant.ro0])


@pytest.mark.unit
def test_register_parallel_group_rejects_duplicate_units():
    """The same unit repeated in the list -> FlexConfigError."""
    m = _plant_pump_and_ro_trains()
    for i in range(2):
        ro = m.plant.component(f"ro{i}")
        add_status(ro, ro.power_electrical, 0.0, 100.0)

    with pytest.raises(FlexConfigError):
        register_parallel_group([m.plant.ro0, m.plant.ro1, m.plant.ro0])
