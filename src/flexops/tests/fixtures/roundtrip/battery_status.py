"""A battery that builds its own status; the spec carries no status element."""

import pyomo.environ as pyo
from pyomo.environ import units as pyunits

import flexops as fo
from flexcore.config.schema import UnitCommitmentConfig
from flexops import logic
from flexops.costing import currency_units

EXPAND_ARCS = False


def build() -> pyo.ConcreteModel:
    """Build the model in code."""
    m = pyo.ConcreteModel(name="battery_status")
    m.time_block = fo.TimeBlock(
        start_date="2025-01-01", end_date="2025-01-01T06:00", time_step=1 * pyunits.hr
    )
    m.properties = fo.SimpleAqueousFlow()
    m.costing = fo.FlexCosting(
        time_block=m.time_block,
        energy_prices={"electrical": 0.1 * currency_units("USD") / pyunits.kWh},
    )
    m.site = fo.PlantBlock(time_block=m.time_block)
    m.site.battery = fo.BatteryModel(
        capacity=10 * pyunits.kWh,
        power_charge_max=5 * pyunits.kW,
        power_discharge_max=5 * pyunits.kW,
        unit_commitment=UnitCommitmentConfig(status=True),
        costing_package=m.costing,
    )
    logic.add_startup_shutdown(m.site.battery, m.site.battery.status)
    m.costing.cost_process()
    m.objective = pyo.Objective(expr=m.costing.aggregate_operating_cost)
    return m
