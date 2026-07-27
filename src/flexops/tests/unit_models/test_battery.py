"""Harness-driven and hand tests for BatteryModel (M08, §3.4/§3.6, R4/R9)."""

import datetime
from pathlib import Path

import pyomo.environ as pyo
import pytest
from pyomo.environ import units as pyunits
from pyomo.opt import assert_optimal_termination

from flexcore.config.schema import UnitCommitmentConfig
from flexcore.exceptions import FlexConfigError
from flexops import SimpleAqueousFlow
from flexops.core.time_block import TimeBlock
from flexops.costing import FlexCosting
from flexops.logic import relax
from flexops.testing import UnitModelTestHarness, dummy_time_block
from flexops.unit_models import BatteryModel, Pump

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_TARIFF_JSON = _FIXTURES / "tariff_tou_demo.json"
_PEAK_HOURS = (16, 17, 18, 19, 20)


class TestBatteryModel(UnitModelTestHarness):
    """capacity fixed, power_charge/power_discharge are the harness's dispatch inputs.

    Unit commitment ``status`` is disabled for this harness build so the plain
    build/units/registration/DoF/solve mechanics stay a clean LP; the
    mutually-exclusive charge/discharge MIP behavior gets its own dedicated
    tests below.
    """

    expected_dof = 0

    def configure(self):
        m = dummy_time_block(4)
        m.unit = BatteryModel(
            capacity=10 * pyunits.kWh,
            unit_commitment=UnitCommitmentConfig(status=False),
        )
        return m, m.unit


def _battery(n: int = 4, **kwargs) -> tuple[pyo.ConcreteModel, BatteryModel]:
    """Build a fresh BatteryModel (status disabled by default) on an n-point block."""
    m = dummy_time_block(n)
    kwargs.setdefault("unit_commitment", UnitCommitmentConfig(status=False))
    kwargs.setdefault("capacity", 10 * pyunits.kWh)
    m.unit = BatteryModel(**kwargs)
    return m, m.unit


def _battery_with_costing(
    n: int = 4, tariff=None, baseload_power_kw: float | None = None, **kwargs
) -> pyo.ConcreteModel:
    """Build an hourly-resolution BatteryModel wired to a FlexCosting block.

    When ``baseload_power_kw`` is given, a flat electrical baseload (a ``Pump``
    fixed to a constant draw at every timestep) is added to the model before
    ``cost_process`` aggregates power, so the facility's net electrical draw
    stays >= 0 across the horizon. This is required for the arbitrage tests --
    see :func:`_build_arbitrage_model` for why.
    """
    start = datetime.datetime(2025, 7, 8)
    end = start + datetime.timedelta(hours=n)
    m = pyo.ConcreteModel()
    m.time_block = TimeBlock(
        start_date=start.isoformat(), end_date=end.isoformat(), time_step=1 * pyunits.hr
    )
    if tariff is None:
        m.costing = FlexCosting(time_block=m.time_block, tariff_file=str(_TARIFF_JSON))
    else:
        m.costing = FlexCosting(time_block=m.time_block, tariff=tariff)
    kwargs.setdefault("unit_commitment", UnitCommitmentConfig(status=False))
    kwargs.setdefault("capacity", 100 * pyunits.kWh)
    m.battery = BatteryModel(costing_package=m.costing, **kwargs)

    if baseload_power_kw is not None:
        # Flat baseload so the facility net draw stays >= 0. Constant-intensity
        # pump: power = energy_intensity * flow; with energy_intensity 1 kWh/m^3
        # the fixed flow (m^3/hr) numerically equals the power draw (kW). Fix its
        # operation at every timestep -- built before cost_process(), which pulls
        # power from the model at call time.
        m.properties = SimpleAqueousFlow(fixed_density=True)
        m.baseload = Pump(
            property_package=m.properties,
            energy_intensity=1.0 * pyunits.kWh / pyunits.m**3,
        )
        for t in m.time_block.time_index:
            m.baseload.inlet_state.flow_vol_phase[t, "Liq"].fix(baseload_power_kw)

    m.costing.cost_process()
    return m


@pytest.mark.unit
def test_soc_constraint_bodies():
    """charge_balance bodies evaluate to 0 on a hand-computed trajectory.

    t=0 is governed by charge_balance too (referencing charge_init in place of
    charge[-1]) -- otherwise power_charge[0]/power_discharge[0] would be free
    of any energy-conservation tie (see the class docstring's "Deviations").
    """
    m, unit = _battery(4, eta_charge=0.9, eta_discharge=0.8)
    dt_hr = pyo.value(pyunits.convert(m.time_block.dt, pyunits.hr))

    power_charge = [5.0, 0.0, 0.0, 2.0]
    power_discharge = [0.0, 3.0, 1.0, 0.0]
    charge = []
    previous = 5.0  # charge_init == initial_soc(0.5) * capacity(10).
    for t in range(4):
        previous = previous + dt_hr * (0.9 * power_charge[t] - power_discharge[t] / 0.8)
        charge.append(previous)

    for t in m.time_block.time_index:
        unit.power_charge[t].set_value(power_charge[t])
        unit.power_discharge[t].set_value(power_discharge[t])
        unit.charge[t].set_value(charge[t])

    assert len(unit.charge_balance) == 4
    for t in unit.charge_balance:
        assert pyo.value(unit.charge_balance[t].body) == pytest.approx(0.0, abs=1e-9)


@pytest.mark.unit
def test_soc_bounds_are_constraints_not_var_bounds():
    """soc_lower/soc_upper bound charge (kWh) via Constraints referencing the
    capacity Var -- Pitfall 1: a literal Var bound can't reference another Var."""
    _, unit = _battery(4, soc_min=0.2, soc_max=0.8)
    capacity_val = pyo.value(unit.capacity)

    unit.charge[0].set_value(0.2 * capacity_val)
    assert pyo.value(unit.soc_lower[0].body) == pytest.approx(
        pyo.value(unit.soc_lower[0].upper), abs=1e-9
    )
    unit.charge[0].set_value(0.8 * capacity_val)
    assert pyo.value(unit.soc_upper[0].body) == pytest.approx(
        pyo.value(unit.soc_upper[0].upper), abs=1e-9
    )
    # soc itself carries no literal bounds -- it is a reporting Expression
    # (charge / capacity), never referenced by a Constraint (see class docstring).
    assert pyo.value(unit.soc[0]) == pytest.approx(0.8, rel=1e-9)


@pytest.mark.unit
def test_capacity_fix_unfix():
    """capacity is fixed at construction; costing modes toggle it (R4)."""
    m = _battery_with_costing()
    assert m.battery.capacity.fixed
    assert pyo.value(m.battery.capacity) == pytest.approx(100.0)

    m.costing.set_design_mode()
    assert not m.battery.capacity.fixed

    m.costing.set_operations_mode()
    assert m.battery.capacity.fixed
    assert pyo.value(m.battery.capacity) == pytest.approx(100.0)


@pytest.mark.unit
def test_battery_forces_nothing_and_status_defaults_true():
    """Unlike Tank, a battery does not force unit_commitment.status off."""
    _, unit = _battery(
        4,
        unit_commitment=UnitCommitmentConfig(),
        power_charge_max=5 * pyunits.kW,
        power_discharge_max=5 * pyunits.kW,
    )
    assert unit.config.unit_commitment.status is True
    assert hasattr(unit, "status")


@pytest.mark.unit
def test_status_requires_both_power_maxima():
    """Enabling status without both power maxima raises FlexConfigError."""
    with pytest.raises(FlexConfigError):
        _battery(4, unit_commitment=UnitCommitmentConfig(status=True))
    with pytest.raises(FlexConfigError):
        _battery(
            4,
            unit_commitment=UnitCommitmentConfig(status=True),
            power_charge_max=5 * pyunits.kW,
        )


@pytest.mark.unit
def test_status_enables_mutually_exclusive_charge_discharge():
    """With status enabled, charge/discharge are tied to one Binary (relax-tracked)."""
    _, unit = _battery(
        4,
        unit_commitment=UnitCommitmentConfig(status=True),
        power_charge_max=5 * pyunits.kW,
        power_discharge_max=5 * pyunits.kW,
    )
    assert unit.status[0].domain is pyo.Binary

    # status=1: charge may take its full max, discharge is forced to 0.
    unit.status[0].set_value(1)
    unit.power_charge[0].set_value(5.0)
    unit.power_discharge[0].set_value(0.0)
    assert (
        pyo.value(unit.status_max_link[0].body)
        <= pyo.value(unit.status_max_link[0].upper) + 1e-9
    )
    assert (
        pyo.value(unit.discharge_exclusivity[0].body)
        <= pyo.value(unit.discharge_exclusivity[0].upper) + 1e-9
    )

    # status=0: charge is forced to 0, discharge may take its full max.
    unit.status[0].set_value(0)
    unit.power_charge[0].set_value(0.0)
    unit.power_discharge[0].set_value(5.0)
    assert (
        pyo.value(unit.status_max_link[0].body)
        <= pyo.value(unit.status_max_link[0].upper) + 1e-9
    )
    assert (
        pyo.value(unit.discharge_exclusivity[0].body)
        <= pyo.value(unit.discharge_exclusivity[0].upper) + 1e-9
    )

    relax(unit)
    assert unit.status[0].domain is pyo.UnitInterval


@pytest.mark.unit
def test_external_dispatch_fixes_power_not_sizing():
    """set_dispatch fixes power_charge/power_discharge; capacity stays a free var."""
    m = _battery_with_costing()
    battery = m.battery
    series = {0: 10.0, 1: -5.0, 2: 0.0, 3: 8.0}
    battery.set_dispatch(series)

    for t, v in series.items():
        assert battery.power_charge[t].fixed
        assert battery.power_discharge[t].fixed
        if v >= 0:
            assert pyo.value(battery.power_charge[t]) == pytest.approx(v)
            assert pyo.value(battery.power_discharge[t]) == pytest.approx(0.0)
        else:
            assert pyo.value(battery.power_charge[t]) == pytest.approx(0.0)
            assert pyo.value(battery.power_discharge[t]) == pytest.approx(-v)

    m.costing.set_design_mode()
    assert not battery.capacity.fixed


@pytest.mark.component
@pytest.mark.needs_highs
def test_external_dispatch_sizing_only_solve():
    """With dispatch fixed and design mode on, capacity still optimizes (feasibly)."""
    from flexcore.solvers import get_solver

    m = _battery_with_costing()
    battery = m.battery
    series = {t: (5.0 if t % 2 == 0 else -5.0) for t in m.time_block.time_index}
    battery.set_dispatch(series)
    m.costing.set_design_mode()
    m.objective = pyo.Objective(expr=m.costing.total_cost)

    results = get_solver(model=m, prefer="highs").solve(m)
    assert_optimal_termination(results)
    assert not battery.capacity.fixed
    required_capacity = max(
        pyo.value(battery.charge[t]) for t in m.time_block.time_index
    )
    assert pyo.value(battery.capacity) >= required_capacity - 1e-6


_BATTERY_POWER_MAX_KW = 50.0


def _build_arbitrage_model() -> pyo.ConcreteModel:
    """24-step battery + flat baseload vs. the demo TOU tariff; objective = opex.

    Demand-charge entries are dropped from the tariff: a demand charge's
    near-fixed cost per kW of peak power (applied to the very first kW drawn)
    swamps the two-level energy arbitrage this test targets (M06's
    ``test_demand_charge_reduces_peak`` exercises demand charges directly). The
    milestone spec's own framing -- "vs. a two-level TOU price" -- is exactly
    the pure energy-tariff case.

    The battery is paired with a flat electrical baseload at the battery's max
    (dis)charge power. This is required, not decorative: EECO computes the
    in-objective energy cost as ``max_pos(sum_t price[t]*power[t]*dt)`` -- a
    horizon total clamped at zero. A battery-only facility can drive that total
    non-positive (by discharging its stored energy), so the clamp pins energy
    cost to 0 and the objective goes flat -- the solver then has no gradient to
    arbitrage and leaves the battery idle. A flat baseload sized to the
    battery's max power keeps the net draw >= 0 (a full-rate discharge floors it
    at 0, never exporting), so the energy cost stays in its linear regime and
    charging off-peak / discharging on-peak strictly lowers cost.
    """
    from flexops.costing import load_tariff

    energy_only = load_tariff(_TARIFF_JSON)
    energy_only = energy_only[energy_only["type"] != "demand"].reset_index(drop=True)

    m = _battery_with_costing(
        n=24,
        tariff=energy_only,
        baseload_power_kw=_BATTERY_POWER_MAX_KW,
        power_charge_max=_BATTERY_POWER_MAX_KW * pyunits.kW,
        power_discharge_max=_BATTERY_POWER_MAX_KW * pyunits.kW,
        eta_charge=0.95,
        eta_discharge=0.95,
        unit_commitment=UnitCommitmentConfig(status=True),
    )
    m.objective = pyo.Objective(expr=m.costing.aggregate_operating_cost)
    return m


@pytest.mark.component
@pytest.mark.needs_highs
def test_battery_arbitrage_mip():
    """The MIP arbitrages the TOU tariff: charges off-peak, discharges on-peak."""
    from flexcore.solvers import get_solver

    m = _build_arbitrage_model()
    results = get_solver(model=m, prefer="highs").solve(m)
    assert_optimal_termination(results)

    battery = m.battery
    peak_charge = sum(pyo.value(battery.power_charge[t]) for t in _PEAK_HOURS)
    peak_discharge = sum(pyo.value(battery.power_discharge[t]) for t in _PEAK_HOURS)
    offpeak_discharge = sum(
        pyo.value(battery.power_discharge[t])
        for t in m.time_block.time_index
        if t not in _PEAK_HOURS
    )
    assert peak_charge == pytest.approx(0.0, abs=1e-6)
    assert peak_discharge > 1.0
    assert offpeak_discharge == pytest.approx(0.0, abs=1e-6)


@pytest.mark.component
@pytest.mark.needs_highs
def test_arbitrage_relaxation_bounds_mip():
    """relax() gives an LP whose optimal objective bounds the MIP's from below."""
    from flexcore.solvers import get_solver

    m_mip = _build_arbitrage_model()
    mip_results = get_solver(model=m_mip, prefer="highs").solve(m_mip)
    assert_optimal_termination(mip_results)
    mip_objective = pyo.value(m_mip.objective)

    m_lp = _build_arbitrage_model()
    relax(m_lp.battery)
    lp_results = get_solver(model=m_lp, prefer="highs").solve(m_lp)
    assert_optimal_termination(lp_results)
    lp_objective = pyo.value(m_lp.objective)

    assert lp_objective <= mip_objective + 1e-6
