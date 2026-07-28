"""flex-pse costing: the thin interface around the external EECO package.

The tariff/operating-cost engine is EECO (``eeco`` on PyPI); this package wraps
it (``plan/01_architecture.md`` §2.4/§3.6). All ``eeco`` calls are
collected in :mod:`flexops.costing.opex` (the sole import point).
"""

from flexops.costing.flex_costing import (
    CapitalCostBreakdown,
    CostReport,
    FlexCosting,
    FuelSpec,
    OperatingCostBreakdown,
    ScalarCostSpec,
)
from flexops.costing.opex import (
    DRConfig,
    OperatingCostHandles,
    add_electricity_cost,
    add_fuel_cost,
    add_operating_cost,
    evaluate_cost,
    evaluate_fuel_cost,
    is_peak,
    load_dr_program,
    load_tariff,
    peak_windows,
    price_gradient,
    price_series,
    tariff_csv_to_dict,
    tariff_currency_units,
)

__all__ = [
    "load_tariff",
    "load_dr_program",
    "tariff_csv_to_dict",
    "tariff_currency_units",
    "price_series",
    "is_peak",
    "peak_windows",
    "price_gradient",
    "add_operating_cost",
    "add_electricity_cost",
    "add_fuel_cost",
    "OperatingCostHandles",
    "evaluate_cost",
    "evaluate_fuel_cost",
    "DRConfig",
    "FlexCosting",
    "CostReport",
    "OperatingCostBreakdown",
    "CapitalCostBreakdown",
    "FuelSpec",
    "ScalarCostSpec",
]
