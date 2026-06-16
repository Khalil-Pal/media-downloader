"""
config/settings.py – Centralised configuration loaded from .env
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"Required environment variable '{key}' is not set.")
    return value


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    # ── Telegram ──────────────────────────────────────────────────────────
    bot_token: str = field(default_factory=lambda: _require("BOT_TOKEN"))
    admin_id: int = field(default_factory=lambda: _int("ADMIN_ID", 0))

    # ── File limits ───────────────────────────────────────────────────────
    max_file_size_mb: int = field(default_factory=lambda: _int("MAX_FILE_SIZE_MB", 50))
    download_path: Path = field(
        default_factory=lambda: Path(os.getenv("DOWNLOAD_PATH", "./temp_downloads"))
    )

    # ── Rate limiting ─────────────────────────────────────────────────────
    rate_limit_max: int = field(default_factory=lambda: _int("RATE_LIMIT_MAX", 3))
    rate_limit_window: int = field(default_factory=lambda: _int("RATE_LIMIT_WINDOW", 60))
    cooldown_seconds: int = field(default_factory=lambda: _int("COOLDOWN_SECONDS", 5))

    # ── Concurrency ───────────────────────────────────────────────────────
    max_concurrent_downloads: int = field(
        default_factory=lambda: _int("MAX_CONCURRENT_DOWNLOADS", 5)
    )

    # ── Defaults ──────────────────────────────────────────────────────────
    default_quality: str = field(
        default_factory=lambda: os.getenv("DEFAULT_QUALITY", "best")
    )
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper()
    )

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    def ensure_download_dir(self) -> None:
        self.download_path.mkdir(parents=True, exist_ok=True)


# Singleton – import this everywhere
settings = Settings()
settings.ensure_download_dir()
