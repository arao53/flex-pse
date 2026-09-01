import logging
import threading
import time
from collections import deque

CONFIGURATION_SIMPLIFICATIONS = 21
logging.addLevelName(CONFIGURATION_SIMPLIFICATIONS, "CONFIGURATION_SIMPLIFICATIONS")

DEFAULT_LOGGER_NAME = "flex-pse"
DEFAULT_LOGGER_LEVEL = CONFIGURATION_SIMPLIFICATIONS

DEFAULT_DEDUP_ENABLED = {
    logging.WARNING: True,
    CONFIGURATION_SIMPLIFICATIONS: True,
}

_GLOBAL_LOGGER_LEVEL = DEFAULT_LOGGER_LEVEL
_GLOBAL_DEDUP_ENABLED: dict[int, bool] | None = None


def get_global_level() -> int:
    return _GLOBAL_LOGGER_LEVEL


def set_global_level(level: int) -> None:
    global _GLOBAL_LOGGER_LEVEL
    _GLOBAL_LOGGER_LEVEL = int(level)


def get_global_dedup_enabled() -> dict[int, bool]:
    global _GLOBAL_DEDUP_ENABLED
    if _GLOBAL_DEDUP_ENABLED is None:
        _GLOBAL_DEDUP_ENABLED = dict(DEFAULT_DEDUP_ENABLED)
    return dict(_GLOBAL_DEDUP_ENABLED)


def set_global_dedup_enabled(dedup_enabled: dict[int, bool]) -> None:
    global _GLOBAL_DEDUP_ENABLED
    _GLOBAL_DEDUP_ENABLED = dict(dedup_enabled)


class FlexPseLogger(logging.Logger):
    def configuration_simplifications(self, msg, *args, **kwargs):
        if self.isEnabledFor(CONFIGURATION_SIMPLIFICATIONS):
            self._log(CONFIGURATION_SIMPLIFICATIONS, msg, args, **kwargs)


logging.setLoggerClass(FlexPseLogger)


class DedupHandler(logging.Handler):
    def __init__(self, target=None, window=10.0, dedup_enabled=None):
        super().__init__()
        self.target = target
        self.window = window
        source = (
            dedup_enabled if dedup_enabled is not None else get_global_dedup_enabled()
        )
        self.dedup_enabled = dict(source)
        self._lock = threading.Lock()
        self._deque = deque()
        self._map = {}

    def emit(self, record):
        levelno = record.levelno
        if not self.dedup_enabled.get(levelno, False):
            if self.target:
                self.target.handle(record)
            return

        key = (record.getMessage(), record.pathname, record.lineno, levelno)
        now = time.monotonic()

        with self._lock:
            while self._deque and now - self._deque[0][1] > self.window:
                old_key, _ = self._deque.popleft()
                self._map.pop(old_key, None)

            if key in self._map:
                return

            self._deque.append((key, now))
            self._map[key] = now

        if self.target:
            self.target.handle(record)


def get_logger(name=None, dedup_enabled=None):
    if name is None:
        name = DEFAULT_LOGGER_NAME
    logger = logging.getLogger(name)
    logger.setLevel(_GLOBAL_LOGGER_LEVEL)
    logger.propagate = False

    if not any(isinstance(h, DedupHandler) for h in logger.handlers):
        stream_handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        stream_handler.setFormatter(formatter)
        effective_dedup = (
            dedup_enabled if dedup_enabled is not None else get_global_dedup_enabled()
        )
        dedup_handler = DedupHandler(
            target=stream_handler, dedup_enabled=effective_dedup
        )
        logger.addHandler(dedup_handler)

    return logger
