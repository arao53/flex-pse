"""Extensions adding a tank's terminal level and the model's objective."""

import pyomo.environ as pyo
from pyomo.environ import units as pyunits
from pyomo.network import Arc

import flexops as fo
from flexops.costing import currency_units
from flexops.tests.sample_extensions import minimize_power, terminal_level

EXPAND_ARCS = True


def build() -> pyo.ConcreteModel:
    """Build the model in code."""
    m = pyo.ConcreteModel(name="extensions")
    m.time_block = fo.TimeBlock(
        start_date="2025-01-01", end_date="2025-01-01T06:00", time_step=1 * pyunits.hr
    )
    m.properties = fo.SimpleAqueousFlow()
    m.costing = fo.FlexCosting(
        time_block=m.time_block,
        energy_prices={"electrical": 0.1 * currency_units("USD") / pyunits.kWh},
    )
    m.site = fo.PlantBlock(time_block=m.time_block)
    m.site.tank = fo.Tank(property_package=m.properties)
    m.site.pump = fo.Pump(property_package=m.properties, costing_package=m.costing)
    m.site.tank_to_pump = Arc(source=m.site.tank.outlet, destination=m.site.pump.inlet)
    m.costing.cost_process()
    terminal_level(m, tank="site.tank", fraction=0.5)
    minimize_power(m, units=["site.pump"])
    return m
