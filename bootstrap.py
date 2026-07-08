"""Startup bootstrap for Railway-provided local files."""
from __future__ import annotations

import logging
import os
from pathlib import Path

_COOKIE_FILES = (
    ("YOUTUBE_COOKIES_TXT", "cookies.txt"),
    ("INSTAGRAM_COOKIES_TXT", "instagram_cookies.txt"),
)
_cookie_bootstrap_status: list[tuple[str, str, bool]] = []


def bootstrap_cookie_files(project_root: Path | None = None) -> None:
    """Write ignored cookie files from environment variables when provided."""
    root = project_root or Path(__file__).resolve().parent
    _cookie_bootstrap_status.clear()

    for env_name, filename in _COOKIE_FILES:
        contents = os.environ.get(env_name, "").strip()
        wrote_file = False

        if contents:
            output_path = root / filename
            output_path.write_text(contents + "\n", encoding="utf-8")
            wrote_file = True

        _cookie_bootstrap_status.append((filename, env_name, wrote_file))


def log_cookie_bootstrap_status(logger: logging.Logger | None = None) -> None:
    """Log cookie bootstrap results without exposing cookie contents."""
    log = logger or logging.getLogger(__name__)

    for filename, env_name, wrote_file in _cookie_bootstrap_status:
        if wrote_file:
            log.info("%s written from %s.", filename, env_name)
        else:
            log.info("%s left as-is; %s is unset or empty.", filename, env_name)

