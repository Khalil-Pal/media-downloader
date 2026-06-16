"""
config/logging_config.py – Structured logging configuration
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from config.settings import settings


def setup_logging() -> None:
    """Configure root logger with console + file handlers."""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level, logging.INFO))

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # File handler (rotated daily via logrotate on the host)
    fh = logging.FileHandler(log_dir / "sandy_squirrel.log", encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Silence overly verbose third-party loggers
    for noisy in ("httpx", "httpcore", "aiohttp", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
