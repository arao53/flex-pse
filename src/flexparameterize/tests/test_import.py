"""Smoke test: the flexparameterize package is importable."""

import pytest


@pytest.mark.unit
def test_import():
    """Importing flexparameterize succeeds."""
    import flexparameterize  # noqa: F401
