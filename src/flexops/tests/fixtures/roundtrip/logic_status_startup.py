"""A pump with a status, startup/shutdown with minimum up/down times, and a dwell."""

import pyomo.environ as pyo
from pyomo.environ import units as pyunits

import flexops as fo
from flexops import logic
from flexops.costing import currency_units

EXPAND_ARCS = False


def build() -> pyo.ConcreteModel:
    """Build the model in code."""
    m = pyo.ConcreteModel(name="status_startup")
    m.time_block = fo.TimeBlock(
        start_date="2025-01-01", end_date="2025-01-01T08:00", time_step=1 * pyunits.hr
    )
    m.properties = fo.SimpleAqueousFlow()
    m.costing = fo.FlexCosting(
        time_block=m.time_block,
        energy_prices={"electrical": 0.1 * currency_units("USD") / pyunits.kWh},
    )
    m.site = fo.PlantBlock(time_block=m.time_block)
    pump = m.site.pump = fo.Pump(
        property_package=m.properties, costing_package=m.costing
    )
    status = logic.add_status(
        pump, pump.power_electrical, 1 * pyunits.kW, 20 * pyunits.kW
    )
    logic.add_startup_shutdown(pump, status, min_uptime=3, min_downtime=2)
    logic.add_dwell(pump.flow_in, 2)
    m.costing.cost_process()
    m.objective = pyo.Objective(expr=m.costing.aggregate_operating_cost)
    return m
