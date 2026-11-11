"""Logging configuration: console + rotating file handler (logs/pipeline.log)."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "ecommerce_dw"
_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def setup_logging(level: str = "INFO", log_dir: Path | None = None,
                  log_file_name: str = "pipeline.log") -> logging.Logger:
    """Configure the project logger. Safe to call more than once (handlers are replaced)."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level.upper())
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(_FORMAT)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(log_dir / log_file_name, maxBytes=10_000_000,
                                           backupCount=5, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger of the project logger (e.g. ``ecommerce_dw.ingestion``)."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
