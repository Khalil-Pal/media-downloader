"""
utils/formatters.py – Human-friendly string formatting helpers
"""
from __future__ import annotations

import math


def format_duration(seconds: int | float | None) -> str:
    """Convert seconds to HH:MM:SS or MM:SS string."""
    if seconds is None:
        return "Unknown"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_size(size_bytes: int | float | None) -> str:
    """Convert bytes to human-readable string (KB / MB / GB)."""
    if size_bytes is None or size_bytes <= 0:
        return "Unknown"
    units = ("B", "KB", "MB", "GB", "TB")
    exp = min(int(math.log(size_bytes, 1024)), len(units) - 1)
    value = size_bytes / (1024 ** exp)
    return f"{value:.1f} {units[exp]}"


def truncate(text: str, max_len: int = 200) -> str:
    """Truncate long strings with ellipsis."""
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def progress_bar(percent: float, width: int = 10) -> str:
    """ASCII progress bar: ████░░░░░░ 42%"""
    filled = int(width * percent / 100)
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar} {percent:.0f}%"


def escape_md(text: str) -> str:
    """Escape MarkdownV2 special characters for Telegram."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))
