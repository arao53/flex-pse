# Pump + tank + battery load shifting

An interactive marimo notebook duplicating the headline economic result from
`src/flexops/tests/costing/test_load_shifting_component.py`: a pump fills a
storage tank against a fixed draw, and minimizing the `FlexCosting` operating
cost under a time-of-use tariff shifts pumping (and battery discharge) out of
the peak-price window. The horizon is stretched from one day to a full month
(July 2025, hourly), a behind-the-meter battery and pump unit-commitment logic
are added (M08), and the pump/tank/battery sizing is exposed as sliders.

**The model is an ordinary flex-pse `ModelConfig`**, built by
`flexops.core.build.build_model` exactly like any other flex-pse model (see
`docs/how_to/build_a_plant.md`, "the config-driven twin"). `ExampleConfig`
(`helpers/config.py`) is that `ModelConfig` plus the handful of knobs it
cannot express -- the facility's fixed draw, the pump's flow cap, and its
unit-commitment LP relaxation. Clicking **Solve** writes every slider value
into an `ExampleConfig` and saves it to `config.json` in this directory; the
notebook then reads that file straight back off disk and `helpers/build.py`
builds and solves the Pyomo model from it. Edit `config.json` by hand (or
drive it from another script) and it builds and solves exactly the same way,
with no notebook involved.

This example is standalone (its own copy of the demo tariff, no dependency on
the test suite) and intentionally lives outside the milestone system. It is
expected to move into a separate examples repo after the M09 API freeze.

## Run it

Requires the `dev` extra (installed automatically by `environment.yml`, or via
`pip install -e ".[dev]"`), which includes `marimo` and `matplotlib`.

```bash
marimo edit examples/pump_tank_load_shifting/load_shifting.py   # developer mode
marimo run examples/pump_tank_load_shifting/load_shifting.py    # interactive app
python examples/pump_tank_load_shifting/load_shifting.py        # plain script, default slider values
```

Or skip the notebook entirely and build/solve straight from the config file:

```python
from pathlib import Path

from helpers.build import build_model, solve_model
from helpers.config import load_config
from helpers.results import extract_results

example_dir = Path("examples/pump_tank_load_shifting")
config = load_config(example_dir / "config.json")
model = build_model(config)
solve_model(model)
results = extract_results(model, config)
```

## Files

- `load_shifting.py` — the marimo notebook: sliders write an `ExampleConfig`
  to `config.json`, then read it back to build, solve, and plot.
- `config.json` — an `ExampleConfig`: a full flex-pse `ModelConfig` (time
  horizon, tariff, pump/tank/battery units and their connections/arcs) plus
  the facility draw, pump flow cap, and pump unit-commitment relaxation that
  a `ModelConfig` cannot express; the single source of truth the model is
  built from.
- `tariff_tou_demo.json` — a copy of
  `src/flexops/tests/fixtures/tariff_tou_demo.json` (the demo TOU tariff), kept
  local so the example has no path dependency on the test suite.
- `helpers/` — config-driven build/solve/plot helpers, factored out of the
  notebook so they're reusable and legible on their own:
  - `config.py` — the `ExampleConfig` pydantic schema (a
    `flexcore.config.schema.ModelConfig` plus the example-specific knobs
    above) plus `load_config`/`save_config`.
  - `build.py` — `build_model`/`solve_model`. `build_model` delegates the
    model itself to `flexops.core.build.build_model` and applies only what a
    `ModelConfig` cannot express.
  - `results.py` — `extract_results`, pulling time series and summary metrics
    off a solved model.
  - `plotting.py` — `plot_results`, the price/load/pump/tank/battery figure.
