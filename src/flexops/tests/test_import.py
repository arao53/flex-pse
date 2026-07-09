"""Smoke test: the flexops package is importable."""

import pytest


@pytest.mark.unit
def test_import():
    """Importing flexops succeeds."""
    import flexops  # noqa: F401
