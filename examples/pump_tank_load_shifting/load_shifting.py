import marimo

__generated_with = "0.23.15"
app = marimo.App(
    width="medium",
    app_title="Pump + Tank + Battery Load Shifting",
)


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Pump + storage tank + battery load shifting

    A pump fills a storage tank against a fixed 100 m³/hr draw, and a
    behind-the-meter battery may charge/discharge alongside it, over all of
    **July 2025** (hourly, 744 steps). Minimizing the
    [`FlexCosting`](../../src/flexops/costing/flex_costing.py) operating
    cost under a time-of-use tariff pushes pumping — and battery discharge —
    out of the tariff's summer weekday peak window (16:00-21:00), which shows
    up directly in the **net facility load** (the plant's total electrical
    draw, `costing.aggregate_electrical_power`): pump draw plus the
    battery's charge (positive) / discharge (negative).

    This mirrors the headline economic result in
    `src/flexops/tests/costing/test_load_shifting_component.py`, stretched
    from one day to a full month, extended with a
    [`BatteryModel`](../../src/flexops/unit_models/battery.py) and the
    [unit-commitment logic layer](../../src/flexops/logic/) (M08), and made
    interactive: drag the sliders below, click **Solve**, and every plot
    re-solves the model with HiGHS.

    Keep **tank initial volume ≤ tank max volume**, and give the pump enough
    headroom over the 100 m³/hr draw to make up for the zeroed peak hours —
    otherwise the solve becomes infeasible and the error surfaces below.

    **Battery.** Sizing the min/max state-of-charge window and the max
    charge/discharge power controls how much load the battery can shift; it
    is behind-the-meter (round-trip losses only cost the facility, they are
    never "free" to the grid) and is built as an LP (no on/off binary needed:
    the round-trip efficiency already keeps charging and discharging
    mutually exclusive at the optimum).

    **Pump unit commitment.** Enabling it adds a status binary plus
    startup/shutdown transition and minimum uptime/downtime logic
    (`flexops.logic.add_status`/`add_startup_shutdown`) — the pump can no
    longer dribble flow arbitrarily; once on, it must stay on (and once off,
    stay off) for the configured minimum number of hours. By default the
    added binaries are immediately **relaxed to a continuous LP**
    (`flexops.logic.relax`) so the interactive solve stays fast; check
    "solve exactly" to see the true binary MILP (much slower — tens of
    seconds over the full month).
    """)
    return


@app.cell
def _():
    from pathlib import Path

    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    import numpy as np
    import pyomo.environ as pyo
    from pyomo.environ import units as pyunits
    from pyomo.network import Arc
    from pyomo.opt import assert_optimal_termination

    from flexcore.config.schema import UnitCommitmentConfig
    from flexcore.solvers import get_solver
    from flexops.core.time_block import TimeBlock
    from flexops.costing import FlexCosting, is_peak, load_tariff, price_series
    from flexops.logic import add_startup_shutdown, add_status, relax
    from flexops.properties.simple_aqueous import SimpleAqueousFlow
    from flexops.unit_models import BatteryModel, Pump, StorageTank

    return (
        Arc,
        BatteryModel,
        FlexCosting,
        Path,
        Pump,
        SimpleAqueousFlow,
        StorageTank,
        TimeBlock,
        UnitCommitmentConfig,
        add_startup_shutdown,
        add_status,
        assert_optimal_termination,
        get_solver,
        is_peak,
        load_tariff,
        mdates,
        np,
        plt,
        price_series,
        pyo,
        pyunits,
        relax,
    )


@app.cell(hide_code=True)
def _(mo):
    pump_max_flow = mo.ui.slider(
        start=100, stop=500, step=10, value=300, label="Pump max flow (m³/hr)"
    )
    tank_max_volume = mo.ui.slider(
        start=200, stop=2000, step=50, value=1000, label="Tank max volume (m³)"
    )
    tank_initial_volume = mo.ui.slider(
        start=0, stop=1000, step=50, value=200, label="Tank initial volume (m³)"
    )
    include_demand_charges = mo.ui.checkbox(value=True, label="Include demand charges")

    include_battery = mo.ui.checkbox(value=True, label="Include battery")
    battery_capacity = mo.ui.slider(
        start=200, stop=4000, step=100, value=2000, label="Battery capacity (kWh)"
    )
    battery_charge_max = mo.ui.slider(
        start=50, stop=1000, step=50, value=500, label="Max charge power (kW)"
    )
    battery_discharge_max = mo.ui.slider(
        start=50, stop=1000, step=50, value=500, label="Max discharge power (kW)"
    )
    battery_soc_min = mo.ui.slider(
        start=0, stop=50, step=5, value=10, label="Min state of charge (%)"
    )
    battery_soc_max = mo.ui.slider(
        start=50, stop=100, step=5, value=90, label="Max state of charge (%)"
    )

    pump_unit_commitment = mo.ui.checkbox(
        value=False, label="Pump unit commitment (min uptime/downtime)"
    )
    pump_min_uptime = mo.ui.slider(
        start=1, stop=12, step=1, value=3, label="Min uptime (hr)"
    )
    pump_min_downtime = mo.ui.slider(
        start=1, stop=12, step=1, value=3, label="Min downtime (hr)"
    )
    pump_uc_exact = mo.ui.checkbox(
        value=False, label="Solve exactly (binary MILP, slower)"
    )

    mo.vstack(
        [
            mo.md("**Pump + tank**"),
            pump_max_flow,
            tank_max_volume,
            tank_initial_volume,
            include_demand_charges,
            mo.md("**Battery** (behind-the-meter)"),
            include_battery,
            battery_capacity,
            battery_charge_max,
            battery_discharge_max,
            battery_soc_min,
            battery_soc_max,
            mo.md("**Pump unit commitment**"),
            pump_unit_commitment,
            pump_min_uptime,
            pump_min_downtime,
            pump_uc_exact,
        ]
    )
    return (
        battery_capacity,
        battery_charge_max,
        battery_discharge_max,
        battery_soc_max,
        battery_soc_min,
        include_battery,
        include_demand_charges,
        pump_max_flow,
        pump_min_downtime,
        pump_min_uptime,
        pump_uc_exact,
        pump_unit_commitment,
        tank_initial_volume,
        tank_max_volume,
    )


@app.cell(hide_code=True)
def _(mo):
    get_solved_once, set_solved_once = mo.state(False)
    run_button = mo.ui.run_button(label="Solve")
    mo.vstack(
        [
            mo.md(
                "Adjust the sliders above, then click **Solve**. The battery "
                "and relaxed pump unit commitment are fast LPs; the exact "
                "(binary) pump unit commitment is a MILP and can take tens of "
                "seconds over the full month."
            ),
            run_button,
        ]
    )
    return get_solved_once, run_button, set_solved_once


@app.cell
def _(Path, include_demand_charges, load_tariff):
    _tariff_path = Path(__file__).parent / "tariff_tou_demo.json"
    tariff = load_tariff(str(_tariff_path))
    if not include_demand_charges.value:
        tariff = tariff[tariff["type"] != "demand"].reset_index(drop=True)
    return (tariff,)


@app.cell
def _(
    Arc,
    BatteryModel,
    FlexCosting,
    Pump,
    SimpleAqueousFlow,
    StorageTank,
    TimeBlock,
    UnitCommitmentConfig,
    add_startup_shutdown,
    add_status,
    assert_optimal_termination,
    battery_capacity,
    battery_charge_max,
    battery_discharge_max,
    battery_soc_max,
    battery_soc_min,
    get_solved_once,
    get_solver,
    include_battery,
    is_peak,
    mo,
    np,
    price_series,
    pump_max_flow,
    pump_min_downtime,
    pump_min_uptime,
    pump_uc_exact,
    pump_unit_commitment,
    pyo,
    pyunits,
    relax,
    run_button,
    set_solved_once,
    tank_initial_volume,
    tank_max_volume,
    tariff,
):
    # Gate the (potentially expensive, MILP-capable) solve behind the Solve
    # button: this cell still reacts to every slider (it must, to read their
    # current .value), but only *solves* on the very first pass (so `python
    # load_shifting.py` and a fresh `marimo run` still show a solved result)
    # or when the button was just clicked -- not on every slider drag.
    mo.stop(
        get_solved_once() and not run_button.value,
        mo.md("*Adjust sliders, then click **Solve** to re-run the optimization.*"),
    )
    set_solved_once(True)

    def build_month(
        *,
        pump_max_flow,
        tank_max_volume,
        tank_initial_volume,
        tariff,
        include_battery,
        battery_capacity,
        battery_charge_max,
        battery_discharge_max,
        battery_soc_min,
        battery_soc_max,
        pump_unit_commitment,
        pump_min_uptime,
        pump_min_downtime,
        pump_uc_exact,
    ):
        """Pump -> Arc -> StorageTank (+ optional battery) + FlexCosting, July 2025."""
        m = pyo.ConcreteModel()
        m.time_block = TimeBlock(
            start_date="2025-07-01", end_date="2025-08-01", time_step=1 * pyunits.hr
        )
        m.properties = SimpleAqueousFlow(fixed_density=True)
        m.costing = FlexCosting(time_block=m.time_block, tariff=tariff)

        m.pump = Pump(
            property_package=m.properties,
            energy_intensity=0.5 * pyunits.kWh / pyunits.m**3,
            costing_package=m.costing,
        )
        m.tank = StorageTank(
            property_package=m.properties,
            max_volume=tank_max_volume * pyunits.m**3,
            initial_volume=tank_initial_volume * pyunits.m**3,
        )
        m.arc = Arc(source=m.pump.outlet, destination=m.tank.inlet)
        pyo.TransformationFactory("network.expand_arcs").apply_to(m)

        for t in m.time_block.time_index:
            m.tank.outlet_state.flow_vol_phase[t, "Liq"].fix(100.0)
            pump_flow = m.pump.inlet_state.flow_vol_phase[t, "Liq"]
            pump_flow.setlb(0.0)
            pump_flow.setub(pump_max_flow)

        if include_battery:
            # Behind-the-meter: no property_package/ports, energy-only unit.
            # unit_commitment.status=False keeps it an LP -- round-trip
            # efficiency < 1 already discourages simultaneous charge/discharge.
            m.battery = BatteryModel(
                capacity=battery_capacity * pyunits.kWh,
                power_charge_max=battery_charge_max * pyunits.kW,
                power_discharge_max=battery_discharge_max * pyunits.kW,
                eta_charge=0.95,
                eta_discharge=0.95,
                soc_min=battery_soc_min,
                soc_max=battery_soc_max,
                initial_soc=(battery_soc_min + battery_soc_max) / 2,
                costing_package=m.costing,
                unit_commitment=UnitCommitmentConfig(status=False),
            )

        if pump_unit_commitment:
            # Constant-intensity relation (power = energy_intensity * flow):
            # a flow bound converts directly to a power bound. min_on_power
            # covers the fixed 100 m^3/hr draw so "always on" stays feasible.
            max_power = 0.5 * pump_max_flow
            min_on_power = 0.5 * 100.0
            status = add_status(
                m.pump,
                m.pump.power_electrical,
                min_on_power * pyunits.kW,
                max_power * pyunits.kW,
            )
            add_startup_shutdown(
                m.pump,
                status,
                min_uptime=pump_min_uptime,
                min_downtime=pump_min_downtime,
            )
            if not pump_uc_exact:
                # First-class LP relaxation (M08): same UC structure, domain
                # switched Binary -> UnitInterval, no rebuild.
                relax(m.pump)

        m.costing.cost_process()
        m.objective = pyo.Objective(expr=m.costing.aggregate_operating_cost)

        last = list(m.time_block.time_index)[-1]
        m.terminal = pyo.Constraint(expr=m.tank.volume[last] >= tank_initial_volume)
        if include_battery:
            # Sustainable arbitrage: don't let the optimizer dump all stored
            # energy for a one-time credit at the horizon end.
            m.battery_terminal = pyo.Constraint(
                expr=m.battery.charge[last] >= m.battery.charge_init
            )
        return m

    model = build_month(
        pump_max_flow=pump_max_flow.value,
        tank_max_volume=tank_max_volume.value,
        tank_initial_volume=tank_initial_volume.value,
        tariff=tariff,
        include_battery=include_battery.value,
        battery_capacity=battery_capacity.value,
        battery_charge_max=battery_charge_max.value,
        battery_discharge_max=battery_discharge_max.value,
        battery_soc_min=battery_soc_min.value / 100.0,
        battery_soc_max=battery_soc_max.value / 100.0,
        pump_unit_commitment=pump_unit_commitment.value,
        pump_min_uptime=pump_min_uptime.value,
        pump_min_downtime=pump_min_downtime.value,
        pump_uc_exact=pump_uc_exact.value,
    )
    # A 1% MIP gap keeps the exact (binary) pump-UC solve interactive-scale;
    # harmless (ignored) for the LP/relaxed cases.
    results = get_solver(model=model, prefer="highs").solve(
        model, options={"mip_rel_gap": 0.01}
    )
    assert_optimal_termination(results)

    time_block = model.time_block
    steps = list(time_block.time_index)
    when = time_block.datetime_index

    price = price_series(tariff, when)
    peak_mask = is_peak(tariff, when).to_numpy()
    pump_flow_series = np.array(
        [pyo.value(model.pump.inlet_state.flow_vol_phase[t, "Liq"]) for t in steps]
    )
    tank_volume_series = np.array([pyo.value(model.tank.volume[t]) for t in steps])
    net_load_series = np.array(
        [pyo.value(model.costing.aggregate_electrical_power[t]) for t in steps]
    )
    if include_battery.value:
        battery_soc_series = np.array([pyo.value(model.battery.soc[t]) for t in steps])
        battery_power_series = np.array(
            [
                pyo.value(
                    model.battery.power_charge[t] - model.battery.power_discharge[t]
                )
                for t in steps
            ]
        )
    else:
        battery_soc_series = None
        battery_power_series = None

    total_cost = pyo.value(model.objective)
    peak_pumping = float(pump_flow_series[peak_mask].sum())
    peak_net_energy = float(net_load_series[peak_mask].sum())
    return (
        battery_power_series,
        battery_soc_series,
        net_load_series,
        peak_mask,
        peak_net_energy,
        peak_pumping,
        price,
        pump_flow_series,
        tank_volume_series,
        total_cost,
        when,
    )


@app.cell(hide_code=True)
def _(mo, peak_net_energy, peak_pumping, total_cost):
    mo.md(f"""
    **Optimal operating cost:** ${total_cost:,.2f}
    &nbsp;&nbsp;|&nbsp;&nbsp;
    **Volume pumped during peak windows:** {peak_pumping:,.3f} m³
    &nbsp;&nbsp;|&nbsp;&nbsp;
    **Net facility load during peak windows:** {peak_net_energy:,.1f} kWh
    """)
    return


@app.cell(hide_code=True)
def _(
    battery_power_series,
    battery_soc_series,
    mdates,
    net_load_series,
    np,
    peak_mask,
    plt,
    price,
    pump_flow_series,
    tank_volume_series,
    when,
):
    plt.rcParams.update({"font.size": 10, "axes.grid": True})
    has_battery = battery_soc_series is not None
    n_panels = 6 if has_battery else 4
    fig, axes = plt.subplots(
        n_panels,
        1,
        figsize=(13, 2.05 * n_panels + 1.2),
        sharex=True,
        gridspec_kw={"hspace": 0.2},
    )

    # Categorical palette (fixed order; see dataviz skill references/palette.md).
    blue, orange, aqua, green, violet = (
        "#2a78d6",
        "#eb6834",
        "#1baf7a",
        "#008300",
        "#4a3aa7",
    )
    soc_ink, peak_band, grid_color = "#52514e", "#9a9a9a", "#e1e0d9"

    def shade_peaks(ax):
        edges = np.diff(peak_mask.astype(int))
        starts = np.where(edges == 1)[0] + 1
        stops = np.where(edges == -1)[0] + 1
        if peak_mask[0]:
            starts = np.r_[0, starts]
        if peak_mask[-1]:
            stops = np.r_[stops, len(peak_mask)]
        for s, e in zip(starts, stops):
            ax.axvspan(
                when[s],
                when[min(e, len(when) - 1)],
                color=peak_band,
                alpha=0.12,
                lw=0,
                zorder=0,
            )

    ax_price, ax_load, ax_pump, ax_tank = axes[:4]

    ax_price.step(when, price.to_numpy(), where="post", color=blue, lw=1.6)
    ax_price.set_ylabel("Energy price\n($/kWh)")
    ax_price.set_ylim(0, float(price.max()) * 1.25)
    ax_price.set_title(
        "July 2025 pump + tank"
        + (" + battery" if has_battery else "")
        + " load shifting under the TOU demo tariff",
        fontsize=12,
        fontweight="bold",
        loc="left",
        pad=10,
    )

    ax_load.step(when, net_load_series, where="post", color=green, lw=1.6)
    ax_load.axhline(0, color="#888888", lw=0.8, zorder=0)
    ax_load.set_ylabel("Net facility load\n(kW)")

    ax_pump.step(when, pump_flow_series, where="post", color=orange, lw=1.4)
    ax_pump.set_ylabel("Pump flow\n(m³/hr)")
    ax_pump.set_ylim(-10, max(310, float(pump_flow_series.max()) * 1.05))

    ax_tank.plot(when, tank_volume_series, color=aqua, lw=1.6)
    ax_tank.set_ylabel("Tank volume\n(m³)")
    ax_tank.set_ylim(0, max(1050, float(tank_volume_series.max()) * 1.05))

    if has_battery:
        # Battery power and SOC are two single-axis panels, never a shared
        # twin-axis (dataviz skill: never a dual-axis chart).
        ax_batt_power, ax_batt_soc = axes[4], axes[5]
        ax_batt_power.step(
            when, battery_power_series, where="post", color=violet, lw=1.4
        )
        ax_batt_power.axhline(0, color="#888888", lw=0.8, zorder=0)
        ax_batt_power.set_ylabel("Battery power\n(kW, +charge/−discharge)")

        ax_batt_soc.plot(when, battery_soc_series * 100, color=soc_ink, lw=1.6)
        ax_batt_soc.set_ylabel("Battery SOC\n(%)")
        ax_batt_soc.set_ylim(0, 100)

    axes[-1].set_xlabel("2025 (local wall-clock)")

    for ax in axes:
        shade_peaks(ax)
        ax.grid(color=grid_color, lw=0.6)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.margins(x=0.005)

    axes[-1].xaxis.set_major_locator(mdates.DayLocator(interval=2))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    fig.autofmt_xdate(rotation=0, ha="center")

    ax_price.plot([], [], color=peak_band, alpha=0.35, lw=8, label="peak price window")
    ax_price.legend(loc="upper right", frameon=False, fontsize=9)
    fig
    return


if __name__ == "__main__":
    app.run()
