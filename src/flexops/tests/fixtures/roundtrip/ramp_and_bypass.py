"""A two-step ramp limit with units, and a bypass around a pump."""

import pyomo.environ as pyo
from pyomo.environ import units as pyunits

import flexops as fo
from flexops import logic
from flexops.costing import currency_units

EXPAND_ARCS = False


def build() -> pyo.ConcreteModel:
    """Build the model in code."""
    m = pyo.ConcreteModel(name="ramp_bypass")
    m.time_block = fo.TimeBlock(
        start_date="2025-01-01", end_date="2025-01-01T06:00", time_step=1 * pyunits.hr
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
    logic.add_ramp_rate(
        pump.power_electrical,
        ramp_up=5.0,
        ramp_down=3.0,
        ramp_rate_units=pyunits.kW / pyunits.hr,
        window=2,
    )
    logic.add_bypass(pump, pump.flow_in, 2 * pyunits.m**3 / pyunits.hr)
    m.costing.cost_process()
    m.objective = pyo.Objective(expr=m.costing.aggregate_operating_cost)
    return m
