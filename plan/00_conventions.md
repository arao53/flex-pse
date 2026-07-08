# 00 — Conventions

Non-negotiable rules for everything written into this repository. Milestone work
orders assume these; they are not repeated there.

## 1. Repository layout

```
flex-pse/
├── pyproject.toml                  # single distribution: "flex-pse"
├── .importlinter                   # import DAG contracts (see §6)
├── .pre-commit-config.yaml         # black, ruff, import-linter
├── CHANGELOG.md                    # Keep a Changelog format, "Unreleased" section on top
├── LICENSE                         # BSD-3-Clause
├── .github/workflows/              # ci.yml, nightly.yml, upstream-canary.yml, docs.yml
├── src/
│   ├── flexcore/                   # shared substrate — never imports the other three
│   │   ├── compat/                 # idaes.py (primary idaes import point), pyomo_ext.py,
│   │   │                           # versions.py, tracked.py (allowlist of tracked idaes/pyomo tool imports)
│   │   ├── solvers/                # classify.py, registry.py, facade.py
│   │   ├── config/                 # schema.py (pydantic: Unit/Plant/Network/Time/Costing/ModelConfig),
│   │   │                           # io.py (YAML canonical), schemas/ (exported JSON Schema)
│   │   └── tests/                  # (no econ module — tariff/cost is the external eeco package)
│   ├── flexops/
│   │   ├── core/                   # time_block.py, ops_block.py, plant_block.py,
│   │   │                           # network_block.py, registration.py, build.py (build_model)
│   │   ├── unit_models/
│   │   │   ├── base/               # siso.py, sido.py, dido.py (IO-topology base blocks)
│   │   │   └──                     # pump.py, storage_tank.py, battery.py, separator.py,
│   │   │                           # exchanger.py, electrolysis.py, ro_skid.py, combustor.py,
│   │   │                           # constant_intensity.py
│   │   ├── logic/                  # status.py, startup_shutdown.py, dwell.py, delays.py,
│   │   │                           # conditional.py, degeneracy.py (model-level), bypass.py
│   │   ├── costing/                # flex_costing.py, unit_costing.py, tariff.py (sole eeco import point)
│   │   ├── design/                 # multi-period design wrapper (M16): DesignModel, merge_for_design
│   │   ├── properties/             # simple_aqueous.py
│   │   ├── testing/                # harness.py (public, shipped)
│   │   └── tests/
│   ├── flexparameterize/
│   │   ├── tags.py, validate.py, apply.py, emit.py  # apply.py = the 2-way mutate-in-place path
│   │   ├── regression/             # base.py, constant.py, linear.py
│   │   └── tests/
│   └── flexschedule/
│       ├── horizon.py, sequences.py, setpoints.py, smoothing.py
│       └── tests/
├── examples/                       # myst-nb notebooks + api_freeze.py
└── docs/                           # sphinx (see plan/03_documentation.md)
```

Tests are **colocated** with their package (`src/<pkg>/tests/`), mirroring the
module layout (`tests/core/test_time_block.py` tests `core/time_block.py`).
When a package is later split into its own repo, its tests move with it.

## 2. Naming

- Packages/modules: `snake_case`. Classes: `CapWords`. Functions/variables:
  `snake_case`. Constants: `UPPER_SNAKE`.
- Pyomo model components follow IDAES conventions where one exists
  (`flow_vol`, `pressure`, `temperature`), and this project's energy
  nomenclature otherwise (see `plan/01_architecture.md` §4):
  - `electrical_work[t]` — electrical draw of a unit, **kW** (a power, despite
    the name — the name is the project-wide standard).
  - `thermal_work[t]` — thermal/gas-driven duty of a unit, **kW**.
  - Never introduce variables named bare `power`, `energy`, or `work`.
- Time index is always named `t`, iterating `time_block.time_points`.
- User-facing constructors take **keyword arguments only** (enforce with `*` in
  signatures). ISO-8601 date strings (`"2025-01-01"`) or `datetime` objects; never
  ambiguous `"1-1-2025"`.
- Config file keys: `snake_case`; every persisted config has a top-level
  `schema_version: int`.

## 3. Style & tooling

- Python ≥ 3.10. Target the oldest supported version in code (no 3.11+-only syntax).
- Format with **black** (default settings); lint with **ruff** (rule set pinned in
  `pyproject.toml`; do not inline-silence rules without a comment explaining why).
- **Type hints on all public functions, methods, and class attributes.** Internal
  helpers should have them too unless Pyomo typing makes it hopeless (then annotate
  what you can).
- **Google-style docstrings** on every public module, class, and function.
  A unit-model class docstring must include: one-paragraph model description,
  the governing equations in LaTeX (``.. math::``), a short usage snippet, and
  cross-references to its config options.
- No mutable default arguments. No `print` (use `logging`, logger per module:
  `_log = logging.getLogger(__name__)`).
- Exceptions: raise project exceptions (`FlexConfigError`, `FlexSolverError`,
  `FlexDataError` — defined in `flexcore.exceptions`) with messages that state
  what was wrong **and what the user should do** ("Solver 'ipopt' not found.
  Install it with `idaes get-extensions` or pass solver='highs'.").

## 4. Configuration rules

Two config layers, never mixed:

1. **Persisted config** (files a user or FlexParameterize writes): pydantic v2
   models in `flexcore.config.schema`, serialized to JSON with `schema_version`.
   Every field has a description (they render into docs). Validation errors must
   name the field path.
2. **Runtime construction options** (what a Pyomo/IDAES block takes when built):
   declared Pyomo `ConfigDict` entries with `description=` set. Populated *from*
   a validated pydantic model at the boundary when construction is config-driven.

Never persist a ConfigDict. Never pass a raw dict through more than one call
without validating it. No opaque nested JSON à la "FlowsNPC config files" —
if you can't document a key, it doesn't exist.

## 5. Commits & PRs

- One milestone per PR. PR title: `M07: FlexCosting — tariff-driven operating cost`.
- PR description includes: milestone link, Definition-of-Done checklist copied
  and ticked, "Deviations from spec" section (write "none" if none).
- CHANGELOG entry under "Unreleased" for anything user-visible.
- Keep commits reviewable; a reviewer should be able to read the PR in one sitting.

## 6. Import discipline (the split-later insurance)

Enforced by import-linter in CI (`.importlinter`):

- Layered contract: `flexcore` ← `flexops` ← {`flexparameterize`, `flexschedule`}.
  Lower layers never import higher ones; `flexparameterize` and `flexschedule`
  are mutually independent.
- Forbidden contract: `idaes` may only be imported inside `flexcore.compat`,
  **plus a tracked allowlist** (`flexcore/compat/tracked.py`) for IDAES/Pyomo
  debugging, diagnostic, and construction/evaluation tools that are impractical
  to re-export (architecture §2.1). The contract's `ignore_imports` is generated
  from that allowlist; adding an entry is a deliberate, reviewed act. The intent
  is to **track and limit** these imports, not to forbid them absolutely.
- Forbidden contract: the external `eeco` package may only be imported inside
  `flexops.costing` (ideally only `flexops/costing/tariff.py`). EECO is under
  active upstream rework; localizing its import point keeps that churn to one
  place, exactly like the `idaes`-in-`compat` rule.

If you need something from IDAES that `flexcore.compat.idaes` does not re-export,
first try adding it to the compat whitelist (with a comment naming the consumer).
Only if it is a debugging/diagnostic/construction tool that does not fit the
whitelist, add a tracked-allowlist entry in `flexcore/compat/tracked.py` with a
reason — never import `idaes` directly and untracked. Likewise, route `eeco`
calls through `flexops/costing/tariff.py`.

## 7. Testing (summary — full spec in plan/02_testing_and_ci.md)

- **Test-driven development is the required workflow**: write the milestone's
  tests first (they are the behavioral spec), watch them fail for the right
  reason, then implement. Tests written after the code do not satisfy any
  Definition of Done.
- **Run the full suite locally before every push**: `ruff check . &&
  black --check . && lint-imports && pytest -q` (all tiers). The pre-push hook
  installed by `pre-commit install --hook-type pre-push` runs exactly this;
  never bypass it on a branch intended for merge.
- Exactly one tier marker per test: `@pytest.mark.unit` (< 1 s, no solver),
  `@pytest.mark.component` (< 30 s, HiGHS/IPOPT only),
  `@pytest.mark.integration` (minutes, end-to-end). Collection fails otherwise.
  All three tiers run and must pass on every PR before merge (public repo, free
  CI); the tiers exist to keep the local TDD loop fast, not to defer tests.
- Solver-availability markers (`needs_highs`, `needs_ipopt`, ...) on anything
  that calls a specific solver.
- Every unit model gets a test class subclassing
  `flexops.testing.UnitModelTestHarness` — about 30 lines: a `configure()`
  method plus expected-DoF and expected-solution data.
- Numerical assertions use explicit tolerances (`pytest.approx(x, rel=1e-6)`),
  never exact float equality.
- Deterministic tests only: fixed seeds, no wall-clock dependence, no network.

## 8. Documentation (summary — full spec in plan/03_documentation.md)

- Docs build (`sphinx-build -W`) is a CI gate; a warning is a failure.
- Every public unit model has a reference page using the
  `.. flexops-unit-tables::` directive (auto-generates Variables / Constraints /
  Degrees-of-Freedom tables from the built model).
- How-to content goes in executable myst-nb notebooks under `examples/`;
  narrative design rationale goes in `docs/explanation/`.

## 9. Agent-specific rules

(Also in `CLAUDE.md`, which agents read automatically.)

- Build only the current milestone. No speculative abstractions "for later" —
  the later milestones are already written; trust them.
- If upstream (Pyomo/IDAES) behavior contradicts the milestone spec, prefer the
  spec's *intent*, implement the smallest working variant, and flag the deviation
  in the PR description.
- When a milestone says "copy this signature," copy it exactly — other
  milestones and docs reference these names verbatim.
