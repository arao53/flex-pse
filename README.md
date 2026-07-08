# flex-pse

An open-source Pyomo/IDAES platform for industrial energy-flexibility
optimization — model a facility as a time-discretized optimization problem,
parameterize it from plant data, and solve rolling-horizon scheduling problems
against real electricity tariffs and demand-response signals.

## Install

```bash
pip install -e ".[dev]"
pre-commit install
pre-commit install --hook-type pre-push
```

## Development

This project is built milestone-by-milestone. See [`PLAN.md`](PLAN.md) for the
roadmap and [`plan/00_conventions.md`](plan/00_conventions.md) for the rules that govern every change.
