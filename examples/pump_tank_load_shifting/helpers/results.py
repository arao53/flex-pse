"""Extract solved-model time series and summary metrics for reporting/plotting."""

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pyomo.environ as pyo

from flexops.costing import is_peak, price_series

from .config import ExampleConfig


@dataclass
class LoadShiftingResults:
    """Time series and summary metrics pulled off a solved model."""

    when: pd.DatetimeIndex
    price: pd.Series
    peak_mask: np.ndarray
    pump_flow: np.ndarray
    tank_volume: np.ndarray
    net_load: np.ndarray
    battery_soc: np.ndarray | None
    battery_power: np.ndarray | None
    total_cost: float
    peak_pumping: float
    peak_net_energy: float


def extract_results(
    model: pyo.ConcreteModel, config: ExampleConfig, tariff: pd.DataFrame
) -> LoadShiftingResults:
    """Pull time series and summary metrics off a solved model.

    Args:
        model: The solved model (see :func:`helpers.build.solve_model`).
        config: The config the model was built from.
        tariff: The tariff DataFrame the model was costed against.

    Returns:
        The extracted :class:`LoadShiftingResults`.
    """
    time_block = model.time_block
    steps = list(time_block.time_index)
    when = time_block.datetime_index

    price = price_series(tariff, when)
    peak_mask = is_peak(tariff, when).to_numpy()
    pump_flow = np.array(
        [pyo.value(model.pump.inlet_state.flow_vol_phase[t, "Liq"]) for t in steps]
    )
    tank_volume = np.array([pyo.value(model.tank.volume[t]) for t in steps])
    net_load = np.array(
        [pyo.value(model.costing.aggregate_electrical_power[t]) for t in steps]
    )

    if config.battery.enabled:
        battery_soc = np.array([pyo.value(model.battery.soc[t]) for t in steps])
        battery_power = np.array(
            [
                pyo.value(
                    model.battery.power_charge[t] - model.battery.power_discharge[t]
                )
                for t in steps
            ]
        )
    else:
        battery_soc = None
        battery_power = None

    total_cost = pyo.value(model.objective)
    peak_pumping = float(pump_flow[peak_mask].sum())
    peak_net_energy = float(net_load[peak_mask].sum())

    return LoadShiftingResults(
        when=when,
        price=price,
        peak_mask=peak_mask,
        pump_flow=pump_flow,
        tank_volume=tank_volume,
        net_load=net_load,
        battery_soc=battery_soc,
        battery_power=battery_power,
        total_cost=total_cost,
        peak_pumping=peak_pumping,
        peak_net_energy=peak_net_energy,
    )
