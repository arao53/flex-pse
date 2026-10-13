"""Indexed RO trains, each member with the same status and startup/shutdown."""

import pyomo.environ as pyo
from pyomo.environ import units as pyunits

import flexops as fo
from flexops import logic
from flexops.costing import currency_units

EXPAND_ARCS = False


def build() -> pyo.ConcreteModel:
    """Build the model in code."""
    m = pyo.ConcreteModel(name="indexed_logic")
    m.time_block = fo.TimeBlock(
        start_date="2025-01-01", end_date="2025-01-01T06:00", time_step=1 * pyunits.hr
    )
    m.properties = fo.SimpleAqueousFlow()
    m.costing = fo.FlexCosting(
        time_block=m.time_block,
        energy_prices={"electrical": 0.1 * currency_units("USD") / pyunits.kWh},
    )
    m.p = fo.PlantBlock(time_block=m.time_block)
    m.p.trains = pyo.Set(initialize=[0, 1, 2], ordered=True)
    m.p.ro = fo.ConstantEnergyIntensityModel(
        m.p.trains,
        property_package=m.properties,
        energy_intensity=0.3 * pyunits.kWh / pyunits.m**3,
        costing_package=m.costing,
    )
    for train in m.p.ro.values():
        status = logic.add_status(
            train, train.power_electrical, 0 * pyunits.kW, 40 * pyunits.kW
        )
        logic.add_startup_shutdown(train, status, min_uptime=2)
    m.costing.cost_process()
    m.objective = pyo.Objective(expr=m.costing.aggregate_operating_cost)
    return m
