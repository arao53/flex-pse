# Pump + tank load shifting

An interactive marimo notebook duplicating the headline economic result from
`src/flexops/tests/costing/test_load_shifting_component.py`: a pump fills a
storage tank against a fixed draw, and minimizing the `FlexCosting` operating
cost under a time-of-use tariff shifts pumping out of the peak-price window.
Here the horizon is stretched from one day to a full month (July 2025, hourly)
and the pump/tank sizing and tariff demand charges are exposed as sliders, so
each drag re-solves the LP with HiGHS and redraws the plot.

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

## Files

- `load_shifting.py` — the marimo notebook.
- `tariff_tou_demo.json` — a copy of
  `src/flexops/tests/fixtures/tariff_tou_demo.json` (the demo TOU tariff), kept
  local so the example has no path dependency on the test suite.
