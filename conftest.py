"""Repo-wide pytest configuration: tier-marker enforcement and unit-tier guards."""

import pytest

pytest_plugins = ["pytester"]

TIER_MARKERS = {"unit", "component", "integration"}


def pytest_collection_modifyitems(config, items):
    """Fail collection if any test carries zero or more than one tier marker."""
    errors = []
    for item in items:
        tiers = TIER_MARKERS & {m.name for m in item.iter_markers()}
        if len(tiers) != 1:
            errors.append(
                f"{item.nodeid}: needs exactly one tier marker "
                f"(unit/component/integration), got {sorted(tiers) or 'none'}"
            )
    if errors:
        raise pytest.UsageError("\n".join(errors))


@pytest.fixture(autouse=True)
def _no_solver_in_unit_tier(request, monkeypatch):
    """Block solver invocation and network access during unit-tier tests.

    TODO(M05): make real when flexcore.solvers.facade exists. Until then this
    is a documented no-op stub — there is nothing to monkeypatch yet.
    """
    if "unit" not in {m.name for m in request.node.iter_markers()}:
        return
    # TODO(M05): monkeypatch flexcore.solvers.facade to raise on solver invocation
    # and block socket access, once the solver facade exists.
