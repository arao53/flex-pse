"""The load-shifting summary figure: price, net load, pump flow, tank volume,
and (if present) battery power/SOC, all shaded over the tariff's peak windows.
"""

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np

from .results import LoadShiftingResults

# Categorical palette (fixed order; see dataviz skill references/palette.md).
_BLUE, _ORANGE, _AQUA, _VIOLET = "#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7"
_GREEN, _SOC_INK, _PEAK_BAND, _GRID_COLOR = "#008300", "#52514e", "#9a9a9a", "#e1e0d9"


def _shade_peaks(ax, when, peak_mask) -> None:
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
            color=_PEAK_BAND,
            alpha=0.12,
            lw=0,
            zorder=0,
        )


def plot_results(results: LoadShiftingResults) -> plt.Figure:
    """Render the price/load/pump/tank(/battery) summary figure.

    Args:
        results: The extracted :class:`~helpers.results.LoadShiftingResults`.

    Returns:
        The matplotlib ``Figure``.
    """
    when, peak_mask = results.when, results.peak_mask
    plt.rcParams.update({"font.size": 10, "axes.grid": True})
    has_battery = results.battery_soc is not None
    n_panels = 6 if has_battery else 4
    fig, axes = plt.subplots(
        n_panels,
        1,
        figsize=(13, 2.05 * n_panels + 1.2),
        sharex=True,
        gridspec_kw={"hspace": 0.2},
    )

    ax_price, ax_load, ax_pump, ax_tank = axes[:4]

    ax_price.step(when, results.price.to_numpy(), where="post", color=_BLUE, lw=1.6)
    ax_price.set_ylabel("Energy price\n($/kWh)")
    ax_price.set_ylim(0, float(results.price.max()) * 1.25)
    ax_price.set_title(
        "Pump + tank"
        + (" + battery" if has_battery else "")
        + " load shifting under the configured tariff",
        fontsize=12,
        fontweight="bold",
        loc="left",
        pad=10,
    )

    ax_load.step(when, results.net_load, where="post", color=_GREEN, lw=1.6)
    ax_load.axhline(0, color="#888888", lw=0.8, zorder=0)
    ax_load.set_ylabel("Net facility load\n(kW)")

    ax_pump.step(when, results.pump_flow, where="post", color=_ORANGE, lw=1.4)
    ax_pump.set_ylabel("Pump flow\n(m³/hr)")
    ax_pump.set_ylim(-10, max(310, float(results.pump_flow.max()) * 1.05))

    ax_tank.plot(when, results.tank_volume, color=_AQUA, lw=1.6)
    ax_tank.set_ylabel("Tank volume\n(m³)")
    ax_tank.set_ylim(0, max(1050, float(results.tank_volume.max()) * 1.05))

    if has_battery:
        # Battery power and SOC are two single-axis panels, never a shared
        # twin-axis (dataviz skill: never a dual-axis chart).
        ax_batt_power, ax_batt_soc = axes[4], axes[5]
        ax_batt_power.step(
            when, results.battery_power, where="post", color=_VIOLET, lw=1.4
        )
        ax_batt_power.axhline(0, color="#888888", lw=0.8, zorder=0)
        ax_batt_power.set_ylabel("Battery power\n(kW, +charge/−discharge)")

        ax_batt_soc.plot(when, results.battery_soc * 100, color=_SOC_INK, lw=1.6)
        ax_batt_soc.set_ylabel("Battery SOC\n(%)")
        ax_batt_soc.set_ylim(0, 100)

    axes[-1].set_xlabel("local wall-clock time")

    for ax in axes:
        _shade_peaks(ax, when, peak_mask)
        ax.grid(color=_GRID_COLOR, lw=0.6)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.margins(x=0.005)

    axes[-1].xaxis.set_major_locator(mdates.DayLocator(interval=2))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    fig.autofmt_xdate(rotation=0, ha="center")

    ax_price.plot([], [], color=_PEAK_BAND, alpha=0.35, lw=8, label="peak price window")
    ax_price.legend(loc="upper right", frameon=False, fontsize=9)
    return fig
