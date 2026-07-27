"""Unit-tier tests for FlexCosting: the opex/capex block structure (M07).

These tests exercise the wrapper — aggregation, the ``opex`` block (electricity
+ fuel + fixed operating cost), the empty ``capex`` placeholder, the
capex-in-objective-only-in-design-mode rule, ``report_cost``'s categorized
breakdown, the DR container no-op, modes, and the construction-order invariant.
None of them invoke a solver (that is ``test_load_shifting_component.py``). The
tariff *math* itself is EECO's and is tested in M06.
"""

from pathlib import Path

import pyomo.environ as pyo
import pytest
from pyomo.environ import units as pyunits
from pyomo.network import Arc

import flexops as fo
from flexcore.exceptions import FlexConfigError
from flexcore.solvers import ProblemClass, classify
from flexops.core.time_block import TimeBlock
from flexops.costing import (
    CapitalCostBreakdown,
    CostReport,
    FlexCosting,
    OperatingCostBreakdown,
    load_tariff,
)
from flexops.properties.simple_aqueous import SimpleAqueousFlow
from flexops.unit_models import Pump, Tank

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_TARIFF_JSON = _FIXTURES / "tariff_tou_demo.json"
_DR_JSON = _FIXTURES / "dr_events_demo.json"


def _time_model() -> pyo.ConcreteModel:
    """A model with a 24-hour, hourly TimeBlock over 2025-07-08 + properties."""
    m = pyo.ConcreteModel()
    m.time_block = TimeBlock(
        start_date="2025-07-08", end_date="2025-07-09", time_step=1 * pyunits.hr
    )
    m.properties = SimpleAqueousFlow(fixed_density=True)
    return m


def _pump_tank_costing(
    *,
    costing_first: bool = True,
    pump_first: bool = True,
    fixed_operating_cost: float = 0.0,
    dr_event_file=None,
    run_cost_process: bool = True,
) -> pyo.ConcreteModel:
    """Build a Pump -> Arc -> Tank system with a FlexCosting block.

    ``costing_first`` / ``pump_first`` permute the component-creation order (the
    construction-order invariant). When costing is created after the units they
    are built with ``costing_package=None`` (aggregation pulls from the model, so
    the association is not required).
    """
    m = _time_model()

    def add_costing() -> None:
        m.costing = FlexCosting(
            time_block=m.time_block,
            tariff_file=str(_TARIFF_JSON),
            fixed_operating_cost=fixed_operating_cost,
            dr_event_file=dr_event_file,
        )

    def add_units() -> None:
        cp = getattr(m, "costing", None)
        pump = Pump(
            property_package=m.properties,
            energy_intensity=0.5 * pyunits.kWh / pyunits.m**3,
            costing_package=cp,
        )
        tank = Tank(
            property_package=m.properties,
            max_volume=1000 * pyunits.m**3,
            initial_volume=200 * pyunits.m**3,
        )
        if pump_first:
            m.pump, m.tank = pump, tank
        else:
            m.tank, m.pump = tank, pump
        m.arc = Arc(source=m.pump.outlet, destination=m.tank.inlet)
        pyo.TransformationFactory("network.expand_arcs").apply_to(m)

    if costing_first:
        add_costing()
        add_units()
    else:
        add_units()
        add_costing()

    if run_cost_process:
        m.costing.cost_process()
    return m


def _set_power(m: pyo.ConcreteModel, profile: dict[int, float]) -> None:
    """Set the pump's ``power_electrical`` values directly (no solve)."""
    for t, val in profile.items():
        m.pump.power_electrical[t].set_value(val)


@pytest.mark.unit
def test_config_exclusivity():
    """Both or neither of tariff_file/tariff -> FlexConfigError naming the options."""
    m = _time_model()
    with pytest.raises(FlexConfigError, match="tariff"):
        m.costing = FlexCosting(time_block=m.time_block)  # neither

    m2 = _time_model()
    with pytest.raises(FlexConfigError, match="tariff"):
        m2.costing = FlexCosting(
            time_block=m2.time_block,
            tariff_file=str(_TARIFF_JSON),
            tariff=load_tariff(_TARIFF_JSON),  # both
        )


@pytest.mark.unit
def test_fo_exports_flexcosting():
    """FlexCosting is reachable as fo.FlexCosting (API-freeze name)."""
    assert fo.FlexCosting is FlexCosting


@pytest.mark.unit
def test_construct_before_units():
    """FlexCosting builds and cost_process runs on a bare TimeBlock model."""
    m = _time_model()
    m.costing = FlexCosting(time_block=m.time_block, tariff_file=str(_TARIFF_JSON))
    m.costing.cost_process()

    assert m.costing.find_component("aggregate_electrical_power") is not None
    assert m.costing.find_component("opex") is not None
    assert m.costing.find_component("capex") is not None
    # Empty registry -> the 0*kW placeholder body -> zero everywhere.
    for t in m.time_block.time_index:
        assert pyo.value(m.costing.aggregate_electrical_power[t]) == pytest.approx(0.0)


@pytest.mark.unit
def test_aggregate_electrical_power():
    """aggregate_electrical_power sums the registered units' power_electrical."""
    m = _pump_tank_costing()
    profile = {t: float(t) for t in m.time_block.time_index}
    _set_power(m, profile)
    for t in (0, 5, 16, 23):
        expected = pyo.value(m.pump.power_electrical[t])
        assert pyo.value(m.costing.aggregate_electrical_power[t]) == pytest.approx(
            expected
        )


@pytest.mark.unit
def test_opex_block_line_items():
    """The opex block exposes electricity/fuel/fixed and their sum."""
    m = _pump_tank_costing(fixed_operating_cost=250.0)
    _set_power(m, {t: 100.0 for t in m.time_block.time_index})
    opex = m.costing.opex

    assert pyo.value(opex.fuel_cost) == pytest.approx(0.0)  # no gas unit
    assert pyo.value(opex.fixed_operating_cost) == pytest.approx(250.0)
    assert pyo.value(opex.total_operating_cost) == pytest.approx(
        pyo.value(opex.electricity_cost)
        + pyo.value(opex.fuel_cost)
        + pyo.value(opex.fixed_operating_cost)
    )


@pytest.mark.unit
def test_fixed_operating_cost_flows_through():
    """fixed_operating_cost adds to the opex total, distinct from the tariff charge."""
    profile = {t: 100.0 for t in range(24)}

    m0 = _pump_tank_costing(fixed_operating_cost=0.0)
    _set_power(m0, profile)
    m1 = _pump_tank_costing(fixed_operating_cost=1234.0)
    _set_power(m1, profile)

    delta = pyo.value(m1.costing.aggregate_operating_cost) - pyo.value(
        m0.costing.aggregate_operating_cost
    )
    assert delta == pytest.approx(1234.0)
    # The fixed operating cost is NOT part of the (EECO) electricity charge.
    assert pyo.value(m1.costing.opex.electricity_cost) == pytest.approx(
        pyo.value(m0.costing.opex.electricity_cost)
    )


@pytest.mark.unit
def test_operating_cost_is_eeco_total():
    """aggregate_operating_cost maps EECO's total, not a re-derived one."""
    m = _pump_tank_costing(fixed_operating_cost=0.0)
    _set_power(m, {t: 100.0 for t in range(24)})
    # EECO built its own electric_* components on the opex block.
    assert any(
        v.local_name.startswith("electric_")
        for v in m.costing.opex.component_objects(pyo.Var)
    )
    # With no gas and no fixed cost, the aggregate is exactly EECO's electric total.
    assert pyo.value(m.costing.aggregate_operating_cost) == pytest.approx(
        pyo.value(m.costing.opex.electricity_cost)
    )


@pytest.mark.unit
def test_operating_costs_carry_tariff_currency():
    """base_currency and every cost expression carry the tariff's currency (USD)."""
    m = _pump_tank_costing(fixed_operating_cost=100.0)
    assert str(m.costing.base_currency) == "USD"  # from the tariff sheet's "$"
    for cost in (
        m.costing.opex.electricity_cost,
        m.costing.opex.fuel_cost,
        m.costing.opex.fixed_operating_cost,
        m.costing.opex.total_operating_cost,
        m.costing.aggregate_operating_cost,
        m.costing.capex.total_capital_cost,
        m.costing.aggregate_capital_cost,
        m.costing.total_cost,
    ):
        assert str(pyunits.get_units(cost)) == "USD"


@pytest.mark.unit
def test_capex_block_empty():
    """The capex block is an empty placeholder: total_capital_cost == 0."""
    m = _pump_tank_costing()
    assert pyo.value(m.costing.capex.total_capital_cost) == pytest.approx(0.0)
    assert pyo.value(m.costing.aggregate_capital_cost) == pytest.approx(0.0)


@pytest.mark.unit
def test_capex_excluded_from_operations_objective():
    """aggregate_operating_cost == opex total; total_cost = operating + capital."""
    m = _pump_tank_costing()
    _set_power(m, {t: 100.0 for t in range(24)})
    assert pyo.value(m.costing.aggregate_operating_cost) == pytest.approx(
        pyo.value(m.costing.opex.total_operating_cost)
    )
    assert pyo.value(m.costing.total_cost) == pytest.approx(
        pyo.value(m.costing.aggregate_operating_cost)
        + pyo.value(m.costing.aggregate_capital_cost)
    )


@pytest.mark.unit
def test_report_cost_breakdown_shape():
    """report_cost returns a categorized CostReport with v0 zero placeholders."""
    m = _pump_tank_costing(fixed_operating_cost=500.0, dr_event_file=str(_DR_JSON))
    profile = {t: 100.0 for t in range(24)}
    _set_power(m, profile)

    report = m.costing.report_cost(m)
    assert isinstance(report, CostReport)
    assert isinstance(report.operating, OperatingCostBreakdown)
    assert isinstance(report.capital, CapitalCostBreakdown)

    assert report.operating.fuel == pytest.approx(0.0)
    assert report.operating.fixed == pytest.approx(500.0)
    # DR is containers-only: a loaded DR file produces no credit.
    assert report.operating.dr_revenue == pytest.approx(0.0)
    assert report.capital.by_component == {}
    assert report.capital.total == pytest.approx(0.0)

    assert report.operating.total == pytest.approx(
        report.operating.electricity
        + report.operating.fuel
        + report.operating.fixed
        - report.operating.dr_revenue
    )
    assert report.total == pytest.approx(report.operating.total + report.capital.total)


@pytest.mark.unit
def test_mode_toggles():
    """Design/operations modes are idempotent no-ops over empty registries; LP both."""
    m = _pump_tank_costing()
    m.objective = pyo.Objective(expr=m.costing.aggregate_operating_cost)

    m.costing.set_design_mode()
    m.costing.set_design_mode()  # idempotent
    assert classify(m) is ProblemClass.LP  # empty capex -> no nonlinearity

    m.costing.set_operations_mode()
    m.costing.set_operations_mode()  # idempotent
    assert classify(m) is ProblemClass.LP


@pytest.mark.unit
def test_construction_order_permutation():
    """aggregate_operating_cost is identical across component-creation orders."""
    profile = {t: 100.0 for t in range(24)}

    values = []
    for costing_first in (True, False):
        for pump_first in (True, False):
            m = _pump_tank_costing(costing_first=costing_first, pump_first=pump_first)
            _set_power(m, profile)
            values.append(pyo.value(m.costing.aggregate_operating_cost))

    for v in values[1:]:
        assert v == pytest.approx(values[0], rel=1e-12)


@pytest.mark.unit
def test_dr_container_loads_noop():
    """A loaded DR file populates the container and builds no DR constraints."""
    from flexops.costing import DRConfig

    m_dr = _pump_tank_costing(dr_event_file=str(_DR_JSON))
    assert isinstance(m_dr.costing.dr, DRConfig)
    assert m_dr.costing.dr.program is not None

    m_no = _pump_tank_costing()
    # No DR constraints: the active-constraint count is unchanged, LP both.
    n_dr = len(list(m_dr.component_data_objects(pyo.Constraint, active=True)))
    n_no = len(list(m_no.component_data_objects(pyo.Constraint, active=True)))
    assert n_dr == n_no
    assert classify(m_dr) is ProblemClass.LP


@pytest.mark.unit
def test_model_classifies_lp():
    """The built pump+tank+costing model classifies LP."""
    m = _pump_tank_costing()
    m.objective = pyo.Objective(expr=m.costing.aggregate_operating_cost)
    assert classify(m) is ProblemClass.LP
