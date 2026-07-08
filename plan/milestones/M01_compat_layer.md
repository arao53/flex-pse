# M01 — Exception hierarchy & dependency pinning

**Effort:** 0.5 day · **Depends on:** M00 · **Parallelizable:** no

## Goal

Establish the project exception hierarchy that every later milestone raises, and
pin the upstream `idaes-pse`/`pyomo` versions the project is tested against. There
is **no compat/isolation layer** — see `plan/01_architecture.md` §2.1 (decision
R12): a re-export whitelist guards only cheap import-path drift and does nothing
for semantic drift, so we follow a standard dependency-pinning cycle instead.
`idaes.*` and `pyomo.*` are imported directly at point of use everywhere.

## Read first

- `plan/01_architecture.md` §2.1 (pin, don't isolate — decision R12) and §1 (DAG context)
- `plan/00_conventions.md` §3 (exception message style), §6 (import discipline —
  only the layered contract; no dependency-isolation contracts)

## Files to create or modify

- `src/flexcore/exceptions.py` — `FlexError`, `FlexConfigError`, `FlexSolverError`, `FlexDataError`
- `pyproject.toml` — pin `idaes-pse`, `pyomo`, and `eeco` to exact tested
  versions (`==`), defaulting to the latest release at implementation time; add a
  comment naming the manual-bump policy
- `src/flexcore/tests/test_exceptions.py`

## Specification

### flexcore/exceptions.py

```python
class FlexError(Exception):
    """Base class for all flex-pse errors."""   # base class: implementer's choice

class FlexConfigError(FlexError): ...
class FlexSolverError(FlexError): ...
class FlexDataError(FlexError): ...
```

Google-style docstrings on each stating when to raise it and reminding that
messages must say what was wrong *and what the user should do* (conventions §3).

### pyproject.toml — dependency pinning

Pin the two upstream packages the project builds on to exact versions:

```toml
dependencies = [
    "idaes-pse==<latest-release>",   # bumped manually; see policy below
    "pyomo==<latest-release>",       # bumped manually; see policy below
    "eeco==<latest-release>",        # bumped manually; see policy below
    "pandas",
    "pydantic>=2",
]
```

- Set `<latest-release>` to the newest published release of each pinned package
  (`idaes-pse`, `pyomo`, `eeco`) at implementation time. Verify the full test
  suite passes against those exact versions before committing the pins.
- Add a comment (or a short `# Dependency policy` note near the pins) stating the
  policy: **maintainers bump these pins manually, roughly quarterly, only after
  the full suite passes against the newer versions.** No automated upstream
  canary in v0; if manual bumps become hectic, revisit automation then.

## Pitfalls

1. **Do not build a compat package.** Decision R12 removed it — no
   `flexcore/compat/`, no re-export whitelist, no `check_environment()`, no
   `upstream-canary.yml`. Import `idaes`/`pyomo` directly.
2. **Loose pins.** Use `==`, not `>=`. The point is a single, known-good, tested
   version pair that only moves by a deliberate maintainer action.

## Tests

All `@pytest.mark.unit`; live under `src/flexcore/tests/`:

- `test_exceptions.py::test_hierarchy` — all three concrete exceptions subclass
  `FlexError` and `Exception`; raising and catching each works.

## Documentation tasks

- Module docstring on `exceptions.py` to reference-page quality (no `docs/` tree
  exists yet — it starts in M02).
- CHANGELOG: "Unreleased — exception hierarchy; pinned idaes-pse/pyomo versions."

## Definition of Done

- [ ] `flexcore/exceptions.py` implemented; `FlexConfigError`/`FlexSolverError`/
      `FlexDataError` all subclass `FlexError`
- [ ] `pyproject.toml` pins `idaes-pse`, `pyomo`, and `eeco` to exact
      latest-release versions, with the manual-bump policy documented
- [ ] `pytest -m unit` green including `test_exceptions.py`
- [ ] `ruff`, `black --check`, and `lint-imports` pass
- [ ] plus the generic DoD in CLAUDE.md
