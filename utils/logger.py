"""
===============================================================================
  Logging — structured, coloured console + rotating file output
===============================================================================
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import config as cfg


def setup_logging(name: str = "wolf") -> logging.Logger:
    """Create and return a configured logger instance."""
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, cfg.LOG_LEVEL.upper(), logging.INFO))

    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)-18s │ %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Console handler ──────────────────────────────────────────────────
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # ── File handler (10 MB, 5 backups) ──────────────────────────────────
    fh = RotatingFileHandler(
        cfg.LOG_DIR / f"{name}.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


def get_logger(module: str) -> logging.Logger:
    """Return a child logger for *module*."""
    parent = logging.getLogger("wolf")
    if not parent.handlers:
        setup_logging()
    return parent.getChild(module)
