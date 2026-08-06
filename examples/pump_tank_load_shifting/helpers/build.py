"""Build and solve the pump + tank (+ battery) model from an :class:`ExampleConfig`.

Mirrors ``flexops.tests.costing.test_load_shifting_component``'s headline
result: minimizing ``FlexCosting`` operating cost under a time-of-use tariff
shifts pumping (and battery discharge) out of the peak window. Every
construction parameter and connection comes from the config; nothing here is
hard-coded.
"""

from pathlib import Path

import pandas as pd
import pyomo.environ as pyo
from pyomo.environ import units as pyunits
from pyomo.network import Arc
from pyomo.opt import assert_optimal_termination

from flexcore.config.schema import UnitCommitmentConfig
from flexcore.solvers import get_solver
from flexops.core.time_block import TimeBlock
from flexops.costing import FlexCosting, load_tariff
from flexops.logic import add_startup_shutdown, add_status, relax
from flexops.properties.simple_aqueous import SimpleAqueousFlow
from flexops.unit_models import BatteryModel, Pump, Tank

from .config import ExampleConfig
from .units import parse_quantity


def load_tariff_for_config(config: ExampleConfig, base_dir: Path) -> pd.DataFrame:
    """Load the tariff a config points at, applying its demand-charge toggle.

    Args:
        config: The example config.
        base_dir: Directory ``config.tariff.path`` is relative to.

    Returns:
        The tariff rate-data DataFrame (``flexops.costing.load_tariff``).
    """
    tariff = load_tariff(str(Path(base_dir) / config.tariff.path))
    if not config.tariff.include_demand_charges:
        tariff = tariff[tariff["type"] != "demand"].reset_index(drop=True)
    return tariff


def _resolve_port(model: pyo.ConcreteModel, endpoint: str):
    """Resolve a ``'unit.port'`` config string to the live Pyomo Port."""
    unit_name, port_name = endpoint.split(".")
    return getattr(getattr(model, unit_name), port_name)


def build_model(config: ExampleConfig, tariff: pd.DataFrame) -> pyo.ConcreteModel:
    """Build the unsolved pump (+ tank + battery) model described by ``config``.

    Args:
        config: The validated example config.
        tariff: The tariff DataFrame (see :func:`load_tariff_for_config`).

    Returns:
        The unsolved ``ConcreteModel``.
    """
    m = pyo.ConcreteModel()
    m.time_block = TimeBlock(
        start_date=config.time.start_date,
        end_date=config.time.end_date,
        time_step=parse_quantity(config.time.time_step),
    )
    m.properties = SimpleAqueousFlow()
    m.costing = FlexCosting(time_block=m.time_block, tariff=tariff)

    m.pump = Pump(
        property_package=m.properties,
        energy_intensity=parse_quantity(config.pump.energy_intensity),
        costing_package=m.costing,
    )
    m.tank = Tank(
        property_package=m.properties,
        max_volume=parse_quantity(config.tank.max_volume),
        initial_volume=parse_quantity(config.tank.initial_volume),
    )

    for i, spec in enumerate(config.arcs):
        setattr(
            m,
            f"arc_{i}",
            Arc(
                source=_resolve_port(m, spec.source),
                destination=_resolve_port(m, spec.destination),
            ),
        )
    pyo.TransformationFactory("network.expand_arcs").apply_to(m)

    draw = pyo.value(
        pyunits.convert(parse_quantity(config.facility.draw), pyunits.m**3 / pyunits.hr)
    )
    max_flow = pyo.value(
        pyunits.convert(parse_quantity(config.pump.max_flow), pyunits.m**3 / pyunits.hr)
    )
    for t in m.time_block.time_index:
        m.tank.outlet_state.flow_vol_phase[t, "Liq"].fix(draw)
        pump_flow = m.pump.inlet_state.flow_vol_phase[t, "Liq"]
        pump_flow.setlb(0.0)
        pump_flow.setub(max_flow)

    if config.battery.enabled:
        # Behind-the-meter: no property_package/ports, energy-only unit.
        # unit_commitment.status=False keeps it an LP -- round-trip
        # efficiency < 1 already discourages simultaneous charge/discharge.
        m.battery = BatteryModel(
            capacity=parse_quantity(config.battery.capacity),
            power_charge_max=parse_quantity(config.battery.power_charge_max),
            power_discharge_max=parse_quantity(config.battery.power_discharge_max),
            eta_charge=config.battery.eta_charge,
            eta_discharge=config.battery.eta_discharge,
            soc_min=config.battery.soc_min,
            soc_max=config.battery.soc_max,
            initial_soc=(config.battery.soc_min + config.battery.soc_max) / 2,
            costing_package=m.costing,
            unit_commitment=UnitCommitmentConfig(status=False),
        )

    uc = config.pump.unit_commitment
    if uc.status:
        # Constant-intensity relation (power = energy_intensity * flow): a
        # flow bound converts directly to a power bound. min_on_power covers
        # the fixed facility draw so "always on" stays feasible.
        energy_intensity = pyo.value(
            pyunits.convert(
                parse_quantity(config.pump.energy_intensity), pyunits.kWh / pyunits.m**3
            )
        )
        status = add_status(
            m.pump,
            m.pump.power_electrical,
            energy_intensity * draw * pyunits.kW,
            energy_intensity * max_flow * pyunits.kW,
        )
        if uc.startup_shutdown:
            add_startup_shutdown(
                m.pump, status, min_uptime=uc.min_up, min_downtime=uc.min_down
            )
        if config.pump.relax:
            # First-class LP relaxation: same UC structure, domain switched
            # Binary -> UnitInterval, no rebuild.
            relax(m.pump)

    m.costing.cost_process()
    m.objective = pyo.Objective(expr=m.costing.aggregate_operating_cost)

    last = list(m.time_block.time_index)[-1]
    tank_initial = pyo.value(
        pyunits.convert(parse_quantity(config.tank.initial_volume), pyunits.m**3)
    )
    m.terminal = pyo.Constraint(expr=m.tank.volume[last] >= tank_initial)
    if config.battery.enabled:
        # Sustainable arbitrage: don't let the optimizer dump all stored
        # energy for a one-time credit at the horizon end.
        m.battery_terminal = pyo.Constraint(
            expr=m.battery.charge[last] >= m.battery.charge_init
        )
    return m


def solve_model(model: pyo.ConcreteModel):
    """Solve ``model`` with HiGHS and assert optimal termination.

    Args:
        model: The built (unsolved) model.

    Returns:
        The Pyomo results object.
    """
    # A 1% MIP gap keeps an exact (binary) pump-UC solve interactive-scale;
    # harmless (ignored) for the LP/relaxed cases.
    results = get_solver(model=model, prefer="highs").solve(
        model, options={"mip_rel_gap": 0.01}
    )
    assert_optimal_termination(results)
    return results
