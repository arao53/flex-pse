import marimo

__generated_with = "0.23.15"
app = marimo.App(width="medium", app_title="Pump + Tank Load Shifting")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Pump + storage tank load shifting

    A pump fills a storage tank against a fixed 100 m³/hr draw, over all of
    **July 2025** (hourly, 744 steps). Minimizing the
    [`FlexCosting`](../../src/flexops/costing/flex_costing.py) operating
    cost under a time-of-use tariff pushes pumping out of the tariff's
    summer weekday peak window (16:00-21:00).

    This mirrors the headline economic result in
    `src/flexops/tests/costing/test_load_shifting_component.py`, stretched
    from one day to a full month and made interactive: drag the sliders
    below and every plot re-solves the LP with HiGHS.

    Keep **tank initial volume ≤ tank max volume**, and give the pump
    enough headroom over the 100 m³/hr draw to make up for the zeroed
    peak hours — otherwise the solve becomes infeasible and the error
    surfaces in the cell below.
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

    from flexcore.solvers import get_solver
    from flexops.core.time_block import TimeBlock
    from flexops.costing import FlexCosting, is_peak, load_tariff, price_series
    from flexops.properties.simple_aqueous import SimpleAqueousFlow
    from flexops.unit_models import Pump, Tank

    return (
        Arc,
        FlexCosting,
        Path,
        Pump,
        SimpleAqueousFlow,
        Tank,
        TimeBlock,
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
    mo.vstack(
        [pump_max_flow, tank_max_volume, tank_initial_volume, include_demand_charges]
    )
    return (
        include_demand_charges,
        pump_max_flow,
        tank_initial_volume,
        tank_max_volume,
    )


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
    FlexCosting,
    Pump,
    SimpleAqueousFlow,
    Tank,
    TimeBlock,
    assert_optimal_termination,
    get_solver,
    is_peak,
    np,
    price_series,
    pump_max_flow,
    pyo,
    pyunits,
    tank_initial_volume,
    tank_max_volume,
    tariff,
):
    def build_month(*, pump_max_flow, tank_max_volume, tank_initial_volume, tariff):
        """Pump -> Arc -> Tank + FlexCosting over all of July 2025 (hourly)."""
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
        m.tank = Tank(
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

        m.costing.cost_process()
        m.objective = pyo.Objective(expr=m.costing.aggregate_operating_cost)

        last = list(m.time_block.time_index)[-1]
        m.terminal = pyo.Constraint(expr=m.tank.volume[last] >= tank_initial_volume)
        return m

    model = build_month(
        pump_max_flow=pump_max_flow.value,
        tank_max_volume=tank_max_volume.value,
        tank_initial_volume=tank_initial_volume.value,
        tariff=tariff,
    )
    results = get_solver(model=model, prefer="highs").solve(model)
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
    total_cost = pyo.value(model.objective)
    peak_pumping = float(pump_flow_series[peak_mask].sum())
    return (
        peak_mask,
        peak_pumping,
        price,
        pump_flow_series,
        tank_volume_series,
        total_cost,
        when,
    )


@app.cell(hide_code=True)
def _(mo, peak_pumping, total_cost):
    mo.md(f"""
    **Optimal operating cost:** ${total_cost:,.2f}
    &nbsp;&nbsp;|&nbsp;&nbsp;
    **Volume pumped during peak windows:** {peak_pumping:,.3f} m³
    """)
    return


@app.cell(hide_code=True)
def _(
    mdates,
    np,
    peak_mask,
    plt,
    price,
    pump_flow_series,
    tank_volume_series,
    when,
):
    plt.rcParams.update({"font.size": 10, "axes.grid": True})
    fig, (ax_price, ax_pump, ax_tank) = plt.subplots(
        3, 1, figsize=(13, 8.5), sharex=True, gridspec_kw={"hspace": 0.18}
    )

    blue, orange, aqua, peak_band = "#2a78d6", "#eb6834", "#1baf7a", "#9a9a9a"

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

    ax_price.step(when, price.to_numpy(), where="post", color=blue, lw=1.6)
    ax_price.set_ylabel("Energy price\n($/kWh)")
    ax_price.set_ylim(0, float(price.max()) * 1.25)
    ax_price.set_title(
        "July 2025 pump + tank load shifting under the TOU demo tariff",
        fontsize=12,
        fontweight="bold",
        loc="left",
        pad=10,
    )

    ax_pump.step(when, pump_flow_series, where="post", color=orange, lw=1.4)
    ax_pump.set_ylabel("Pump flow\n(m³/hr)")
    ax_pump.set_ylim(-10, max(310, float(pump_flow_series.max()) * 1.05))

    ax_tank.plot(when, tank_volume_series, color=aqua, lw=1.6)
    ax_tank.set_ylabel("Tank volume\n(m³)")
    ax_tank.set_ylim(0, max(1050, float(tank_volume_series.max()) * 1.05))
    ax_tank.set_xlabel("2025 (local wall-clock)")

    for ax in (ax_price, ax_pump, ax_tank):
        shade_peaks(ax)
        ax.grid(color="#e6e6e3", lw=0.6)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.margins(x=0.005)

    ax_tank.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    ax_tank.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    fig.autofmt_xdate(rotation=0, ha="center")

    ax_price.plot([], [], color=peak_band, alpha=0.35, lw=8, label="peak price window")
    ax_price.legend(loc="upper right", frameon=False, fontsize=9)
    fig
    return


if __name__ == "__main__":
    app.run()
