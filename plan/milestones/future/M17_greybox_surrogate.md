# M17 — CyIpopt grey-box surrogate

**Effort:** 4–5 days · **Depends on:** M09 (surrogate registry), loosely M11
(regressor protocol, for the fit-provenance shape) · **Parallelizable:** with
M12–M16

## Update note (pre-implementation scoping — read before the rest)

Like M11b, this milestone is scoped by a research/prototyping pass rather
than drafted ahead of time (a working, hand-verified prototype exists outside
this repo — see "Prototype" below). Three things discovered during that pass
change the shape of this document and must be re-checked before
implementation starts, not assumed:

1. **PR #104 (`avdudchenko:update-surrogate-structure`, open, not yet
   merged) changes `Surrogate.build()`'s return contract** from `body` (a
   single callable) to `(block, body)`, and introduces a `CoefficientRegistry`
   so a surrogate's fitted coefficients become first-class `Var`s that
   `flexparameterize` can unfix/refit in place. **This milestone must not
   start implementation until #104 either merges or is explicitly
   deprioritized** — every `Surrogate.build()` signature and example below
   assumes the *current* (pre-#104) contract (`build` returns `body` only,
   attaches auxiliary components to `unit` itself, and `swap_relation` finds
   them via a before/after `component_map()` diff). If #104 lands first,
   translate directly: the `ExternalGreyBoxBlock` this milestone attaches
   becomes the returned `block`, and its `CoefficientRegistry` stays empty (a
   grey box's parameters are opaque to Pyomo — see Open Question 1) or is
   deliberately left unpopulated with a documented reason.
2. **`flexcore.solvers.facade.get_solver`'s priority list (`gurobi, scip,
   highs, cbc, ipopt`) has no entry that can solve a model containing an
   `ExternalGreyBoxBlock`.** Every one of those is an ASL/NL-file solver
   interface; none can call back into a Python `evaluate_outputs` method.
   Only `pyo.SolverFactory("cyipopt")`
   (`pyomo.contrib.pynumero.algorithms.solvers.cyipopt_solver.CyIpoptSolver`,
   via `PyomoNLPWithGreyBoxBlocks`) can. This is the central integration risk
   of the milestone — see Specification §3.
3. **`m11b_estimation` (branch, unmerged M11b work) is not a blocker but is
   the precedent to follow** for the solver-bypass question above:
   `flexparameterize.estimation.estimate_parameters` passes `solver="ipopt"`
   straight to `parmest`, deliberately **not** resolved through
   `flexcore.solvers.get_solver`, because the facade's classification logic
   has nothing useful to add for a capability that only ever needs one
   specific NLP solver. This milestone follows the same pattern for CyIpopt
   rather than teaching the facade a new problem class (see Open Question 2).

## Prototype (informal, not part of this repo)

A standalone script (not checked in) verified the core mechanics end to end:
an `ExternalGreyBoxModel` wrapping a black-box forecaster
(`theforecastingcompany/t0-alpha`, or a synthetic stand-in with an identical
`.predict(context, horizon, quantiles, future_covariates)` interface when the
real model/weights aren't available), with `evaluate_jacobian_outputs`/
`evaluate_hessian_outputs` computed by central finite-difference perturbation
of the forecaster itself (never differentiating through its internals). Two
solve strategies were confirmed to converge to the identical optimum:

- A hand-rolled outer trust-region loop (build a local quadratic model from
  the grey box's FD Jacobian/Hessian at the current iterate, solve a small
  Pyomo+ipopt QP subproblem, accept/reject/resize, repeat).
- `SolverFactory("cyipopt").solve(model)` on a model where the grey box
  declares itself as the Pyomo objective directly (`has_objective`/
  `evaluate_objective`/`evaluate_grad_objective`/`evaluate_hessian_objective`)
  — IPOPT's own Newton iterations become the outer loop, at roughly 2x the
  black-box call count of the manual loop for this toy problem, cut back down
  with `hessian_approximation="limited-memory"` (quasi-Newton from Jacobians
  alone, dropping the FD Hessian).

**The prototype is a materially harder problem than this milestone's v1** —
see Specification §1 for why, and why v1 deliberately does not attempt it.

## Goal

Give `flexops.surrogates` a class that can wrap an arbitrary black-box
Python callable (a proprietary/legacy simulator, a network call to a hosted
model, a foundation model with no analytic form) as a unit's registered
relation, with derivatives obtained by finite-difference perturbation rather
than requiring the callable to be autodiff-able or even differentiable in
closed form.

**This is a different, harder capability than PLAN.md §4.2's "External
forecaster interface" backlog item — do not conflate the two.** That item is
a "forecast, then fix parameters" adapter: the forecaster runs once, entirely
*outside* the optimization, and its output becomes a fixed `Param` before the
solve starts — no feedback loop, no derivatives, no grey box, no CyIpopt
dependency at all, and (as scoped there) considerably cheaper to build. This
milestone is for the case where the forecaster's output must live *inside*
the NLP's feedback loop — the unit's own decision variables influence what
gets forecast, or the callable is the only available model of a nonlinear
physical relation with no closed form and needs to be optimized *against*,
not just consulted beforehand. If the "forecast, then fix" pattern is all a
given use case needs, implement that directly against the existing
`update_parameters`/`.fix()` pattern (`_attach_surrogate` and
`commit_estimate` are both precedent) — it needs none of this milestone's
machinery. Revisit whether §4.2's backlog item is still worth doing
separately once this milestone's scope is settled; do not assume this
milestone supersedes it.

This is also **not** a replacement for `NeuralNetworkSurrogate` (reserved for a
literal trained network's weights embedded as a white-box Pyomo
formulation — e.g. an OMLT big-M ReLU or ICNN encoding, fully
ASL/ipopt-solvable, no grey box needed). This milestone's surrogate treats
the callable as **opaque** — it may not even be a neural network — and pays
for that generality with a mandatory CyIpopt-only solve path. Keeping them
as two separate `SurrogateType` members avoids overloading one class with
two structurally different solve requirements.

## Open questions (resolve before/at milestone kickoff, not assumed here)

1. **What does a grey-box surrogate's "coefficients" mean for
   `flexparameterize`?** A `MultilinearSurrogate`'s coefficients are fitted
   floats a `Regressor` produces and `to_surrogate_spec()` serializes. A
   grey-box surrogate's "parameters" are the wrapped model's own internal
   weights (e.g. t0-alpha's transformer weights) — flex-pse has no way to
   regress those, and should not pretend to. Recommendation: this milestone's
   `SurrogateSpec.data` names a *reference* to a pre-fitted external model
   (see §2), never inline weights, and there is no `Regressor` counterpart —
   it is a "bring your own fitted model" surrogate, not a "fit it here" one,
   the same way `constant_intensity` and `multilinear` differ in whether
   `flexparameterize.regression` produces them.
2. **Does `flexcore.solvers.facade` need to know about grey-box models at
   all?** Recommendation (following the M11b precedent in the Update note):
   no. Add a narrow, explicit check instead —
   `flexops.core.build.build_model` (or `swap_relation` itself) raises a
   clear `FlexConfigError` at *build* time if a grey-box surrogate is
   attached, pointing the caller at `SolverFactory("cyipopt")` directly,
   rather than teaching `get_solver`/`classify` a new problem class for a
   capability that only ever has one correct solver. Revisit only if a
   second CyIpopt-only capability appears later.
3. **How is the wrapped callable referenced from a JSON-serializable
   `SurrogateSpec.data`?** Proposed: a dotted import path string
   (`data["predict_callable"] = "mypackage.forecasters.t0alpha_predict"`),
   resolved via `importlib` at `build()` time, plus an opaque
   `data["predict_kwargs"]` dict passed to it — mirrors nothing else in this
   repo, because nothing else in this repo executes arbitrary imported code
   from config data. **This is a real, new trust boundary** (see Pitfall 1)
   and needs an explicit decision, not a silent default, on whether
   `SurrogateSpec` configs are ever loaded from a source flex-pse does not
   already trust as much as its own code.
4. **Batching.** The real t0-alpha API is batched (`context: [B, T]`). Whether
   `evaluate_outputs` calls the wrapped model once per time index or once for
   the whole horizon (v1's block-diagonal structure, §1, makes either
   equally correct — only the call count differs) is an implementation
   choice to make against whatever the referenced callable actually supports,
   not a fixed requirement here.

## Specification

### 1. Scope: a *static*, per-time-index relation — no cross-time feedback

The prototype's forecaster took the unit's own planned decision variables
across the **whole horizon** as covariates, producing outputs coupled across
every time index (a dense Jacobian/Hessian). That is a materially larger
capability — an autoregressive, context-window-carrying surrogate closer to
what a `flexschedule` rolling-horizon driver would eventually want — and is
explicitly **out of scope for v1**.

v1's `ExternalModelSurrogate` wraps a callable of the shape
`f(inputs: dict[str, float]) -> float`, called **independently at each time
index** on that index's own resolved input variables — structurally the same
shape as `MultilinearSurrogate.build()`'s `body(t)`, just backed by an opaque
callable instead of a closed-form expression. Because output at time `t`
depends only on inputs at time `t`, the grey box's Jacobian and Hessian
(across the whole `ExternalGreyBoxBlock`, sized for the full time horizon so
only one block is attached per relation) are **block-diagonal**: perturbation
cost is `O(H)` calls for the Jacobian and `O(H)` for the Hessian (one
second-derivative probe per time index), not the prototype's `O(H²)` — a
much better story for a real neural forecaster where each call may be
expensive. A future milestone can generalize to cross-time coupling once a
concrete use case needs it; do not build it speculatively here (conventions:
no code for hypothetical requirements).

### 2. `SurrogateSpec.data` contract

```python
{
    "predict_callable": "mypackage.forecasters.t0alpha_predict",  # dotted path, resolved via importlib
    "predict_kwargs": {"model_name": "theforecastingcompany/t0-alpha"},  # opaque, passed to the callable once at build time (e.g. to load/cache weights)
    "input_variables": {"flow_in": "m^3/hr", "ambient_temperature": "degK"},
    "output_variables": {"fouling_rate": "1/hr"},  # exactly one entry, same constraint as MultilinearSurrogate
    "fd_eps": 0.05,       # optional, default TBD by implementer benchmarking
    "hessian": "finite_difference",  # or "none" -- see Pitfall 3
}
```

`predict_callable` resolves (via `importlib.import_module` +
`getattr`/dotted traversal) to a callable of signature
`f(inputs: dict[str, float], **predict_kwargs) -> float`, called once per
time index with that index's resolved, unit-converted input values.

### 3. New class: `flexops/surrogates/grey_box.py`

```python
class ExternalModelSurrogate(Surrogate):
    surrogate_type: ClassVar[SurrogateType] = SurrogateType.EXTERNAL_MODEL

    def _validate(self) -> None: ...   # resolve predict_callable eagerly (fail at construction,
                                        # not mid-solve, per the package's own stated convention);
                                        # validate input/output_variables like MultilinearSurrogate

    @property
    def input_variables(self) -> dict[str, str]: ...
    @property
    def output_variables(self) -> dict[str, str]: ...

    def build(self, unit, target):
        # 1. resolve each input Var via unit.resolve_variable (as MultilinearSurrogate does)
        # 2. attach one ExternalGreyBoxBlock, sized for the unit's full time_index, to `unit`
        #    (swap_relation's existing before/after component_map() diff finds and tracks it —
        #    no change needed there under the pre-#104 contract)
        # 3. return body(t) -> block.outputs[f"y_{t}"], unit-converted like every other surrogate
```

The `ExternalGreyBoxModel` adapter (private to this module, not part of the
public `Surrogate` API) does the FD Jacobian/Hessian exactly as the
prototype did, generalized to the block-diagonal, per-time-index structure
in §1: `evaluate_jacobian_outputs` perturbs only input `t` to get
`d(output_t)/d(input_t)`, zero elsewhere; `evaluate_hessian_outputs` uses the
weighted-sum-of-outputs contract (`set_output_constraint_multipliers`) —
required here (unlike the prototype's simpler objective-only CyIpopt path)
because each output feeds its own separate `target[t] == output[t]`
constraint, not a single scalar objective — but reduces to one independent
scalar second derivative per time index, not a dense matrix.

### 4. Build-time solver guard (Open Question 2)

`build_model`/`swap_relation` (exact location TBD at implementation) raises
`FlexConfigError` if a model containing an `EXTERNAL_MODEL` surrogate is
handed to `flexcore.solvers.get_solver`, naming `SolverFactory("cyipopt")` as
the required alternative — mirroring `M11b`'s explicit-`k_aug`-rejection
pattern (clear message at the point of misuse, not a deep failure inside
someone else's library).

## Files to create or modify

- `src/flexops/surrogates/grey_box.py` — `ExternalModelSurrogate` (new).
- `src/flexops/surrogates/surrogates.py` — register
  `SurrogateType.EXTERNAL_MODEL: ExternalModelSurrogate` in `SURROGATES`.
- `src/flexcore/config/schema.py` — add `EXTERNAL_MODEL = "external_model"`
  to `SurrogateType`.
- `src/flexcore/solvers/facade.py` or `src/flexops/core/build.py` — the
  build-time guard from Specification §4 (exact location depends on where
  `get_solver` is actually invoked relative to model construction — check
  current call sites before choosing).
- `pyproject.toml` — new optional extra (do **not** add to core deps or to
  `[solvers]`, which is documented as HiGHS-only self-contained wheels; a
  new `[greybox]` extra pinning `cyipopt`, matching the existing pattern of
  `[parameterize]` for scikit-learn). Confirm whether `cyipopt` has
  reliable PyPI wheels for this repo's supported platforms or needs a
  conda-forge-only note in installation docs (it installed cleanly via
  `conda install -c conda-forge cyipopt` during prototyping; PyPI wheel
  coverage was not checked).
- `.github/workflows/ci.yml` / a new `.github/actions/setup-cyipopt` —
  component/integration tests need cyipopt installed in CI, following
  `.github/actions/setup-ipopt`'s precedent.
- `src/flexops/tests/surrogates/test_grey_box.py` (new).
- `pyproject.toml` `[tool.pytest.ini_options]` markers — add
  `needs_cyipopt: skip if cyipopt/SolverFactory("cyipopt") is unavailable`,
  alongside the existing `needs_ipopt`/`needs_highs`/etc.
- Docs: `docs/reference/flexops/surrogates` (new class), 
  `docs/explanation/config_schema.md` (extend the surrogate-type list),
  `docs/how_to/parameterize_from_data.md` or a new how-to specifically for
  "wrapping an external model" (unlike every other surrogate, this one is
  never produced by a `Regressor` — see Open Question 1 — so the existing
  fit-then-attach narrative doesn't fit; needs its own short section).
- `CHANGELOG.md` — Unreleased entry; note this is **distinct from** the
  "external forecaster interface" item in PLAN.md §4.2 (see Goal) — do not
  describe this as closing that backlog item.

## Pitfalls

1. **Arbitrary code execution from config data.** `predict_callable` is a
   dotted import path resolved and called from `SurrogateSpec.data` —
   config content flex-pse has, until now, only ever used to select among a
   closed, known registry (`SURROGATES`), never to import and execute
   arbitrary named code. State this loudly in the public docstring and in
   `docs/explanation/config_schema.md`: a `SurrogateSpec` with
   `surrogate_type="external_model"` must be trusted exactly as much as
   code, never loaded from an untrusted or user-uploaded config. Do not
   silently paper over this with a sandboxing scheme this milestone does not
   actually build.
2. **`ExternalGreyBoxModel.evaluate_hessian_outputs` must return the
   lower-triangular portion only** (verified directly: `cyipopt_solver.py`'s
   `PyomoNLPWithGreyBoxBlocks` raises `ValueError` on a full symmetric
   matrix) — easy to get wrong once, since the failure only surfaces at
   solve time, not at class-definition time.
3. **Central finite-difference stencil correctness.** A forward-biased
   stencil (`f(u+2h) - 2f(u+h) + f(u)` instead of
   `f(u+h) - 2f(u) + f(u-h)`) is a real mistake made and caught during
   prototyping — silently lower-order accurate, not a crash, so it will not
   surface as a test failure unless a test specifically checks Hessian
   accuracy against a known analytic function (see Tests).
4. **Every finite-difference call re-invokes the wrapped external model.**
   For an expensive callable (a real neural forecaster, a network call), the
   default full FD Hessian is the dominant per-solve cost. `data["hessian"]
   = "none"` should skip `evaluate_hessian_outputs` entirely and require
   `hessian_approximation="limited-memory"` on the CyIpopt solve (confirmed
   in the prototype: same converged optimum, ~2.7x fewer callable
   invocations on the toy problem) — document this as the recommended
   setting for any callable more expensive than the synthetic stand-in.
5. **`cyipopt` must stay out of the default install.** Follow the existing
   `[solvers]`/`[parameterize]` extras pattern (memory: tooling/optional
   imports get their own extra, never core deps); `flexops.surrogates`
   already only imports `pyomo.contrib.pynumero...` inside this one module,
   at the point of use, so a bare install without `[greybox]` still imports
   the rest of `flexops.surrogates` fine and only fails at
   `ExternalModelSurrogate` construction with a clear `ImportError`-wrapping
   message.
6. **PR #104 risk (Update note item 1).** If `Surrogate.build()`'s contract
   changes mid-implementation, every code sample in this document needs a
   mechanical but non-trivial translation. Check `#104`'s status before
   writing code, not just before merging.
7. **Never delete Pyomo components.** A grey-box relation swapped again
   later must `deactivate()` the old `ExternalGreyBoxBlock`, exactly like
   every other surrogate swap — confirm `ExternalGreyBoxBlock.deactivate()`
   actually removes it from `PyomoNLPWithGreyBoxBlocks`' view (expected,
   since it is a standard Pyomo `Block`, but not yet verified against this
   repo's pinned Pyomo version).

## Tests

`src/flexops/tests/surrogates/test_grey_box.py`:

- `unit`, no solver:
  - `test_validate_resolves_predict_callable_eagerly` — bad dotted path
    raises `FlexConfigError` at construction.
  - `test_validate_rejects_multiple_output_variables` (mirrors
    `MultilinearSurrogate`'s equivalent test).
  - `test_jacobian_matches_analytic_derivative_on_known_function` — wrap a
    plain Python function with a known closed-form derivative (e.g.
    `f(x) = x**3`), assert the FD Jacobian is within tolerance — this is
    what would have caught Pitfall 3 immediately.
  - `test_hessian_matches_analytic_second_derivative_on_known_function` —
    same idea, catches the exact stencil bug found during prototyping.
  - `test_hessian_is_lower_triangular_only` — guards Pitfall 2 directly.
  - `test_block_diagonal_structure_ignores_cross_time_perturbation` — perturb
    input at time `s`, assert output Jacobian entries at `t != s` are
    (numerically) zero, confirming the `O(H)` scoping in Specification §1
    actually holds in the implementation, not just in the design.
- `component`, `needs_cyipopt`:
  - `test_swap_relation_attaches_grey_box_and_solves` — small one-unit model
    (mirror `flexparameterize/tests/helpers.py::build_plant()`'s shape),
    swap to an `external_model` surrogate wrapping a simple known function,
    solve with `SolverFactory("cyipopt")`, assert the result matches a
    hand-computed optimum.
  - `test_get_solver_raises_clear_error_for_grey_box_model` — the
    Specification §4 guard.
  - `test_resolves_when_cyipopt_unavailable_skips_not_fails` — mirror the
    `needs_ipopt` skip pattern.

## Documentation tasks

- `docs/reference/flexops/surrogates`: new `ExternalModelSurrogate` section.
- `docs/explanation/config_schema.md`: extend the `SurrogateType` list;
  explicitly flag `external_model` as the one surrogate type that executes
  imported code from config data (Pitfall 1) and has no `Regressor`
  counterpart (Open Question 1).
- A new how-to (or new section) walking through wrapping an external
  callable — no `Regressor`/`fit()` step, since the model is assumed
  pre-fitted elsewhere; show the `SolverFactory("cyipopt")` requirement
  explicitly, since it is the one surrogate type where the reader cannot
  just call `pyo.SolverFactory("ipopt").solve(m)` as everywhere else in the
  docs.
- `CHANGELOG.md` — Unreleased entry; note this is a **CyIpopt-only** solve
  path, unlike every other surrogate, and is distinct from PLAN.md §4.2's
  "external forecaster interface" backlog item (see Goal) — do not describe
  this as closing that item.

## Definition of Done

- [ ] PR #104's fate (merged/closed/deprioritized) is confirmed before
      implementation starts; this document's `build()` contract examples
      are updated to match whichever is current.
- [ ] `ExternalModelSurrogate` exists, registered in `SURROGATES` under
      `SurrogateType.EXTERNAL_MODEL`, following the exact
      validate-eagerly/one-output-variable conventions every other
      surrogate class follows.
- [ ] Jacobian and Hessian FD implementations are verified against a known
      analytic function (not just "the solve converges") — this is the
      direct fix for the stencil bug found during prototyping.
- [ ] `evaluate_hessian_outputs` returns lower-triangular only; a dedicated
      test guards this.
- [ ] The block-diagonal (`O(H)`, not `O(H²)`) structure from Specification
      §1 is both implemented and tested, not just asserted in this document.
- [ ] Attempting `flexcore.solvers.get_solver` on a model containing a
      grey-box surrogate raises a clear, actionable `FlexConfigError`.
- [ ] `cyipopt` is an optional extra (`[greybox]`), never a core or
      `[solvers]` dependency; a bare install still imports
      `flexops.surrogates` cleanly.
- [ ] Every cyipopt-dependent test uses a `needs_cyipopt` marker and only
      skips (never fails) when cyipopt is unavailable.
- [ ] The arbitrary-code-execution trust boundary (Pitfall 1) is documented
      prominently in both the class docstring and
      `docs/explanation/config_schema.md`, with an explicit decision
      recorded on whether/how it is scoped (e.g. "SurrogateSpec configs are
      always as trusted as code" is an acceptable decision, but must be a
      stated one, not a silent gap).
- [ ] Reference/how-to/explanation docs updated; `sphinx-build -W` passes;
      CHANGELOG updated, without describing this as closing PLAN.md §4.2's
      "external forecaster interface" backlog item (see Goal — they are
      distinct capabilities).
- [ ] plus the generic DoD in `CLAUDE.md`
