"""Smoke test: the flexcore package is importable."""

import pytest


@pytest.mark.unit
def test_import():
    """Importing flexcore succeeds."""
    import flexcore  # noqa: F401
