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
    [unit-commitment logic layer](../../src/flexops/logic/) (M08).

    **The model is built entirely from `config.json`.** Every slider below
    only edits an in-memory [`ExampleConfig`](helpers/config.py); clicking
    **Solve** writes it to `config.json` in this directory, then
    [`helpers.build`](helpers/build.py) reads that file straight back off
    disk and constructs the Pyomo model from it -- the sliders never reach
    the model directly. Edit `config.json` by hand (or point another script
    at it) and it builds and solves exactly the same way.

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

    from flexcore.config.schema import TimeConfig, UnitCommitmentConfig
    from helpers.build import build_model, load_tariff_for_config, solve_model
    from helpers.config import (
        BatteryConfig,
        ExampleConfig,
        FacilityConfig,
        PumpConfig,
        TankConfig,
        TariffConfig,
        load_config,
        save_config,
    )
    from helpers.plotting import plot_results
    from helpers.results import extract_results

    EXAMPLE_DIR = Path(__file__).parent
    CONFIG_PATH = EXAMPLE_DIR / "config.json"

    return (
        BatteryConfig,
        CONFIG_PATH,
        EXAMPLE_DIR,
        ExampleConfig,
        FacilityConfig,
        PumpConfig,
        TankConfig,
        TariffConfig,
        TimeConfig,
        UnitCommitmentConfig,
        build_model,
        extract_results,
        load_config,
        load_tariff_for_config,
        plot_results,
        save_config,
        solve_model,
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
                "Adjust the sliders above, then click **Solve**. This writes "
                "your choices to `config.json` and re-solves from that file. "
                "The battery and relaxed pump unit commitment are fast LPs; "
                "the exact (binary) pump unit commitment is a MILP and can "
                "take tens of seconds over the full month."
            ),
            run_button,
        ]
    )
    return get_solved_once, run_button, set_solved_once


@app.cell
def _(
    BatteryConfig,
    CONFIG_PATH,
    EXAMPLE_DIR,
    ExampleConfig,
    FacilityConfig,
    PumpConfig,
    TankConfig,
    TariffConfig,
    TimeConfig,
    UnitCommitmentConfig,
    battery_capacity,
    battery_charge_max,
    battery_discharge_max,
    battery_soc_max,
    battery_soc_min,
    build_model,
    extract_results,
    get_solved_once,
    include_battery,
    include_demand_charges,
    load_config,
    load_tariff_for_config,
    mo,
    pump_max_flow,
    pump_min_downtime,
    pump_min_uptime,
    pump_uc_exact,
    pump_unit_commitment,
    run_button,
    save_config,
    set_solved_once,
    solve_model,
    tank_initial_volume,
    tank_max_volume,
):
    # Gate the (potentially expensive, MILP-capable) solve behind the Solve
    # button: this cell still reacts to every slider (it must, to read their
    # current .value), but only *writes+solves* on the very first pass (so
    # `python load_shifting.py` and a fresh `marimo run` still show a solved
    # result) or when the button was just clicked -- not on every slider drag.
    mo.stop(
        get_solved_once() and not run_button.value,
        mo.md("*Adjust sliders, then click **Solve** to re-run the optimization.*"),
    )
    set_solved_once(True)

    # 1. Every knob above -> one ExampleConfig -> config.json. The model is
    #    never built from the sliders directly.
    draft_config = ExampleConfig(
        time=TimeConfig(
            start_date="2025-07-01", end_date="2025-08-01", time_step="1 hr"
        ),
        tariff=TariffConfig(
            path="tariff_tou_demo.json",
            include_demand_charges=include_demand_charges.value,
        ),
        facility=FacilityConfig(draw="100 m**3/hr"),
        pump=PumpConfig(
            max_flow=f"{pump_max_flow.value} m**3/hr",
            unit_commitment=UnitCommitmentConfig(
                status=pump_unit_commitment.value,
                startup_shutdown=pump_unit_commitment.value,
                min_up=pump_min_uptime.value,
                min_down=pump_min_downtime.value,
            ),
            relax=not pump_uc_exact.value,
        ),
        tank=TankConfig(
            max_volume=f"{tank_max_volume.value} m**3",
            initial_volume=f"{tank_initial_volume.value} m**3",
        ),
        battery=BatteryConfig(
            enabled=include_battery.value,
            capacity=f"{battery_capacity.value} kWh",
            power_charge_max=f"{battery_charge_max.value} kW",
            power_discharge_max=f"{battery_discharge_max.value} kW",
            soc_min=battery_soc_min.value / 100.0,
            soc_max=battery_soc_max.value / 100.0,
        ),
    )
    save_config(draft_config, CONFIG_PATH)

    # 2. Read config.json back -- everything below is built purely from the
    #    on-disk artifact, not from `draft_config` above.
    config = load_config(CONFIG_PATH)
    tariff = load_tariff_for_config(config, EXAMPLE_DIR)
    model = build_model(config, tariff)
    solve_model(model)
    results = extract_results(model, config, tariff)
    return (results,)


@app.cell(hide_code=True)
def _(mo, results):
    mo.md(f"""
    **Optimal operating cost:** ${results.total_cost:,.2f}
    &nbsp;&nbsp;|&nbsp;&nbsp;
    **Volume pumped during peak windows:** {results.peak_pumping:,.3f} m³
    &nbsp;&nbsp;|&nbsp;&nbsp;
    **Net facility load during peak windows:** {results.peak_net_energy:,.1f} kWh
    """)
    return


@app.cell(hide_code=True)
def _(plot_results, results):
    plot_results(results)
    return


if __name__ == "__main__":
    app.run()
