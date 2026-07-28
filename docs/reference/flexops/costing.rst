flexops.costing
===============

.. currentmodule:: flexops.costing

EECO integration
----------------

flex-pse does **not** build its own tariff/cost engine. Tariffs, demand
charges, tiered/fixed charges, and both the optimization-time and
post-optimization cost computations come from the external **EECO** package
(``eeco`` on PyPI), a core runtime dependency (architecture §2.4, decisions
R4/R9). ``flexops.costing.opex`` is the thin flex-pse interface around it —
and, by convention (decision R12), the **only** module in the codebase that
imports ``eeco``, so there is one file to fix when EECO's API moves.

EECO owns all cost math; these wrappers are glue: they marshal inputs, rename
EECO's outputs to stable flex-pse names, and translate EECO/pandas errors into
the flex-pse exception hierarchy. A flex-pse tariff object is simply an EECO
``rate_data`` ``DataFrame`` (EECO 0.2.1 has no tariff loader of its own; its
cost functions consume that frame directly).

Loaders and CSV conversion
---------------------------

.. autosummary::
   :toctree: generated
   :nosignatures:

   load_tariff
   load_dr_program
   tariff_csv_to_dict
   tariff_currency_units

A tariff may be authored as a JSON records structure or imported from an EECO
``rate_data`` CSV. The JSON form is a ``tariff_data`` records list, e.g. (an
excerpt of the demo ``flexdemo-b20`` time-of-use tariff)::

    {
      "tariff_data": [
        {"utility": "electric", "type": "energy", "name": "peak",
         "month_start": 6, "month_end": 9, "weekday_start": 0, "weekday_end": 4,
         "hour_start": 16, "hour_end": 21, "basic_charge_limit (metric)": 0,
         "charge (metric)": 0.18, "units": "$/kWh"},
        {"utility": "electric", "type": "demand", "name": "peak-demand",
         "month_start": 6, "month_end": 9, "weekday_start": 0, "weekday_end": 4,
         "hour_start": 16, "hour_end": 21, "basic_charge_limit (metric)": 0,
         "charge (metric)": 21.5, "units": "$/kW"}
      ]
    }

Charge windows are half-open on the hour: ``hour_start=16, hour_end=21`` bills
hours 16:00–20:59 inclusive (21:00 is off-peak).

An optional ``assessed`` column controls the billing period of a ``demand``
charge row: EECO defaults to ``"monthly"`` when the column is absent or the
row omits it, applying one demand-charge epigraph over the whole
``month_start``–``month_end`` window. Set ``"assessed": "daily"`` on a demand
row to instead apply a separate epigraph per calendar day within that
window — the pattern behind daily demand charges on tariffs such as those
increasingly common in California.

Tariff signal helpers
----------------------

Plain-pandas signals over a tariff, for writing logic/heuristic constraints.
Each is a flex-pse helper built on EECO's ``get_charge_dict`` charge arrays
(the source of the price data); EECO 0.2.1 exposes no per-stamp price accessor.

.. autosummary::
   :toctree: generated
   :nosignatures:

   price_series
   is_peak
   peak_windows
   price_gradient

In-objective cost bridge
------------------------

.. autosummary::
   :toctree: generated
   :nosignatures:

   add_operating_cost
   add_electricity_cost
   add_fuel_cost
   OperatingCostHandles

:func:`add_operating_cost` is the facility-level umbrella: it builds **both**
the electricity and fuel costs onto one opex block (EECO namespaces its Pyomo
components by utility, so ``electric_*`` and ``gas_*`` never collide) and returns
a single :class:`OperatingCostHandles` whose ``total_operating_cost`` is the sum
across utilities. The facility consumption defaults to the standard series on the
block — ``block.power_electrical`` and ``block.fuel_usage`` — so a caller need not
re-declare them each use; pass ``electrical_power``/``fuel_power`` to override. The
single-utility builders :func:`add_electricity_cost` and :func:`add_fuel_cost`
remain available for building one leg alone. :func:`add_fuel_cost` takes a
``fuel_type`` (default ``"gas"``, the only value EECO 0.2.1 supports); a
hydrogen utility is expected upstream and will add a second value.

Post-optimization evaluators
----------------------------

.. autosummary::
   :toctree: generated
   :nosignatures:

   evaluate_cost
   evaluate_fuel_cost

Demand response (containers-only in v0)
---------------------------------------

.. autosummary::
   :toctree: generated
   :nosignatures:

   DRConfig

.. note::

   Demand response is **containers-only** in v0 (architecture §2.4). A
   :class:`DRConfig` holds a loaded DR program so the wiring exists, and the
   internal DR hook is a no-op: supplying a DR file never changes the
   objective. Building DR event/curtailment/incentive/capacity constraints is
   post-v0. EECO 0.2.1 exposes no DR API, so the DR file format is a flex-pse
   placeholder loaded into the container only.

In-objective vs. reported cost
------------------------------

EECO is used two ways. :func:`add_operating_cost` asks EECO to build the
**convex-relaxed** operating-cost ``Expression`` on a Pyomo block — the
tractable proxy the scheduler minimizes. :func:`evaluate_cost` evaluates EECO on
a **fixed, realized** aggregate-power numpy array to compute the TRUE
(de-relaxed) cost — the user-facing bill (§6 reporting rule, R4/R9).

Because the relaxation drops the tiered energy surcharge when no consumption
estimate is supplied, the in-objective total is a proxy that is **≤ or ≈** the
post-hoc true bill. The raw solver objective is never reported as the
user-facing cost.

.. admonition:: Timezones / DST

   EECO reasons in naive **local wall-clock time**: its charge windows are keyed
   on ``datetime.month``/``weekday``/``hour`` with no timezone conversion.
   flex-pse v0 is consistently naive-local (matching
   :class:`~flexops.core.time_block.TimeBlock`). Timezone-aware datetime indices
   are rejected at the wrapper boundary with
   :class:`~flexcore.exceptions.FlexDataError`; strip the timezone
   (``index.tz_localize(None)``) before passing an index in.

FlexCosting block
-----------------

.. currentmodule:: flexops.costing.flex_costing

.. autosummary::
   :toctree: generated
   :nosignatures:

   FlexCosting
   FlexCostingData
   CostReport
   OperatingCostBreakdown
   CapitalCostBreakdown
   FuelSpec
   ScalarCostSpec

``FlexCosting`` subclasses IDAES ``FlowsheetCostingBlockData`` and **delegates
all tariff/energy operating cost to EECO** (decision R4), in two ways: it hands
EECO the aggregate electrical power (kW) + tariff to build the convex-relaxed
in-objective cost (:func:`~flexops.costing.add_electricity_cost`), and post-solve
calls EECO's evaluator (:func:`~flexops.costing.evaluate_cost`) for the reported
bill. Its own jobs are aggregation, the ``opex``/``capex`` block structure and
naming, CapEx + modes, and ``report_cost``; it writes no tariff cost math.

Every quantity FlexCosting exposes is a **decision-visible** ``Var`` defined by an
``eq_<name>`` equality ``Constraint`` — aggregate power, each cost line item, the
annualized cost, and the totals are first-class model variables (not bare
Expressions).

Every cost lives in one of two sub-blocks built by
:meth:`~FlexCostingData.cost_process`:

* **``opex``** holds all operating cost — ``electricity_cost`` and ``fuel_cost``
  (both from EECO), ``fixed_operating_cost`` (a non-tariff facility cost:
  maintenance/labor/chemicals, from ``CostingConfig.fixed_operating_cost``), and
  ``scalar_cost`` (non-energy flows/supplies/products, below). Their sum,
  ``total_operating_cost``, is re-exposed as ``aggregate_operating_cost`` — the
  operations-mode objective. The fixed operating cost is **distinct** from the
  tariff's own ``fixed_charge``, which EECO already folds into ``electricity_cost``.
* **``capex``** is an **empty placeholder** in v0 (``total_capital_cost == 0``,
  re-exposed as ``aggregate_capital_cost``); later milestones aggregate per-unit
  capital costs into it.

.. note:: **Indexed per-carrier power aggregation.**

   :meth:`~FlexCostingData.cost_process` pulls every registered power draw from
   the model and defines ``aggregate_power[t, carrier]`` in kW, where ``carrier``
   is ``"electrical"``, a registered **fuel** name, or a per-temperature thermal
   label ``"thermal@<T>K"``. Every draw is normalized to kW with
   ``pyunits.convert`` at aggregation, so a non-power (or non-kW-convertible) draw
   fails **loudly**. Thermal duties at **different temperatures are never summed**
   together — each temperature is its own carrier; ``aggregate_thermal_power`` is a
   temperature-blind total kept for backward compatibility.

.. note:: **Fuels — all billed via EECO's gas leg.**

   :meth:`~FlexCostingData.register_fuel` registers a named fuel (natural gas,
   hydrogen, biogas, …) with a ``heating_value`` and a ``fuel_units`` basis —
   volumetric (``m**3``, the default) or energy (``therm``). Its kW draws
   aggregate under the fuel's carrier and are billed through the existing
   :func:`~flexops.costing.add_fuel_cost` against the **same tariff** loaded at
   construction, normalized to the fuel's usage rate (``fuel_units/hr``) via the
   heating value. flex-pse synthesizes **no** tariff content and does **no**
   fuel-type recognition; a fuel priced in the tariff sheet's ``gas``-utility rows
   just works, and a tariff missing those rows surfaces EECO's own validation
   error.

.. note:: **Non-energy scalar costs — native, never via EECO.**

   :meth:`~FlexCostingData.register_scalar_cost` costs an arbitrary time-indexed
   rate (a flow/supply/product) as ``price × Σ_t quantity[t] × dt`` — e.g. water
   withdrawal ($/m³), chemical dosing ($/kg), or a product-revenue credit (a
   negative ``price``). Built entirely in flex-pse; EECO is not involved. A
   ``quantity`` that does not convert to the declared ``quantity_units`` raises,
   forcing unit consistency.

.. note:: **Annualization.**

   ``cost_process`` builds a ``capital_recovery_factor`` (from
   ``CostingConfig.lifetime_years`` and ``discount_rate``) and an
   ``annualized_cost`` Var ($/year): operating cost scaled from the horizon to a
   year plus capital cost times the CRF. With the empty v0 capex block, the
   annualized cost is just the operating cost on an annual basis.

.. note:: **Currency basis.**

   The costing block's ``base_currency`` — and the units on every operating- and
   capital-cost expression it builds — is the tariff sheet's currency basis, read
   from the charge ``units`` column by
   :func:`~flexops.costing.tariff_currency_units` (EECO tariffs
   are dollar-based: ``"$"`` → ``USD``). EECO's own cost expressions are
   dimensionless dollars, so FlexCosting casts them to this currency; the
   ``report_cost`` numbers are magnitudes in that currency.

.. note:: **Capital cost enters the objective only in design mode.**

   The operations-mode objective is ``aggregate_operating_cost`` alone; the
   design-mode objective is ``total_cost`` (operating **+** capital).
   :meth:`~FlexCostingData.set_operations_mode` fixes the registered sizing Vars
   and deactivates their capex constraints;
   :meth:`~FlexCostingData.set_design_mode` unfixes and activates them. Both are
   idempotent single-model toggles; in v0 the sizing registry is empty so they are
   no-ops (wiring for M08/M16). Merging representative months and linking sizing
   Vars across them is the M16 design wrapper, not this mode.

.. note:: **Reporting rule (R9, §6).**

   :meth:`~FlexCostingData.report_cost` returns a categorized :class:`CostReport`
   — capital vs. operating, each itemized — recomputed **post-solve**, never read
   off the solver objective (a relaxed/scalarized proxy). Operating electricity
   and fuel are EECO post-hoc evaluations on the realized dispatch; fixed is the
   config constant. In v0 ``fuel``, ``dr_revenue``, and the whole ``capital``
   breakdown are zero placeholders, so the structure is stable as those features
   land.

Construction-order invariant
----------------------------

``FlexCosting`` may be constructed **before any units exist** — the API-freeze
script builds ``m.costing`` before ``m.svcw.tank`` — because all aggregation and
the EECO call are deferred to ``cost_process()``, which pulls every unit's
registered power from the model. Building costing first, last, or between units
gives the identical result.
