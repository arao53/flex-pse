# Why the reported cost isn't the solver's objective

The electricity cost a user sees in a run's report is always
{meth}`~flexops.costing.flex_costing.FlexCostingData.report_cost`. It is
computed **after** the model has solved, from the power values the solve
actually produced — never read off the solver's own internal objective value.

Those two numbers are not the same, and the difference is deliberate.

## The objective is a solver aid, not a bill

Some tariff structures — a tiered energy surcharge that only kicks in once
monthly consumption crosses a threshold, for example — are awkward for a
solver to handle directly: they introduce a jump or a non-convexity that makes
the optimization slower or, in the worst case, unsolvable in reasonable time.
So the cost expression built into the objective is a **simplified, relaxed**
version of the true tariff, chosen because it keeps the optimization
problem's structure tractable (an LP or MILP a fast open-source solver can
close in seconds, rather than a much harder nonconvex program).

That simplified expression is a proxy the optimizer minimizes to find a good
operating schedule. It was never meant to be read as a bill, and reading it as
one would be misleading: because the relaxation typically drops or
under-counts the tiered surcharge, the objective's value is usually **at or
below** the true cost of the schedule it produced.

## The report is the true cost of what actually happened

Once the solver has picked a schedule, `report_cost` evaluates the **real**
tariff — the full, un-relaxed cost function, tiers and all — against the
power values that schedule actually settles on. This is the number that
matches what a utility bill would show for that schedule, and it is the only
number flex-pse presents as "the cost."

The raw solver objective is never surfaced as the reported cost. It remains
available only behind an explicit debug flag, for anyone diagnosing the
optimization itself rather than trying to read a bill.
