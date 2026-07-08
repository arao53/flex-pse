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


@pytest.mark.unit
def test_attributes_default_to_none():
    """Context attributes are optional and default to None."""
    assert FlexConfigError("bad config").field is None
    assert FlexConfigError("bad config").value is None
    assert FlexSolverError("no solver").solver is None
    assert FlexSolverError("no solver").problem_class is None
    assert FlexDataError("missing data").field is None


@pytest.mark.unit
def test_attributes_are_captured():
    """Keyword context passed at raise time is exposed as attributes."""
    config_err = FlexConfigError("bad config", field="time_step", value=7)
    assert config_err.field == "time_step"
    assert config_err.value == 7

    solver_err = FlexSolverError("no solver", solver="ipopt", problem_class="MINLP")
    assert solver_err.solver == "ipopt"
    assert solver_err.problem_class == "MINLP"

    data_err = FlexDataError("missing data", field="reactor.T_inlet")
    assert data_err.field == "reactor.T_inlet"
