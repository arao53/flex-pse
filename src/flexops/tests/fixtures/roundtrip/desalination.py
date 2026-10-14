"""Desalination gate: a trimmed copy of the flex-pse-examples desalination model.

Three parallel trains of pretreatment and RO between a fixed-duty intake and a
shared post-treatment step, with tees, a permeate header and an ocean outfall.
Prices are synthetic and the horizon is two days at 1 h.
"""

import pyomo.environ as pyo
from pyomo.environ import units as pyunits
from pyomo.network import Arc

import flexops as fo
from flexops.costing import currency_units
from flexops.logic import (
    add_conditional,
    add_startup_shutdown,
    add_status,
    register_parallel_group,
)

EXPAND_ARCS = True
TIER = "component"

RATED_FEED = 337.67
"""Feed per RO skid, m^3/hr; a skid is off or at this flow."""

INTAKE_FLOW = 1063.5
"""Fixed seawater intake, m^3/hr."""

RECUP_STEPS = 3
"""Steps post-treatment stays out after the RO system restarts."""

PRICES = [0.1 + 0.2 * ((t % 24) in range(16, 21)) for t in range(48)]
"""Electricity price per hour, USD/kWh."""


@fo.extension
def post_treatment_recuperation(model, *, steps: int):
    """Run post-treatment only once ro[0] has been on for ``steps`` steps."""
    plant = model.plant

    @plant.Constraint(model.time_block.time_index)
    def post_treatment_recuperation(b, t):
        started = sum(
            plant.ro[0].startup[s] for s in range(max(t - steps + 1, 1), t + 1)
        )
        return plant.post_treatment.status[t] <= plant.ro[0].status[t] - started


@fo.extension
def set_demand(model, *, volume: float):
    """Set the horizon's product-water obligation, in m^3."""
    model.plant.potable.delivery_min.set_value(volume)


def build() -> pyo.ConcreteModel:
    """Build the model in code."""
    m3_hr = pyunits.m**3 / pyunits.hr
    kwh_m3 = pyunits.kWh / pyunits.m**3
    m = pyo.ConcreteModel(name="desalination")
    m.time_block = fo.TimeBlock(
        start_date="2026-07-01", end_date="2026-07-03", time_step=1 * pyunits.hr
    )
    tb = m.time_block
    m.properties = fo.SimpleAqueousFlow()
    usd_per_kwh = currency_units("USD") / pyunits.kWh
    m.costing = fo.FlexCosting(
        time_block=tb, energy_prices={"electrical": [p * usd_per_kwh for p in PRICES]}
    )
    m.plant = fo.PlantBlock(time_block=tb)
    plant = m.plant
    plant.trains = pyo.Set(initialize=range(3), ordered=True)
    costed = {"property_package": m.properties, "costing_package": m.costing}

    plant.intake_pump = fo.Pump(
        power_relation="constant_intensity", energy_intensity=0.157 * kwh_m3, **costed
    )
    plant.pretreatment = fo.ConstantEnergyIntensityModel(
        plant.trains, energy_intensity=0.01 * kwh_m3, **costed
    )
    plant.ro = fo.ReverseOsmosis(
        plant.trains,
        recovery=0.465,
        recovery_min=0.4,
        recovery_max=0.5,
        energy_intensity=3.34 * kwh_m3,
        **costed,
    )
    for i in plant.trains:
        plant.ro[i].recovery.unfix()
    plant.post_treatment = fo.ConstantEnergyIntensityModel(
        energy_intensity=0.11 * kwh_m3, **costed
    )
    plant.product_pump = fo.Pump(
        power_relation="constant_intensity", energy_intensity=0.3 * kwh_m3, **costed
    )
    plant.feed_header = fo.Splitter(
        property_package=m.properties,
        outlet_names=tuple(f"t{i}" for i in plant.trains),
    )
    plant.train_split = fo.Splitter(
        plant.trains, property_package=m.properties, outlet_names=("ro", "bypass")
    )
    plant.permeate_split = fo.Splitter(
        plant.trains, property_package=m.properties, outlet_names=("header", "offspec")
    )
    plant.permeate_header = fo.Mixer(
        property_package=m.properties,
        inlet_names=tuple(f"t{i}" for i in plant.trains),
    )
    plant.outfall = fo.Product(
        property_package=m.properties,
        inlet_names=tuple(
            f"{stream}_{i}"
            for i in plant.trains
            for stream in ("brine", "bypass", "offspec")
        ),
        resource_name="outfall",
    )
    plant.potable = fo.Product(
        property_package=m.properties,
        resource_name="potable_water",
        min_demand=1.0 * pyunits.m**3,
        demand_basis="horizon",
    )
    plant.seawater = fo.Feed(property_package=m.properties, resource_name="seawater")

    plant.seawater_to_intake = Arc(
        source=plant.seawater.outlet_a,
        destination=plant.intake_pump.inlet,
        doc="Raw seawater to the intake pump.",
    )
    plant.intake_to_header = Arc(
        source=plant.intake_pump.outlet, destination=plant.feed_header.inlet
    )

    @plant.Arc(plant.trains, doc="Feed header to each train's pretreatment.")
    def header_to_pretreatment(b, i):
        return (
            plant.feed_header.find_component(f"outlet_t{i}"),
            plant.pretreatment[i].inlet,
        )

    @plant.Arc(plant.trains)
    def pretreatment_to_split(b, i):
        return (plant.pretreatment[i].outlet, plant.train_split[i].inlet)

    @plant.Arc(plant.trains)
    def split_to_ro(b, i):
        return (plant.train_split[i].outlet_ro, plant.ro[i].inlet)

    @plant.Arc(plant.trains)
    def ro_to_permeate_split(b, i):
        return (plant.ro[i].outlet_a, plant.permeate_split[i].inlet)

    @plant.Arc(plant.trains)
    def permeate_split_to_header(b, i):
        return (
            plant.permeate_split[i].outlet_header,
            plant.permeate_header.find_component(f"inlet_t{i}"),
        )

    plant.header_to_post = Arc(
        source=plant.permeate_header.outlet, destination=plant.post_treatment.inlet
    )
    plant.post_to_product = Arc(
        source=plant.post_treatment.outlet, destination=plant.product_pump.inlet
    )
    plant.product_to_delivery = Arc(
        source=plant.product_pump.outlet, destination=plant.potable.inlet_a
    )

    @plant.Arc(plant.trains)
    def brine_to_outfall(b, i):
        return (plant.ro[i].outlet_b, plant.outfall.find_component(f"inlet_brine_{i}"))

    @plant.Arc(plant.trains)
    def bypass_to_outfall(b, i):
        return (
            plant.train_split[i].outlet_bypass,
            plant.outfall.find_component(f"inlet_bypass_{i}"),
        )

    @plant.Arc(plant.trains)
    def offspec_to_outfall(b, i):
        return (
            plant.permeate_split[i].outlet_offspec,
            plant.outfall.find_component(f"inlet_offspec_{i}"),
        )

    pyo.TransformationFactory("network.expand_arcs").apply_to(m)

    always_on = [plant.intake_pump, *plant.pretreatment.values()]
    for unit in always_on:
        add_status(unit, unit.flow_in, 0 * m3_hr, INTAKE_FLOW * m3_hr)
        for t in tb.time_index:
            unit.status[t].fix(1)
    for t in tb.time_index:
        plant.seawater.withdrawal[t].fix(INTAKE_FLOW)
    for i in plant.trains:
        add_status(
            plant.ro[i], plant.ro[i].feed, RATED_FEED * m3_hr, RATED_FEED * m3_hr
        )
        add_startup_shutdown(
            plant.ro[i], plant.ro[i].status, min_uptime=RECUP_STEPS, min_downtime=1
        )
    add_status(
        plant.post_treatment,
        plant.post_treatment.flow_in,
        0 * m3_hr,
        3 * RATED_FEED * 0.5 * m3_hr,
    )
    post_treatment_recuperation(m, steps=RECUP_STEPS)
    register_parallel_group([plant.ro[i] for i in plant.trains])
    add_conditional(plant.ro[0], plant.ro[1], then="on")

    set_demand(m, volume=20000.0)
    m.costing.cost_process()
    m.objective = pyo.Objective(expr=m.costing.aggregate_operating_cost)
    return m
