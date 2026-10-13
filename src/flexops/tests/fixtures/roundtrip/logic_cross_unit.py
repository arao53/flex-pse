"""Three units in a parallel group, plus a conditional that turns one off."""

import pyomo.environ as pyo
from pyomo.environ import units as pyunits

import flexops as fo
from flexops import logic
from flexops.costing import currency_units

EXPAND_ARCS = False


def build() -> pyo.ConcreteModel:
    """Build the model in code."""
    m = pyo.ConcreteModel(name="cross_unit")
    m.time_block = fo.TimeBlock(
        start_date="2025-01-01", end_date="2025-01-01T06:00", time_step=1 * pyunits.hr
    )
    m.properties = fo.SimpleAqueousFlow()
    m.costing = fo.FlexCosting(
        time_block=m.time_block,
        energy_prices={"electrical": 0.1 * currency_units("USD") / pyunits.kWh},
    )
    m.site = fo.PlantBlock(time_block=m.time_block)
    units = []
    for name in ("a", "b", "c"):
        unit = fo.ConstantEnergyIntensityModel(
            property_package=m.properties,
            energy_intensity=0.4 * pyunits.kWh / pyunits.m**3,
            costing_package=m.costing,
        )
        m.site.add_component(name, unit)
        unit = m.site.find_component(name)
        logic.add_status(unit, unit.power_electrical, 0 * pyunits.kW, 50 * pyunits.kW)
        units.append(unit)
    logic.register_parallel_group(units)
    logic.add_conditional(units[0], units[2], then="off")
    m.costing.cost_process()
    m.objective = pyo.Objective(expr=m.costing.aggregate_operating_cost)
    return m
