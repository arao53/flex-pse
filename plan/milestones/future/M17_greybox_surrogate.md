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
4. **v1 implements derivatives via automatic differentiation (PyTorch
   `autograd`), not finite-difference perturbation — this is a reversal from
   the prototype and narrows the milestone's scope.** Decided during
   planning: autograd is exact (no step-size tuning, no truncation error —
   see Pitfall 3, which finite-difference would have reintroduced) and
   materially cheaper (one backward pass per Jacobian vs. `2H` forward
   evaluations). The cost is that it only works for a callable implemented
   in an autodiff-capable framework — it cannot differentiate through a
   truly opaque callable (a compiled binary, a bare REST call, a legacy
   Fortran simulator). **Finite-difference perturbation — the fallback that
   would handle those — is deliberately not implemented in v1**, following
   this repo's existing convention for a named-but-not-yet-built option
   (`NeuralNetworkSurrogate`'s `_validate` raises `NotImplementedError`
   naming the implemented alternative; see Specification §2/§3). This also
   narrows the Goal below: v1 does not actually cover "an arbitrary black-box
   callable that may not even be a neural network" — it covers a callable
   built in an autodiff framework, with the fully-opaque case deferred.

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
The prototype also used finite-difference perturbation throughout; **v1
implements autograd-based differentiation instead** (Update note item 4,
Specification §2/§3) — the prototype validated the CyIpopt wiring and the
lower-triangular-Hessian/block-diagonal mechanics, not the differentiation
strategy this milestone actually ships first.

## Goal

Give `flexops.surrogates` a class that can wrap a black-box Python callable
with no closed-form Pyomo expression (a foundation model, a network call to a
hosted model, any callable with no analytic derivative flex-pse could embed
directly) as a unit's registered relation, with derivatives obtained by
automatic differentiation rather than requiring the caller to hand-derive
them or embed the callable's internals as Pyomo expressions.

**v1 requires the wrapped callable to be implemented in an autodiff-capable
framework (PyTorch — see Specification §2 for why this is the one framework
v1 commits to).** It does not (yet) cover a *truly* opaque callable — a
compiled binary, a bare REST call, a legacy simulator with no Python-visible
computational graph — the way the word "black-box" might suggest; that case
needs finite-difference perturbation, which v1 deliberately does not
implement (Update note item 4). Concretely, v1 does cover exactly the
motivating case from the prototype (a PyTorch-based forecaster like
`theforecastingcompany/t0-alpha`), just not literally "any callable
whatsoever."

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
the callable as **opaque to Pyomo** — it is never translated into Pyomo
expressions, no matter how it computes its output — while still requiring it
be a PyTorch computational graph underneath, and pays for that with a
mandatory CyIpopt-only solve path. Keeping them as two separate
`SurrogateType` members avoids overloading one class with two structurally
different solve requirements.

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
   not a fixed requirement here. Autograd makes the batched case genuinely
   attractive (`torch.func.vmap` + `jacrev`/`hessian` over the batch
   dimension can produce the whole block-diagonal Jacobian/Hessian in one
   vectorized pass rather than `H` separate backward calls) — worth
   benchmarking against the simple per-`t` loop before committing, not
   assumed to be worth the added complexity here.
5. **Is PyTorch really the one framework v1 commits to, or should the
   `predict_callable` contract be framework-agnostic (torch, JAX, either)?**
   Recommendation: commit to PyTorch only for v1 — it is what
   `theforecastingcompany/t0-alpha` (`tfc-t0`) actually ships, supporting
   both means either an abstraction layer over two autodiff APIs (real
   complexity for a hypothetical second framework, against conventions'
   "no code for hypothetical requirements") or a second surrogate class.
   Revisit if a concrete JAX-based use case shows up.
6. **Does the real t0-alpha forward pass actually stay differentiable
   end-to-end through `future_covariates`?** Not yet verified against the
   real package (the prototype used a synthetic stand-in). A quantile/median
   computed via `torch.quantile` is differentiable, but worth an explicit
   spike against the real weights before implementation — see Pitfall 4 for
   why a silent break here is worse than a crash.

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
cost is `O(H)` backward passes for the Jacobian and `O(H)` for the Hessian
(one second-derivative probe per time index), not the prototype's `O(H²)` —
a much better story for a real neural forecaster where each call may be
expensive. A future milestone can generalize to cross-time coupling once a
concrete use case needs it; do not build it speculatively here (conventions:
no code for hypothetical requirements).

Note the scoping rationale above was written against finite-difference cost
(`O(H²)` calls for a dense Hessian). Autograd changes that calculus:
`torch.autograd.functional.jacobian`/`hessian` (or `vmap`+`jacrev`, Open
Question 4) can produce the *full, dense* cross-time-coupled Jacobian/Hessian
in roughly the same order of backward passes as the block-diagonal case,
since PyTorch does not care whether the underlying computation happens to be
block-diagonal. This weakens, but does not remove, the cost argument for
staying static in v1 — the scope restriction is kept here regardless,
because the harder capability is still a distinct piece of product design
(an autoregressive, context-carrying surrogate, not just "a bigger
Jacobian"), not because autograd could not compute the derivatives. Flagged
for reconsideration alongside Open Question 4, not decided here.

### 2. `SurrogateSpec.data` contract

```python
{
    "predict_callable": "mypackage.forecasters.t0alpha_predict",  # dotted path, resolved via importlib
    "predict_kwargs": {"model_name": "theforecastingcompany/t0-alpha"},  # opaque, passed to the callable once at build time (e.g. to load/cache weights)
    "input_variables": {"flow_in": "m^3/hr", "ambient_temperature": "degK"},
    "output_variables": {"fouling_rate": "1/hr"},  # exactly one entry, same constraint as MultilinearSurrogate
    "differentiation": "autograd",  # the only implemented value in v1; "finite_difference" is reserved (see below)
}
```

`predict_callable` resolves (via `importlib.import_module` +
`getattr`/dotted traversal) to a callable of signature
`f(inputs: dict[str, torch.Tensor], **predict_kwargs) -> torch.Tensor`,
called once per time index with that index's resolved, unit-converted input
values wrapped as scalar tensors with `requires_grad=True`. **PyTorch is the
one framework v1 commits to** (Open Question 5) — `predict_callable` must be
built from differentiable `torch` operations throughout, not merely "any
Python callable returning a float" as an earlier draft of this document
described.

`"differentiation": "finite_difference"` is accepted by the schema (so the
field exists and a future milestone can implement it without a schema
migration) but `_validate()` raises `NotImplementedError` naming
`"autograd"` as the implemented alternative — the same idiom
`NeuralNetworkSurrogate`/`ArimaSurrogate` already use for a reserved-but-
unbuilt option. Omitting the key defaults to `"autograd"`.

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
public `Surrogate` API) computes derivatives via `torch.autograd`, not the
prototype's finite differences, generalized to the block-diagonal,
per-time-index structure in §1:

- `evaluate_outputs`: for each time index `t`, call `predict_callable` on
  that index's input tensors (`requires_grad=True`) and record the output
  tensor (kept around, not just its detached float value — autograd needs
  the graph for the next two methods).
- `evaluate_jacobian_outputs`: `torch.autograd.grad(output_t, input_t,
  create_graph=True)` per time index gives `d(output_t)/d(input_t)` exactly;
  zero elsewhere (the block-diagonal structure), assembled into the same
  `scipy.sparse.coo_matrix` shape a finite-difference implementation would
  have produced — the external contract is unchanged by the differentiation
  strategy.
- `evaluate_hessian_outputs`: a second `torch.autograd.grad` call on the
  Jacobian tensor from the previous step (this is *why* `create_graph=True`
  above is mandatory — see Pitfall 4) gives the exact second derivative per
  time index. Still uses the weighted-sum-of-outputs contract
  (`set_output_constraint_multipliers`) — required here (unlike the
  prototype's simpler objective-only CyIpopt path) because each output feeds
  its own separate `target[t] == output[t]` constraint, not a single scalar
  objective — but reduces to one independent scalar second derivative per
  time index, not a dense matrix, and returned lower-triangular only
  (Pitfall 2, unchanged from the FD version).

`data["differentiation"] == "finite_difference"` is rejected at `_validate()`
with `NotImplementedError` (Specification §2) — there is no FD code path in
this module for v1.

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
  new `[greybox]` extra pinning `cyipopt` **and `torch`**, matching the
  existing pattern of `[parameterize]` for scikit-learn). Confirm whether
  `cyipopt` has reliable PyPI wheels for this repo's supported platforms or
  needs a conda-forge-only note in installation docs (it installed cleanly
  via `conda install -c conda-forge cyipopt` during prototyping; PyPI wheel
  coverage was not checked). `torch` is a heavy dependency (hundreds of MB
  even CPU-only) — pin the CPU-only build explicitly (see Pitfall 5) rather
  than letting the default PyPI wheel pull in CUDA.
- `.github/workflows/ci.yml` / a new `.github/actions/setup-cyipopt` —
  component/integration tests need cyipopt **and CPU-only torch** installed
  in CI, following `.github/actions/setup-ipopt`'s precedent; the arima
  integration PR's `[parameterize]`-in-CI change (adding a whole extra to
  the standard `pip install -e ".[dev,solvers,parameterize]"` CI line) is
  the pattern to mirror for `[greybox]` here.
- `src/flexops/tests/surrogates/test_grey_box.py` (new).
- `pyproject.toml` `[tool.pytest.ini_options]` markers — add
  `needs_cyipopt: skip if cyipopt/SolverFactory("cyipopt") is unavailable`
  and `needs_torch: skip if torch is not importable`, alongside the existing
  `needs_ipopt`/`needs_highs`/etc.
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
3. **(Deferred — applies only once finite-difference is implemented, not to
   v1.) Central finite-difference stencil correctness.** Recorded here so it
   is not rediscovered from scratch: a forward-biased stencil
   (`f(u+2h) - 2f(u+h) + f(u)` instead of `f(u+h) - 2f(u) + f(u-h)`) is a
   real mistake made and caught during prototyping — silently lower-order
   accurate, not a crash. Autograd sidesteps this whole bug class (Update
   note item 4), which is the main reason it is v1's priority.
4. **`torch.autograd.grad(..., create_graph=True)` is mandatory on the
   Jacobian computation, not optional.** Omitting it is the standard PyTorch
   gotcha: the returned gradient tensor is silently detached from the
   autograd graph, so the second `torch.autograd.grad` call in
   `evaluate_hessian_outputs` either errors ("does not require grad") or —
   worse — silently returns zeros if not guarded, which IPOPT would treat as
   "flat here," not fail loudly on. Test against a known nonzero analytic
   second derivative (see Tests), not just "the call succeeds."
5. **A non-differentiable operation inside `predict_callable` breaks
   silently, not loudly.** An in-place tensor op, a `.detach()`, a
   `.item()`/`float()` cast, boolean masking, or `torch.no_grad()` context
   anywhere in the wrapped model's forward pass can silently produce a
   `None` gradient or a disconnected graph — PyTorch does not raise by
   default. `_validate()` must run an eager smoke test (call
   `predict_callable` once on dummy input, take the gradient, assert it is
   not `None` and is finite) at *construction* time, mirroring this
   package's existing "fail at construction, not mid-solve" convention —
   without this check, a broken gradient surfaces as CyIpopt silently
   "converging" at a non-stationary point, not as an error (see Open
   Question 6 for whether the real t0-alpha needs this check applied to
   its actual weights before implementation starts, not just to test
   doubles).
6. **`torch` is a heavy dependency for an optional extra.** Even the
   CPU-only wheel is hundreds of MB; pin it explicitly to the CPU build
   (Files to create or modify) so `[greybox]` does not silently pull in a
   multi-GB CUDA distribution on a machine with no GPU.
7. **`cyipopt`/`torch` must stay out of the default install.** Follow the
   existing `[solvers]`/`[parameterize]` extras pattern (memory:
   tooling/optional imports get their own extra, never core deps);
   `flexops.surrogates` already only imports `pyomo.contrib.pynumero...`
   (and would only import `torch`) inside this one module, at the point of
   use, so a bare install without `[greybox]` still imports the rest of
   `flexops.surrogates` fine and only fails at `ExternalModelSurrogate`
   construction with a clear `ImportError`-wrapping message.
8. **PR #104 risk (Update note item 1).** If `Surrogate.build()`'s contract
   changes mid-implementation, every code sample in this document needs a
   mechanical but non-trivial translation. Check `#104`'s status before
   writing code, not just before merging.
9. **Never delete Pyomo components.** A grey-box relation swapped again
   later must `deactivate()` the old `ExternalGreyBoxBlock`, exactly like
   every other surrogate swap — confirm `ExternalGreyBoxBlock.deactivate()`
   actually removes it from `PyomoNLPWithGreyBoxBlocks`' view (expected,
   since it is a standard Pyomo `Block`, but not yet verified against this
   repo's pinned Pyomo version).

## Tests

`src/flexops/tests/surrogates/test_grey_box.py`:

- `unit`, `needs_torch`, no solver:
  - `test_validate_resolves_predict_callable_eagerly` — bad dotted path
    raises `FlexConfigError` at construction.
  - `test_validate_rejects_multiple_output_variables` (mirrors
    `MultilinearSurrogate`'s equivalent test).
  - `test_validate_rejects_finite_difference_differentiation_mode` —
    `data["differentiation"] = "finite_difference"` raises
    `NotImplementedError` naming `"autograd"`, mirroring
    `NeuralNetworkSurrogate`'s existing not-yet-implemented test pattern.
  - `test_validate_raises_on_non_differentiable_callable` — a deliberately
    broken `predict_callable` (e.g. one that calls `.detach()` or
    `.item()` internally) raises `FlexConfigError` at construction, not a
    silent `None` gradient at solve time (Pitfall 5).
  - `test_jacobian_matches_analytic_derivative_on_known_function` — wrap a
    small `torch` function with a known closed-form derivative (e.g.
    `f(x) = x**3`), assert the autograd Jacobian matches **exactly** (to
    floating-point precision, not "within tolerance" — unlike
    finite-difference, autograd has no reason to be approximate here).
  - `test_hessian_matches_analytic_second_derivative_on_known_function` —
    same idea for the second derivative; this is what guards Pitfall 4
    (the `create_graph=True` gotcha) — an implementation that forgets it
    would fail this test with a `None`/zero-valued Hessian, not a subtle
    accuracy gap.
  - `test_hessian_is_lower_triangular_only` — guards Pitfall 2 directly.
  - `test_block_diagonal_structure_ignores_cross_time_perturbation` — perturb
    input at time `s`, assert output Jacobian entries at `t != s` are
    exactly zero, confirming the `O(H)` scoping in Specification §1
    actually holds in the implementation, not just in the design.
- `component`, `needs_cyipopt`, `needs_torch`:
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
- [ ] Jacobian and Hessian autograd implementations are verified against a
      known analytic function to floating-point precision (not just "the
      solve converges") — an implementation that forgets
      `create_graph=True` (Pitfall 4) fails this immediately rather than
      passing with degraded accuracy the way an uncaught finite-difference
      bug would have.
- [ ] `data["differentiation"] = "finite_difference"` raises
      `NotImplementedError` naming `"autograd"` — a reserved, explicitly
      not-yet-built option, not a silently accepted no-op.
- [ ] `_validate()` runs an eager differentiability smoke test on
      `predict_callable` and raises `FlexConfigError` (not a downstream
      `None`-gradient failure) when it is not actually autodiff-able
      (Pitfall 5).
- [ ] `evaluate_hessian_outputs` returns lower-triangular only; a dedicated
      test guards this.
- [ ] The block-diagonal (`O(H)`, not `O(H²)`) structure from Specification
      §1 is both implemented and tested, not just asserted in this document.
- [ ] Attempting `flexcore.solvers.get_solver` on a model containing a
      grey-box surrogate raises a clear, actionable `FlexConfigError`.
- [ ] `cyipopt` and `torch` (CPU-only) are both in the optional `[greybox]`
      extra, never core or `[solvers]` dependencies; a bare install still
      imports `flexops.surrogates` cleanly.
- [ ] Every cyipopt/torch-dependent test uses a `needs_cyipopt`/`needs_torch`
      marker and only skips (never fails) when either is unavailable.
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
