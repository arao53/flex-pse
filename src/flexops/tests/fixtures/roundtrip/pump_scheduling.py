"""Pump scheduling gate: a trimmed copy of the flex-pse-examples pump_scheduling model.

A feed pump fills a tank that a product pump draws on to meet a fixed demand,
with a battery alongside. Prices and demand are synthetic and the horizon is
one day at 1 h.
"""

import pyomo.environ as pyo
from pyomo.environ import units as pyunits
from pyomo.network import Arc

import flexops as fo
from flexops import logic
from flexops.costing import currency_units

EXPAND_ARCS = True

BAR = 1e5
"""Pascals per bar."""

DEMAND = [40.0 + 20.0 * ((t % 24) in range(7, 20)) for t in range(24)]
"""Product demand per hour, m^3/hr."""

PRICES = [0.08 + 0.22 * ((t % 24) in range(16, 21)) for t in range(24)]
"""Electricity price per hour, USD/kWh."""


@fo.extension
def feed_pump_status(model, *, rated_flow: float, min_fraction: float):
    """Let the feed pump be off, or run at min_fraction to 1 of rated_flow (m^3/hr)."""
    pump = model.feed_pump
    pump.flow_in = pyo.Reference(pump.inlet_state.flow_vol_phase[:, "Liq"])
    flow = pyunits.m**3 / pyunits.hr
    logic.add_status(
        pump, pump.flow_in, min_fraction * rated_flow * flow, rated_flow * flow
    )


@fo.extension
def terminal_tank_volume(model, *, volume: float):
    """End the horizon with at least ``volume`` m^3 in the tank."""
    last = model.time_block.time_index.last()
    model.tank_terminal = pyo.Constraint(expr=model.tank.volume[last] >= volume)


@fo.extension
def terminal_battery_charge(model):
    """End the horizon at least as charged as the battery started."""
    last = model.time_block.time_index.last()
    model.battery_terminal = pyo.Constraint(
        expr=model.battery.charge[last] >= model.battery.charge_init
    )


def build() -> pyo.ConcreteModel:
    """Build the model in code."""
    m = pyo.ConcreteModel(name="pump_scheduling")
    m.time_block = fo.TimeBlock(
        start_date="2026-07-01", end_date="2026-07-02", time_step=1 * pyunits.hr
    )
    m.properties = fo.SimpleAqueousFlow(
        has_pressure=True, density=1000 * pyunits.kg / pyunits.m**3
    )
    usd_per_kwh = currency_units("USD") / pyunits.kWh
    m.costing = fo.FlexCosting(
        time_block=m.time_block,
        energy_prices={"electrical": [p * usd_per_kwh for p in PRICES]},
    )
    m.feed_pump = fo.Pump(
        property_package=m.properties,
        power_relation="hydraulic",
        efficiency=0.75,
        costing_package=m.costing,
    )
    m.tank = fo.Tank(
        property_package=m.properties,
        max_volume=600 * pyunits.m**3,
        initial_volume=300 * pyunits.m**3,
        level_min=0.1,
        level_max=0.95,
    )
    m.product_pump = fo.Pump(
        property_package=m.properties,
        power_relation="hydraulic",
        efficiency=0.7,
        costing_package=m.costing,
    )
    m.feed_to_tank = Arc(source=m.feed_pump.outlet, destination=m.tank.inlet)
    m.tank_to_product = Arc(source=m.tank.outlet, destination=m.product_pump.inlet)
    pyo.TransformationFactory("network.expand_arcs").apply_to(m)

    for t in m.time_block.time_index:
        m.feed_pump.delta_pressure[t].fix(2.0 * BAR)
        m.product_pump.delta_pressure[t].fix(3.0 * BAR)
        m.feed_pump.inlet_state.pressure[t].fix(1.0 * BAR)
        m.product_pump.outlet_state.flow_vol_phase[t, "Liq"].fix(DEMAND[t])

    feed_pump_status(m, rated_flow=120.0, min_fraction=0.4)
    # t=0 is the initial-state snapshot; water pumped there never reaches the tank.
    m.feed_pump.flow_in[0].fix(0.0)

    rate = 30 * pyunits.kW
    m.battery = fo.BatteryModel(
        capacity=rate * 4 * pyunits.hr,
        power_charge_max=rate,
        power_discharge_max=rate,
        eta_charge=0.95,
        eta_discharge=0.95,
        soc_min=0.1,
        soc_max=0.9,
        initial_soc=0.5,
        costing_package=m.costing,
    )
    terminal_battery_charge(m)
    terminal_tank_volume(m, volume=300.0)

    m.costing.cost_process()
    m.objective = pyo.Objective(expr=m.costing.aggregate_operating_cost)
    return m
