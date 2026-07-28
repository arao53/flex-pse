"""Unit-tier tests for FlexCosting: opex/capex structure, fuels, scalar costs.

These tests exercise the wrapper — indexed per-carrier power aggregation, the
``opex`` block (electricity + fuel + fixed + scalar operating cost), the empty
``capex`` placeholder, annualization, the capex-in-objective-only-in-design-mode
rule, ``report_cost``'s categorized breakdown, the DR container no-op, modes, and
the construction-order invariant. None of them invoke a solver (that is
``test_load_shifting_component.py``); derived Vars are propagated through their
defining equality constraints via :func:`_propagate`. The tariff *math* itself is
EECO.
"""

from pathlib import Path

import pyomo.environ as pyo
import pytest
from pyomo.core.base.units_container import InconsistentUnitsError
from pyomo.environ import units as pyunits
from pyomo.network import Arc
from pyomo.util.calc_var_value import calculate_variable_from_constraint

import flexops as fo
from flexcore import nomenclature as nm
from flexcore.exceptions import FlexConfigError
from flexcore.solvers import ProblemClass, classify
from flexops.core.registration import IORegistry, PowerRecord
from flexops.core.time_block import TimeBlock
from flexops.costing import (
    CapitalCostBreakdown,
    CostReport,
    FlexCosting,
    FuelSpec,
    OperatingCostBreakdown,
    load_tariff,
)
from flexops.properties.simple_aqueous import SimpleAqueousFlow
from flexops.unit_models import Pump, Tank

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_TARIFF_JSON = _FIXTURES / "tariff_tou_demo.json"
_DR_JSON = _FIXTURES / "dr_events_demo.json"


def _two_utility_tariff():
    """A flat (no-tier, no-demand) tariff with one electric and one gas charge."""
    records = [
        {
            "utility": "electric",
            "type": "energy",
            "name": "allday",
            "month_start": 1,
            "month_end": 12,
            "weekday_start": 0,
            "weekday_end": 6,
            "hour_start": 0,
            "hour_end": 24,
            "basic_charge_limit (metric)": 0,
            "charge (metric)": 0.10,
            "units": "$/kWh",
        },
        {
            "utility": "gas",
            "type": "energy",
            "name": "allday",
            "month_start": 1,
            "month_end": 12,
            "weekday_start": 0,
            "weekday_end": 6,
            "hour_start": 0,
            "hour_end": 24,
            "basic_charge_limit (metric)": 0,
            "charge (metric)": 0.50,
            "units": "$/m3",
        },
    ]
    return load_tariff(records)


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
    tariff=None,
    run_cost_process: bool = True,
) -> pyo.ConcreteModel:
    """Build a Pump -> Arc -> Tank system with a FlexCosting block.

    ``costing_first`` / ``pump_first`` permute the component-creation order (the
    construction-order invariant). When costing is created after the units they
    are built with ``costing_package=None`` (aggregation pulls from the model, so
    the association is not required). ``tariff`` overrides the default electric
    demo tariff with a pre-loaded rate_data object.
    """
    m = _time_model()

    def add_costing() -> None:
        kwargs = dict(
            time_block=m.time_block,
            fixed_operating_cost=fixed_operating_cost,
            dr_event_file=dr_event_file,
        )
        if tariff is not None:
            kwargs["tariff"] = tariff
        else:
            kwargs["tariff_file"] = str(_TARIFF_JSON)
        m.costing = FlexCosting(**kwargs)

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


def _propagate(costing, passes: int = 8) -> None:
    """Solve every ``var == expr`` defining constraint for its Var, no solver.

    Walks the costing block (and its sub-blocks) for each Var that has a sibling
    constraint named ``eq_<var_local_name>`` and computes the Var from it via
    :func:`calculate_variable_from_constraint`. Repeated ``passes`` propagate
    values along the dependency chain regardless of component order. EECO's own
    internal cost Vars carry no ``eq_`` sibling and are left at their init values
    (matching the pre-conversion Expression behavior).
    """
    pairs = []
    for var in costing.component_objects(pyo.Var, descend_into=True, sort=False):
        con = var.parent_block().component(f"eq_{var.local_name}")
        if con is None:
            continue
        for idx in var:
            pairs.append((var[idx], con[idx]))
    for _ in range(passes):
        for v, c in pairs:
            calculate_variable_from_constraint(v, c)


def _add_fuel_draw(m, fuel_name: str, values: dict[int, float], *, units=pyunits.kW):
    """Attach a bare block carrying a registered fuel-draw Var, fixed."""
    blk = pyo.Block()
    setattr(m, f"fuel_{fuel_name}", blk)
    var = pyo.Var(m.time_block.time_index, initialize=0.0, units=units)
    blk.add_component(f"{nm.POWER_FUEL}_{fuel_name}", var)
    blk._io_registry = IORegistry()
    blk._io_registry.power.append(
        PowerRecord(
            var=var,
            name=f"{nm.POWER_FUEL}_{fuel_name}",
            kind=nm.PowerKind.FUEL,
            fuel_name=fuel_name,
        )
    )
    for t, val in values.items():
        var[t].set_value(val)
    return var


def _add_thermal_draw(m, tag: str, temperature, values: dict[int, float]):
    """Attach a bare block carrying a registered thermal-duty Var (kW) at T, fixed."""
    blk = pyo.Block()
    setattr(m, f"thermal_{tag}", blk)
    var = pyo.Var(m.time_block.time_index, initialize=0.0, units=pyunits.kW)
    blk.add_component(nm.POWER_THERMAL, var)
    blk._io_registry = IORegistry()
    blk._io_registry.power.append(
        PowerRecord(
            var=var,
            name=nm.POWER_THERMAL,
            kind=nm.PowerKind.THERMAL,
            temperature=temperature,
        )
    )
    for t, val in values.items():
        var[t].set_value(val)
    return var


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
    assert m.costing.find_component("aggregate_power") is not None
    assert m.costing.find_component("opex") is not None
    assert m.costing.find_component("capex") is not None
    _propagate(m.costing)
    # Empty registry -> the 0*kW placeholder body -> zero everywhere.
    for t in m.time_block.time_index:
        assert pyo.value(m.costing.aggregate_electrical_power[t]) == pytest.approx(0.0)


@pytest.mark.unit
def test_aggregate_electrical_power():
    """aggregate_electrical_power sums the registered units' power_electrical."""
    m = _pump_tank_costing()
    profile = {t: float(t) for t in m.time_block.time_index}
    _set_power(m, profile)
    _propagate(m.costing)
    for t in (0, 5, 16, 23):
        expected = pyo.value(m.pump.power_electrical[t])
        assert pyo.value(m.costing.aggregate_electrical_power[t]) == pytest.approx(
            expected
        )
        assert pyo.value(m.costing.aggregate_power[t, "electrical"]) == pytest.approx(
            expected
        )


@pytest.mark.unit
def test_opex_block_line_items():
    """The opex block exposes electricity/fuel/fixed/scalar and their sum."""
    m = _pump_tank_costing(fixed_operating_cost=250.0)
    _set_power(m, {t: 100.0 for t in m.time_block.time_index})
    _propagate(m.costing)
    opex = m.costing.opex

    assert pyo.value(opex.fuel_cost) == pytest.approx(0.0)  # no fuel unit
    assert pyo.value(opex.scalar_cost) == pytest.approx(0.0)  # no scalar cost
    assert pyo.value(opex.fixed_operating_cost) == pytest.approx(250.0)
    assert pyo.value(opex.total_operating_cost) == pytest.approx(
        pyo.value(opex.electricity_cost)
        + pyo.value(opex.fuel_cost)
        + pyo.value(opex.fixed_operating_cost)
        + pyo.value(opex.scalar_cost)
    )


@pytest.mark.unit
def test_fixed_operating_cost_flows_through():
    """fixed_operating_cost adds to the opex total, distinct from the tariff charge."""
    profile = {t: 100.0 for t in range(24)}

    m0 = _pump_tank_costing(fixed_operating_cost=0.0)
    _set_power(m0, profile)
    _propagate(m0.costing)
    m1 = _pump_tank_costing(fixed_operating_cost=1234.0)
    _set_power(m1, profile)
    _propagate(m1.costing)

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
    _propagate(m.costing)
    # EECO built its own electric_* components on the opex block.
    assert any(
        v.local_name.startswith("electric_")
        for v in m.costing.opex.component_objects(pyo.Var)
    )
    # With no fuel/scalar and no fixed cost, the aggregate is EECO's electric total.
    assert pyo.value(m.costing.aggregate_operating_cost) == pytest.approx(
        pyo.value(m.costing.opex.electricity_cost)
    )


@pytest.mark.unit
def test_operating_costs_carry_tariff_currency():
    """Cost Vars carry the tariff currency (USD); power Vars carry kW."""
    m = _pump_tank_costing(fixed_operating_cost=100.0)
    assert str(m.costing.base_currency) == "USD"  # from the tariff sheet's "$"
    for cost in (
        m.costing.opex.electricity_cost,
        m.costing.opex.fuel_cost,
        m.costing.opex.fixed_operating_cost,
        m.costing.opex.scalar_cost,
        m.costing.opex.total_operating_cost,
        m.costing.aggregate_operating_cost,
        m.costing.capex.total_capital_cost,
        m.costing.aggregate_capital_cost,
        m.costing.total_cost,
    ):
        assert str(pyunits.get_units(cost)) == "USD"
    # Power aggregates are kW; annualized cost is USD per year.
    assert str(pyunits.get_units(m.costing.aggregate_power)) == "kW"
    assert "USD" in str(pyunits.get_units(m.costing.annualized_cost))


@pytest.mark.unit
def test_capex_block_empty():
    """The capex block is an empty placeholder: total_capital_cost == 0."""
    m = _pump_tank_costing()
    _propagate(m.costing)
    assert pyo.value(m.costing.capex.total_capital_cost) == pytest.approx(0.0)
    assert pyo.value(m.costing.aggregate_capital_cost) == pytest.approx(0.0)


@pytest.mark.unit
def test_capex_excluded_from_operations_objective():
    """aggregate_operating_cost == opex total; total_cost = operating + capital."""
    m = _pump_tank_costing()
    _set_power(m, {t: 100.0 for t in range(24)})
    _propagate(m.costing)
    assert pyo.value(m.costing.aggregate_operating_cost) == pytest.approx(
        pyo.value(m.costing.opex.total_operating_cost)
    )
    assert pyo.value(m.costing.total_cost) == pytest.approx(
        pyo.value(m.costing.aggregate_operating_cost)
        + pyo.value(m.costing.aggregate_capital_cost)
    )


@pytest.mark.unit
def test_annualized_cost():
    """annualized_cost scales opex to a year; CRF matches the config formula."""
    m = _pump_tank_costing(fixed_operating_cost=8760.0)
    _set_power(m, {t: 100.0 for t in range(24)})
    _propagate(m.costing)

    horizon_years = pyo.value(pyunits.convert(m.time_block.horizon, pyunits.year))
    op = pyo.value(m.costing.aggregate_operating_cost)
    # capex is 0 in v0, so annualized_cost == operating / horizon_years.
    assert pyo.value(m.costing.annualized_cost) == pytest.approx(op / horizon_years)

    i, n = 0.08, 20.0
    expected_crf = i * (1 + i) ** n / ((1 + i) ** n - 1)
    assert pyo.value(m.costing.capital_recovery_factor) == pytest.approx(expected_crf)


@pytest.mark.unit
def test_power_units_normalized():
    """A power var in MW normalizes to kW; a non-power var raises loudly."""
    m = _pump_tank_costing(tariff=_two_utility_tariff(), run_cost_process=False)
    # An MW-denominated fuel draw aggregates in kW (x1000).
    _add_fuel_draw(m, "biogas", {t: 2.0 for t in range(24)}, units=pyunits.MW)
    m.costing.register_fuel("biogas", heating_value=10.0)
    m.costing.cost_process()
    _propagate(m.costing)
    assert pyo.value(m.costing.aggregate_power[0, "biogas"]) == pytest.approx(2000.0)

    # A non-power (volumetric) var registered as electrical must raise at aggregation.
    m2 = _pump_tank_costing(run_cost_process=False)
    blk = pyo.Block()
    m2.bad = blk
    bad = pyo.Var(m2.time_block.time_index, units=pyunits.m**3 / pyunits.hr)
    blk.add_component(nm.POWER_ELECTRICAL, bad)
    blk._io_registry = IORegistry()
    blk._io_registry.power.append(
        PowerRecord(var=bad, name=nm.POWER_ELECTRICAL, kind=nm.PowerKind.ELECTRICAL)
    )
    with pytest.raises(InconsistentUnitsError):
        m2.costing.cost_process()


@pytest.mark.unit
def test_register_fuel():
    """A registered fuel is aggregated and billed via EECO's gas leg."""
    m = _pump_tank_costing(tariff=_two_utility_tariff(), run_cost_process=False)
    fuel = _add_fuel_draw(m, "natural_gas", {t: 50.0 for t in range(24)})
    m.costing.register_fuel("natural_gas", heating_value=10.0)
    m.costing.cost_process()
    _propagate(m.costing)

    for t in (0, 12, 23):
        assert pyo.value(m.costing.aggregate_power[t, "natural_gas"]) == pytest.approx(
            pyo.value(fuel[t])
        )
    # The fuel leg wired through add_fuel_cost: normalized usage var + EECO gas_* comps.
    assert m.costing.opex.find_component("eeco_gas_usage_natural_gas") is not None
    assert m.costing.opex.find_component("fuel_cost_natural_gas") is not None
    assert any(
        v.local_name.startswith("gas_")
        for v in m.costing.opex.component_objects(pyo.Var)
    )
    assert m.costing.fuel_spec("natural_gas").heating_value == 10.0


@pytest.mark.unit
def test_register_fuel_returns_fuelspec():
    """register_fuel records a FuelSpec queryable by name (default m^3 basis)."""
    m = _pump_tank_costing(tariff=_two_utility_tariff(), run_cost_process=False)
    m.costing.register_fuel("hydrogen", heating_value=3.0)
    spec = m.costing.fuel_spec("hydrogen")
    assert isinstance(spec, FuelSpec)
    assert spec.name == "hydrogen"
    assert spec.heating_value == 3.0
    assert str(spec.fuel_units) == "m**3"


@pytest.mark.unit
def test_register_fuel_therm_basis():
    """A fuel can be metered in therms (energy basis); usage is therm/hr."""
    tariff = load_tariff(
        [
            {
                "utility": "electric",
                "type": "energy",
                "name": "allday",
                "month_start": 1,
                "month_end": 12,
                "weekday_start": 0,
                "weekday_end": 6,
                "hour_start": 0,
                "hour_end": 24,
                "basic_charge_limit (metric)": 0,
                "charge (metric)": 0.10,
                "units": "$/kWh",
            },
            {
                "utility": "gas",
                "type": "energy",
                "name": "allday",
                "month_start": 1,
                "month_end": 12,
                "weekday_start": 0,
                "weekday_end": 6,
                "hour_start": 0,
                "hour_end": 24,
                "basic_charge_limit (metric)": 0,
                "charge (metric)": 1.20,
                "units": "$/therm",
            },
        ]
    )
    m = _pump_tank_costing(tariff=tariff, run_cost_process=False)
    _add_fuel_draw(m, "natural_gas", {t: 293.07 for t in range(24)})  # kW
    # 1 therm = 29.307 kWh, so heating_value = 29.307 kWh/therm.
    m.costing.register_fuel(
        "natural_gas", heating_value=29.307, fuel_units=pyunits.therm
    )
    m.costing.cost_process()
    _propagate(m.costing)

    usage = m.costing.opex.eeco_gas_usage_natural_gas
    # 293.07 kW / 29.307 kWh/therm = 10 therm/hr.
    assert pyo.value(usage[0]) == pytest.approx(10.0, rel=1e-3)


@pytest.mark.unit
def test_thermal_aggregated_by_temperature():
    """Thermal duties at different temperatures are separate carriers, never mixed."""
    m = _pump_tank_costing(run_cost_process=False)
    _add_thermal_draw(m, "lo", 350 * pyunits.K, {t: 10.0 for t in range(24)})
    _add_thermal_draw(m, "hi", 400 * pyunits.K, {t: 20.0 for t in range(24)})
    _add_thermal_draw(m, "lo2", 350 * pyunits.K, {t: 5.0 for t in range(24)})
    m.costing.cost_process()
    _propagate(m.costing)

    lo = "thermal@350K"
    hi = "thermal@400K"
    assert pyo.value(m.costing.aggregate_power[0, lo]) == pytest.approx(15.0)  # 10 + 5
    assert pyo.value(m.costing.aggregate_power[0, hi]) == pytest.approx(20.0)
    # The temperature-blind total sums all thermal buckets.
    assert pyo.value(m.costing.aggregate_thermal_power[0]) == pytest.approx(35.0)


@pytest.mark.unit
def test_register_scalar_cost():
    """A non-energy scalar cost (price x quantity) enters the opex total, no EECO."""
    m = _pump_tank_costing(run_cost_process=False)
    # A water-withdrawal flow, m^3/hr, priced at $2.5/m^3.
    m.water = pyo.Var(m.time_block.time_index, units=pyunits.m**3 / pyunits.hr)
    for t in m.time_block.time_index:
        m.water[t].set_value(3.0)
    m.costing.register_scalar_cost(
        "water", m.water, price=2.5, quantity_units=pyunits.m**3 / pyunits.hr
    )
    m.costing.cost_process()
    _propagate(m.costing)

    dt_hours = pyo.value(pyunits.convert(m.time_block.dt, pyunits.hr))
    expected = 2.5 * 3.0 * 24 * dt_hours
    assert pyo.value(m.costing.opex.scalar_cost) == pytest.approx(expected)
    assert pyo.value(m.costing.opex.total_operating_cost) == pytest.approx(
        pyo.value(m.costing.opex.electricity_cost)
        + pyo.value(m.costing.opex.fuel_cost)
        + pyo.value(m.costing.opex.fixed_operating_cost)
        + expected
    )


@pytest.mark.unit
def test_scalar_cost_not_via_eeco():
    """Scalar costs never build EECO components."""
    m = _pump_tank_costing(run_cost_process=False)
    m.chem = pyo.Var(m.time_block.time_index, units=pyunits.kg / pyunits.hr)
    for t in m.time_block.time_index:
        m.chem[t].set_value(1.0)
    m.costing.register_scalar_cost(
        "chem", m.chem, price=4.0, quantity_units=pyunits.kg / pyunits.hr
    )
    m.costing.cost_process()
    # No gas_* EECO components appear (scalar costs are not routed through EECO).
    assert not any(
        v.local_name.startswith("gas_")
        for v in m.costing.opex.component_objects(pyo.Var)
    )


@pytest.mark.unit
def test_register_scalar_cost_unit_attribution():
    """An optional unit= is stored on the spec for later per-unit attribution."""
    m = _pump_tank_costing(run_cost_process=False)
    m.water = pyo.Var(m.time_block.time_index, units=pyunits.m**3 / pyunits.hr)

    # Default: no unit association.
    plain = m.costing.register_scalar_cost(
        "water", m.water, price=2.5, quantity_units=pyunits.m**3 / pyunits.hr
    )
    assert plain.unit is None

    # An attributed cost records the owning unit block verbatim.
    attributed = m.costing.register_scalar_cost(
        "water_pump",
        m.water,
        price=2.5,
        quantity_units=pyunits.m**3 / pyunits.hr,
        unit=m.pump,
    )
    assert attributed.unit is m.pump


@pytest.mark.unit
def test_report_cost_breakdown_shape():
    """report_cost returns a categorized CostReport with v0 zero placeholders."""
    m = _pump_tank_costing(fixed_operating_cost=500.0, dr_event_file=str(_DR_JSON))
    profile = {t: 100.0 for t in range(24)}
    _set_power(m, profile)
    _propagate(m.costing)

    report = m.costing.report_cost(m)
    assert isinstance(report, CostReport)
    assert isinstance(report.operating, OperatingCostBreakdown)
    assert isinstance(report.capital, CapitalCostBreakdown)

    assert report.operating.fuel == pytest.approx(0.0)
    assert report.operating.fixed == pytest.approx(500.0)
    assert report.operating.scalar == pytest.approx(0.0)
    # DR is containers-only: a loaded DR file produces no credit.
    assert report.operating.dr_revenue == pytest.approx(0.0)
    assert report.capital.by_component == {}
    assert report.capital.total == pytest.approx(0.0)

    assert report.operating.total == pytest.approx(
        report.operating.electricity
        + report.operating.fuel
        + report.operating.fixed
        + report.operating.scalar
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
            _propagate(m.costing)
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
