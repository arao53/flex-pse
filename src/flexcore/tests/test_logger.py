"""Unit tests for :mod:`flexcore.logger`.

Covers the custom ``CONFIGURATION_SIMPLIFICATIONS`` level, the ``FlexPseLogger``
class, the ``DedupHandler`` sliding-window deduplication, and the global runtime
controls. Time is monkeypatched so the dedup window can be exercised without
sleeping.
"""

import logging
import time

import pytest

from flexcore import logger as logger_module
from flexcore.logger import (
    CONFIGURATION_SIMPLIFICATIONS,
    DEFAULT_DEDUP_ENABLED,
    DEFAULT_LOGGER_LEVEL,
    DedupHandler,
    get_global_dedup_enabled,
    get_global_level,
    get_logger,
    set_global_dedup_enabled,
    set_global_level,
)


@pytest.mark.unit
def test_configuration_simplifications_level():
    """The custom level sits between INFO and WARNING so it is visible but
    below warnings."""
    assert logging.INFO < CONFIGURATION_SIMPLIFICATIONS < logging.WARNING


@pytest.mark.unit
def test_level_name_registered():
    """The custom level is registered with logging under its string name."""
    assert (
        logging.getLevelName(CONFIGURATION_SIMPLIFICATIONS)
        == "CONFIGURATION_SIMPLIFICATIONS"
    )


@pytest.mark.unit
def test_get_logger_default_name():
    """get_logger() returns a logger named 'flex-pse' when no name is supplied."""
    logger = get_logger()
    assert logger.name == "flex-pse"


@pytest.mark.unit
def test_get_logger_custom_name():
    """get_logger(name) returns a logger with the requested name."""
    logger = get_logger("custom.logger")
    assert logger.name == "custom.logger"


@pytest.mark.unit
def test_default_level():
    """New loggers inherit the global default level (CONFIGURATION_SIMPLIFICATIONS)."""
    logger_module._GLOBAL_LOGGER_LEVEL = DEFAULT_LOGGER_LEVEL
    logger = get_logger("test.default.level")
    assert logger.level == DEFAULT_LOGGER_LEVEL
    assert logger.level == CONFIGURATION_SIMPLIFICATIONS


@pytest.mark.unit
def test_default_dedup_enabled():
    """The default dedup mapping enables dedup for WARNING and
    CONFIGURATION_SIMPLIFICATIONS."""
    assert DEFAULT_DEDUP_ENABLED == {
        logging.WARNING: True,
        CONFIGURATION_SIMPLIFICATIONS: True,
    }


@pytest.mark.unit
def test_get_global_level_defaults():
    """get_global_level() returns DEFAULT_LOGGER_LEVEL when not overridden."""
    logger_module._GLOBAL_LOGGER_LEVEL = DEFAULT_LOGGER_LEVEL
    assert get_global_level() == DEFAULT_LOGGER_LEVEL


@pytest.mark.unit
def test_set_global_level():
    """set_global_level() updates the global level and new loggers pick it up."""
    logger_module._GLOBAL_LOGGER_LEVEL = DEFAULT_LOGGER_LEVEL
    set_global_level(logging.WARNING)
    assert get_global_level() == logging.WARNING
    logger = get_logger("test.set.level")
    assert logger.level == logging.WARNING


@pytest.mark.unit
def test_get_global_dedup_enabled_defaults():
    """get_global_dedup_enabled() lazily initialises to DEFAULT_DEDUP_ENABLED."""
    logger_module._GLOBAL_DEDUP_ENABLED = None
    assert get_global_dedup_enabled() == dict(DEFAULT_DEDUP_ENABLED)


@pytest.mark.unit
def test_set_global_dedup_enabled():
    """set_global_dedup_enabled() replaces the global mapping."""
    logger_module._GLOBAL_DEDUP_ENABLED = None
    set_global_dedup_enabled({logging.INFO: True, logging.WARNING: False})
    assert get_global_dedup_enabled() == {
        logging.INFO: True,
        logging.WARNING: False,
    }


@pytest.mark.unit
def test_set_global_dedup_enabled_does_not_mutate_input():
    """set_global_dedup_enabled() copies the input dict so callers'
    mutations don't leak."""
    logger_module._GLOBAL_DEDUP_ENABLED = None
    original = {logging.INFO: True}
    set_global_dedup_enabled(original)
    original[logging.INFO] = False
    assert get_global_dedup_enabled() == {logging.INFO: True}


class _RecordingHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _make_logger(name, dedup_enabled=None):
    logger_module._GLOBAL_DEDUP_ENABLED = None
    logger_module._GLOBAL_LOGGER_LEVEL = DEFAULT_LOGGER_LEVEL
    logger = get_logger(name, dedup_enabled=dedup_enabled)
    logger.setLevel(logging.DEBUG)
    target = _RecordingHandler()
    target.setLevel(logging.DEBUG)
    dedup = DedupHandler(target=target, dedup_enabled=dedup_enabled)
    logger.handlers = [dedup]
    logger.propagate = False
    return logger, target


@pytest.mark.unit
def test_warning_deduplicated_by_default(monkeypatch):
    """Repeated warnings at the same call site are deduplicated within the
    window; only one record is emitted."""
    logger, target = _make_logger("test.warn.dedup")
    fake_time = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: fake_time[0])

    for _ in range(3):
        logger.warning("dup warning")

    assert len(target.records) == 1
    assert target.records[0].getMessage() == "dup warning"
    assert target.records[0].levelno == logging.WARNING


@pytest.mark.unit
def test_warning_not_deduplicated_when_disabled(monkeypatch):
    """When dedup is disabled for WARNING, every warning is emitted independently."""
    logger, target = _make_logger(
        "test.warn.no.dedup", dedup_enabled={logging.WARNING: False}
    )
    fake_time = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: fake_time[0])

    for _ in range(3):
        logger.warning("plain warning")

    assert len(target.records) == 3
    assert all(r.levelno == logging.WARNING for r in target.records)


@pytest.mark.unit
def test_configuration_simplifications_deduplicated_by_default(monkeypatch):
    """Repeated configuration_simplifications messages are deduplicated by default."""
    logger, target = _make_logger("test.cs.dedup")
    fake_time = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: fake_time[0])

    for _ in range(3):
        logger.configuration_simplifications("dup cs")

    assert len(target.records) == 1
    assert target.records[0].getMessage() == "dup cs"
    assert target.records[0].levelno == CONFIGURATION_SIMPLIFICATIONS


@pytest.mark.unit
def test_configuration_simplifications_not_deduplicated_when_disabled(
    monkeypatch,
):
    """When dedup is disabled for CONFIGURATION_SIMPLIFICATIONS, every
    message is emitted."""
    logger, target = _make_logger(
        "test.cs.no.dedup",
        dedup_enabled={CONFIGURATION_SIMPLIFICATIONS: False},
    )
    fake_time = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: fake_time[0])

    for _ in range(3):
        logger.configuration_simplifications("cs message")

    assert len(target.records) == 3
    assert all(r.levelno == CONFIGURATION_SIMPLIFICATIONS for r in target.records)


@pytest.mark.unit
def test_info_not_deduplicated_by_default(monkeypatch):
    """INFO messages are not deduplicated by default; all three are emitted."""
    logger, target = _make_logger("test.info.no.dedup")
    fake_time = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: fake_time[0])

    for _ in range(3):
        logger.info("plain info")

    assert len(target.records) == 3
    assert all(r.levelno == logging.INFO for r in target.records)


@pytest.mark.unit
def test_info_deduplicated_when_enabled(monkeypatch):
    """When dedup is enabled for INFO, repeated messages collapse to one record."""
    logger, target = _make_logger("test.info.dedup", dedup_enabled={logging.INFO: True})
    fake_time = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: fake_time[0])

    for _ in range(3):
        logger.info("dup info")

    assert len(target.records) == 1
    assert target.records[0].getMessage() == "dup info"
    assert target.records[0].levelno == logging.INFO


@pytest.mark.unit
def test_dedup_allows_after_window_expires(monkeypatch):
    """After the sliding window expires, the same message is emitted again
    as a new record."""
    logger, target = _make_logger("test.window.expires")
    fake_time = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: fake_time[0])

    logger.warning("first burst")
    fake_time[0] = 6.0
    logger.warning("second burst")

    assert len(target.records) == 2
    assert target.records[0].getMessage() == "first burst"
    assert target.records[1].getMessage() == "second burst"


@pytest.mark.unit
def test_dedup_ignores_different_call_sites(monkeypatch):
    """Dedup keys on (message, pathname, lineno, levelno), so different
    call sites are not considered duplicates."""
    logger, target = _make_logger("test.different.sites")
    fake_time = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: fake_time[0])

    logger.warning("same message")
    record = logging.LogRecord(
        name="test.different.sites",
        level=logging.WARNING,
        pathname="other_module.py",
        lineno=42,
        msg="same message",
        args=None,
        exc_info=None,
    )
    logger.handle(record)

    assert len(target.records) == 2


@pytest.mark.unit
def test_non_dedup_levels_not_deduplicated(monkeypatch):
    """Levels without dedup enabled emit every record; only WARNING and
    CONFIGURATION_SIMPLIFICATIONS are deduped here."""
    logger, target = _make_logger("test.other.levels")
    fake_time = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: fake_time[0])

    for _ in range(2):
        logger.debug("dup debug")
    for _ in range(2):
        logger.info("dup info")
    for _ in range(2):
        logger.configuration_simplifications("dup cs")
    for _ in range(2):
        logger.error("dup error")
    for _ in range(2):
        logger.warning("dup warning")

    assert len(target.records) == 8
    assert [r.levelno for r in target.records] == [
        logging.DEBUG,
        logging.DEBUG,
        logging.INFO,
        logging.INFO,
        CONFIGURATION_SIMPLIFICATIONS,
        logging.ERROR,
        logging.ERROR,
        logging.WARNING,
    ]
