# flex-pse

An open-source Pyomo/IDAES platform for industrial energy-flexibility
optimization — model a facility as a time-discretized optimization problem,
parameterize it from plant data, and solve rolling-horizon scheduling problems
against real electricity tariffs and demand-response signals.

## Install

Environments are managed with two tools:

- **conda** installs the optimization stack — the Python version, `pyomo`, and
  `idaes-pse`, pinned in [`environment.yml`](environment.yml) — because IDAES
  ships its solver binaries through conda.
- **uv** installs everything else (the package itself and all other
  dependencies).

```bash
# 1. Create and activate the conda environment from environment.yml.
conda env create -f environment.yml
conda activate flex-pse

# 2. Install idaes solver binaries.
idaes get-extensions

# 3. Install the project and remaining dependencies with uv.
uv pip install -e ".[dev]"

# 4. Enable the git hooks.
pre-commit install
pre-commit install --hook-type pre-push
```

## Development

This project is built milestone-by-milestone. See [`PLAN.md`](PLAN.md) for the
roadmap and [`plan/00_conventions.md`](plan/00_conventions.md) for the rules that govern every change.
