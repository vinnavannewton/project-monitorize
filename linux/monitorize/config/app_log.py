"""Persistent desktop application logging."""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOG_FILE = Path(
    os.environ.get(
        "MONITORIZE_LOG_FILE",
        Path.home() / ".local" / "state" / "monitorize" / "monitorize.log",
    )
)
_logger = None


def configure(path=LOG_FILE):
    global _logger
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    logger = logging.getLogger("monitorize.desktop")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    handler = RotatingFileHandler(
        path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
        "%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    _logger = logger
    return path


def write(category, message, level=logging.INFO):
    logger = _logger
    if logger is None:
        return
    for line in str(message).rstrip().splitlines() or [""]:
        logger.log(level, "[%s] %s", category, line)


def install_exception_hook():
    previous = sys.excepthook

    def handle(exc_type, exc_value, traceback):
        logger = _logger
        if logger is None:
            logger = logging.getLogger("monitorize.desktop")
        logger.critical(
            "[APP] Unhandled exception",
            exc_info=(exc_type, exc_value, traceback),
        )
        previous(exc_type, exc_value, traceback)

    sys.excepthook = handle


def close():
    global _logger
    if _logger is None:
        return
    for handler in _logger.handlers[:]:
        handler.close()
        _logger.removeHandler(handler)
    _logger = None
