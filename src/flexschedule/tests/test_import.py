"""Smoke test: the flexschedule package is importable."""

import pytest


@pytest.mark.unit
def test_import():
    """Importing flexschedule succeeds."""
    import flexschedule  # noqa: F401
