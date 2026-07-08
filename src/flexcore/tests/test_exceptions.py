"""Tests for the flex-pse exception hierarchy."""

import pytest

from flexcore.exceptions import (
    FlexConfigError,
    FlexDataError,
    FlexError,
    FlexSolverError,
)


@pytest.mark.unit
def test_hierarchy():
    """All three concrete exceptions subclass FlexError and Exception."""
    for exc_cls in (FlexConfigError, FlexSolverError, FlexDataError):
        assert issubclass(exc_cls, FlexError)
        assert issubclass(exc_cls, Exception)

        with pytest.raises(exc_cls):
            raise exc_cls("message")

        with pytest.raises(FlexError):
            raise exc_cls("message")
