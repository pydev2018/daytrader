"""
===============================================================================
  Logging — structured, coloured console + rotating file output
===============================================================================
Production hardened:
- File handler creation catches PermissionError on Windows
- Falls back to console-only if log file is locked
- Handler attachment inside lock to prevent race conditions
"""

import logging
import sys
import time
import threading
from logging.handlers import RotatingFileHandler

from config import settings as cfg

_setup_lock = threading.Lock()


def setup_logging(name: str = "wolf") -> logging.Logger:
    """Create and return a configured logger instance."""
    with _setup_lock:
        log_dir_ok = True
        try:
            cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            log_dir_ok = False

        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, cfg.LOG_LEVEL.upper(), logging.INFO))

        if logger.handlers:
            return logger

        fmt = logging.Formatter(
            "[%(asctime)s UTC] %(levelname)-8s %(name)-18s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        # Force UTC timestamps
        fmt.converter = time.gmtime

        # Console handler (always works)
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

        # File handler — with Windows lock protection
        if log_dir_ok:
            log_path = cfg.LOG_DIR / f"{name}.log"
            try:
                fh = RotatingFileHandler(
                    log_path,
                    maxBytes=10 * 1024 * 1024,
                    backupCount=5,
                    encoding="utf-8",
                )
                fh.setFormatter(fmt)
                logger.addHandler(fh)
            except PermissionError:
                # Log file locked by another process — try alternate name
                import os
                alt_path = cfg.LOG_DIR / f"{name}_{os.getpid()}.log"
                try:
                    fh = RotatingFileHandler(
                        alt_path,
                        maxBytes=10 * 1024 * 1024,
                        backupCount=5,
                        encoding="utf-8",
                    )
                    fh.setFormatter(fmt)
                    logger.addHandler(fh)
                    logger.warning(f"Primary log locked, using: {alt_path}")
                except Exception:
                    logger.warning("File logging disabled — console only")
            except Exception as exc:
                logger.warning(f"File logging failed: {exc} — console only")
        else:
            logger.warning("Log directory unavailable — console only")

    return logger


def get_logger(module: str) -> logging.Logger:
    """Return a child logger for *module*."""
    parent = logging.getLogger("wolf")
    if not parent.handlers:
        setup_logging()
    return parent.getChild(module)
