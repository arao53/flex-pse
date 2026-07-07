# M01 — Compat layer & upstream canary

**Effort:** 1–2 days · **Depends on:** M00 · **Parallelizable:** no

## Goal

Create the single point through which all IDAES (and non-stable Pyomo) symbols
enter the codebase, the tracked-allowlist for diagnostic/debugging tools that
cannot practically be whitelisted, the version-support declaration with an
actionable environment check, the project exception hierarchy, and the weekly CI
canary that tests us against `pyomo@main` + `idaes-pse@main`. After this
milestone, no file outside `flexcore/compat/` ever writes `import idaes` **except
through the reviewed `tracked.py` allowlist**, and upstream breakage is detected
by a scheduled job, not by users.

## Read first

- `plan/01_architecture.md` §2.1 (compat — the upstream-survival layer; the
  whitelist **and** the tracked-exceptions allowlist policy both live there) and
  §1 (DAG context)
- `plan/00_conventions.md` §3 (exception message style), §6 (import discipline —
  the tracked allowlist and generated `ignore_imports`)
- `plan/02_testing_and_ci.md` §3 (upstream-canary.yml spec), §1 (`upstream` marker)

## Files to create or modify

- `src/flexcore/compat/idaes.py` — the sole *unrestricted* `idaes` import point; whitelist re-exports
- `src/flexcore/compat/tracked.py` — the reviewed allowlist of tracked
  `idaes`/`pyomo` diagnostic/debugging/construction imports + `ignore_imports`
  generation (see below)
- `src/flexcore/compat/pyomo_ext.py` — `pyunits` re-export + model-statistics helpers
- `src/flexcore/compat/versions.py` — supported ranges + `check_environment()`
- `src/flexcore/compat/__init__.py` — re-export `check_environment` (nothing else)
- `src/flexcore/exceptions.py` — `FlexConfigError`, `FlexSolverError`, `FlexDataError`
- `.github/workflows/upstream-canary.yml` — weekly canary per 02_testing_and_ci.md §3
- `.importlinter` — extend `ignore_imports` for the concrete idaes submodules used;
  the tracked-allowlist edges are **generated** from `tracked.py` (below)
- `src/flexcore/tests/compat/test_idaes_whitelist.py`, `test_no_direct_idaes_imports.py`,
  `test_tracked.py`, `test_versions.py`; `src/flexcore/tests/test_exceptions.py`

## Specification

### flexcore/compat/idaes.py

Module docstring: "The only module in flex-pse allowed to import `idaes`.
Enforced by import-linter and by `test_no_direct_idaes_imports`." Re-export the
whitelist from 01_architecture §2.1, **each symbol with a one-line comment
naming its first consumer** (for consumers that arrive in a later milestone,
name the milestone). Define `__all__` listing every re-export — the whitelist
test iterates it. Re-export lines carry `# pragma: no cover`
(02_testing_and_ci.md §4).

```python
from idaes.core import (
    declare_process_block_class,  # flexops.core.time_block (M02)
    UnitModelBlockData,           # flexops.core.ops_block (M03)
    FlowsheetBlockData,           # flexops.core.plant_block (M09)
    PhysicalParameterBlock,       # flexops.properties.simple_aqueous (M03)
    StateBlock,                   # flexops.properties.simple_aqueous (M03)
    StateBlockData,               # flexops.properties.simple_aqueous (M03)
)
from idaes.core import FlowsheetCostingBlockData  # flexops.costing.flex_costing (M07)
from idaes.core.util.model_statistics import degrees_of_freedom  # flexops.testing.harness (M04); M03 tests
from idaes.core.util.initialization import propagate_state  # flexops.testing.harness (M04); Arcs (M09)
from idaes.core.util.scaling import calculate_scaling_factors  # flexops.testing.harness (M04)
# Units-consistency convenience, re-exported here per 01_architecture §2.1 even
# though it originates in pyomo.util.check_units:
from pyomo.util.check_units import assert_units_consistent  # flexops tests (M03+); harness (M04)
```

Adjust exact source module paths to the installed IDAES version (e.g.
`FlowsheetCostingBlockData` lives under `idaes.core.base.costing_base` in some
versions — import from the most public path that works, and record the path in
the comment). If a symbol's import path does not exist in the supported IDAES
range, fix the path here — that is this file's entire purpose. Update
`.importlinter`'s `ignore_imports` so each real `flexcore.compat.idaes ->
idaes.*` edge is allowed. The tracked-allowlist edges (from `tracked.py`) are
**generated**, not hand-written here — see `tracked.py` below.

### flexcore/compat/tracked.py

The whitelist in `idaes.py` is the preferred path, but IDAES and Pyomo ship
genuinely useful model-**debugging/diagnostic** tools
(`idaes.core.util.model_statistics`, `DiagnosticsToolbox`, the degeneracy
hunter, scaling reports) and some construction/evaluation-phase helpers that are
impractical to re-export one symbol at a time (architecture §2.1). For these,
direct `idaes`/`pyomo` use is allowed **only from an explicit, reviewed
allowlist** kept here. Adding an entry is a deliberate act; the goal is to
*track and limit*, not to leak.

```python
TrackedImport = namedtuple("TrackedImport", "module_glob symbol reason")

TRACKED_IMPORTS: tuple[TrackedImport, ...] = (
    # (module_glob, symbol, reason) — every entry is a reviewed exception.
    TrackedImport("idaes.core.util.model_statistics", "*",
                  "DoF/variable/constraint diagnostics used in tests + harness"),
    TrackedImport("idaes.core.util.model_diagnostics", "DiagnosticsToolbox",
                  "structural/numerical debugging during model bring-up"),
    TrackedImport("idaes.core.util.model_diagnostics", "DegeneracyHunter",
                  "degenerate-constraint hunting during MIP debugging"),
    TrackedImport("idaes.core.util.scaling", "*",
                  "scaling-factor reports during model bring-up"),
    # ... entries are the implementer's choice; keep each with a real reason.
)
```

- `module_glob` is a glob against the importing module's dotted path (e.g.
  `flexops.*.tests.*` may be the importer, or the tracked *target* module — pick
  one convention and document it; the entries above list the tracked **target**
  `idaes`/`pyomo` modules that off-compat code may reach).
- `generate_ignore_imports() -> list[str]` renders `TRACKED_IMPORTS` into the
  exact `ignore_imports` lines the import-linter `idaes` forbidden contract
  needs, so the contract's ignore list is **generated from the allowlist**, never
  hand-edited in two places. Provide a tiny entry point (a `__main__` or a
  console helper) that prints/writes them; the `.importlinter` idaes contract's
  `ignore_imports` is produced by it (document the regeneration command in the
  module docstring, mirroring the schema-export pattern in M03).
- `is_allowed(module_glob_or_target, symbol) -> bool` — used by the lint below.

### The off-allowlist diagnostic-import lint

Add a check (a test, run in the `unit` tier — implementer's choice on packaging
it as pytest vs. a ruff/flake plugin, but it must run in CI) that walks every
`*.py` under `src/` outside `flexcore/compat/`, finds `idaes`/`pyomo`
diagnostic/debugging imports (model_statistics, model_diagnostics,
DiagnosticsToolbox, DegeneracyHunter, scaling reports, etc.), and **reports**
each one that is not covered by `TRACKED_IMPORTS`. It reports (fails loudly with
the offending file + import + a pointer to add a tracked entry), never silently
allows — so usage stays visible and bounded (architecture §2.1). This is
distinct from the hard grep test below, which forbids *all* untracked `idaes`
imports; this lint specifically surfaces diagnostic-tool creep so it can be
reviewed onto the allowlist rather than banned.

### flexcore/compat/pyomo_ext.py

- `from pyomo.environ import units as pyunits` — the project-wide re-export
  point (consumers import `pyunits` from here, not from pyomo, when inside
  `flexcore`; `flexops`+ may use `pyomo.environ` directly since only `idaes` is
  restricted — but keep this as the canonical point for non-stable Pyomo APIs).
- Model-statistics helpers (implementer's choice, keep tiny):
  `n_variables(model) -> int`, `n_constraints(model) -> int`, implemented with
  plain `model.component_data_objects(...)` iteration — no private Pyomo imports
  yet. This module exists so that when we *do* need a private Pyomo utility,
  it has one home.

### flexcore/compat/versions.py

```python
SUPPORTED_PYOMO: tuple[str, str] = ("6.7", "7.0")   # [min inclusive, max exclusive)
SUPPORTED_IDAES: tuple[str, str] = ("2.4", "3.0")   # (implementer's choice: set to
                                                     # the ranges current at implementation time)

def check_environment() -> None:
    """Raise FlexConfigError if installed pyomo/idaes-pse fall outside supported ranges."""
```

Read installed versions via `importlib.metadata.version("pyomo")` /
`("idaes-pse")`; compare by parsed version tuples (use
`packaging.version.Version` — it ships with setuptools environments; add
`packaging` to core deps if not already pulled in). The error message must be
actionable (conventions §3): state installed vs supported and the pip command
to fix, e.g. `pip install "pyomo>=6.7,<7.0"`. Do **not** call
`check_environment()` at import time — it is an explicit call (solver facade
and CLI entry points call it later).

### flexcore/exceptions.py

```python
class FlexError(Exception):
    """Base class for all flex-pse errors."""   # base class: implementer's choice

class FlexConfigError(FlexError): ...
class FlexSolverError(FlexError): ...
class FlexDataError(FlexError): ...
```

Google-style docstrings on each stating when to raise it and reminding that
messages must say what was wrong *and what the user should do*.

### .github/workflows/upstream-canary.yml (02_testing_and_ci.md §3)

- `on:` weekly cron (e.g. `"0 6 * * 1"`) + `workflow_dispatch` with a boolean
  input `use_released` (default `false`) — when true, skip the git installs and
  test against released versions (this is how the DoD verifies the workflow
  mechanics without depending on upstream main being green). **Never triggered
  by pull_request** — the canary is not a PR gate.
- Steps: checkout; setup python 3.12; `pip install -e ".[dev,solvers]"`; unless
  `use_released`, `pip install git+https://github.com/Pyomo/pyomo git+https://github.com/IDAES/idaes-pse`;
  print `pip show pyomo idaes-pse` versions into the log; run
  `pytest -m "unit or component or upstream"`.
- On failure: `actions/github-script` step (`if: failure()`) that searches open
  issues labeled `upstream-breakage`; if one exists, comment on it with the run
  URL and failing pyomo/idaes versions; otherwise open a new issue titled
  "Upstream breakage: pyomo@main / idaes-pse@main" with the same info and a log
  excerpt (last ~50 lines). Apply the `upstream-breakage` label; create the
  label in the repo if absent.

No `upstream`-marked tests exist yet (they arrive when something pokes IDAES
internals directly); the marker is already registered from M00, and the `-m`
expression simply selects zero extra tests today.

## Pitfalls

1. **IDAES import paths move between versions.** If `from idaes.core import X`
   fails, hunt the symbol's current home (`idaes.core.base...`) and import from
   there — never work around it by importing at the point of use.
2. **Importing `idaes` in `pyomo_ext.py` or `versions.py`** — only
   `compat/idaes.py` may; the grep test and import-linter both catch it, but
   don't rely on that to remember.
3. **Calling `check_environment()` on import** of `flexcore` — this makes the
   canary useless (it would fail at collection with a version error instead of
   surfacing real breakage) and breaks users on slightly-newer versions.
4. **`actions/github-script` permissions** — the workflow needs
   `permissions: issues: write` at the top level or the issue step 403s.
5. **Forgetting `__all__`** in `idaes.py` — the whitelist test parametrizes over
   it; without it the test silently tests nothing.
6. **IDAES import cost** — `import idaes` takes seconds. Keep
   `flexcore/__init__.py` free of compat imports so `import flexcore` stays fast;
   only `flexcore.compat.idaes` pays the cost, on demand.
7. **Hand-editing the tracked `ignore_imports` in `.importlinter`.** Those edges
   are generated from `tracked.py`; editing them by hand desyncs the two. Add the
   entry to `TRACKED_IMPORTS` (with a reason) and regenerate.
8. **The diagnostic lint silently allowing instead of reporting.** Its whole
   point (architecture §2.1) is to *report* off-allowlist diagnostic imports
   loudly so they get reviewed onto the allowlist — never widen it to auto-pass.

## Tests

All `@pytest.mark.unit` unless noted; live under `src/flexcore/tests/compat/`
(add `__init__.py`):

- `test_idaes_whitelist.py::test_symbol_importable[<name>]` — parametrized over
  `flexcore.compat.idaes.__all__`: `getattr(module, name)` is not None.
- `test_idaes_whitelist.py::test_all_matches_module` — every public attribute
  re-exported by the module appears in `__all__` (no unlisted strays).
- `test_no_direct_idaes_imports.py::test_no_direct_idaes_imports` — grep-based
  backup to import-linter: walk every `*.py` under `src/` (locate via
  `pathlib.Path(flexcore.__file__).parents[1]`), regex
  `^\s*(import idaes|from idaes)` per line, assert offending imports live only in
  `flexcore/compat/` (`idaes.py`, plus anything on the `tracked.py` allowlist).
  Skip files it cannot read; assert the walk found a sane number of files (> 10)
  so a path bug can't vacuously pass.
- `test_tracked.py::test_tracked_allowlist_respected` — an import of a tracked
  diagnostic module from an on-allowlist location passes the off-allowlist lint;
  a synthetic off-allowlist diagnostic import (e.g. a fixture module directly
  importing `idaes.core.util.model_diagnostics.DegeneracyHunter` when it is not
  in `TRACKED_IMPORTS`) is **flagged** by the lint. Also assert
  `generate_ignore_imports()` produces a non-empty list and that every entry of
  `TRACKED_IMPORTS` carries a non-empty `reason`.
- `test_versions.py::test_check_environment_passes_in_dev_env` — current
  environment passes (dev envs install supported versions).
- `test_versions.py::test_check_environment_raises_actionable` — monkeypatch the
  installed-version lookup to return `"0.1"`; assert `FlexConfigError` whose
  message contains the installed version, the supported range, and `pip install`.
- `src/flexcore/tests/test_exceptions.py::test_hierarchy` — all three exceptions
  subclass `FlexError` and `Exception`; raising/catching works.

## Documentation tasks

- Module docstrings are the documentation for this milestone (no `docs/` tree
  exists yet — it starts in M02). Write them to reference-page quality:
  `compat/idaes.py` explains the survival strategy and how to extend the
  whitelist (conventions §6 wording); `versions.py` documents the range policy.
- CHANGELOG: "Unreleased — compat layer, tracked-import allowlist, exception
  hierarchy, upstream canary."
- `compat/tracked.py`'s module docstring documents the allowlist policy
  (architecture §2.1 / conventions §6) and the `ignore_imports` regeneration
  command.

## Definition of Done

- [ ] `flexcore.compat.idaes` imports cleanly; every `__all__` symbol resolves
- [ ] Every re-export has a first-consumer comment (milestone named if future)
- [ ] `flexcore/compat/tracked.py` holds the `TRACKED_IMPORTS` allowlist (each
      entry with a reason); `generate_ignore_imports()` produces the idaes
      contract's `ignore_imports` (not hand-edited)
- [ ] Off-allowlist diagnostic-import lint **reports** (fails on) an untracked
      `idaes`/`pyomo` diagnostic import; the allowlist-respected test is green
- [ ] `flexcore/exceptions.py` and `compat/versions.py` implemented; `check_environment()` passes locally
- [ ] `lint-imports` passes with the generated + extended ignore list; grep test passes
- [ ] `pytest -m unit` green including all new tests
- [ ] Canary workflow merged; **manual dispatch with `use_released=true` runs green**
- [ ] Canary is not wired to pull_request triggers (verified in YAML review)
- [ ] plus the generic DoD in CLAUDE.md
