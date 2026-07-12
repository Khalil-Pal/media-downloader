"""Shared cookie-file lookup helpers for media download backends."""
from __future__ import annotations

from pathlib import Path

YOUTUBE_COOKIES_FILE = Path("cookies.txt")
INSTAGRAM_COOKIES_FILE = Path("instagram_cookies.txt")


def get_youtube_cookies_file() -> str | None:
    """Return the yt-dlp YouTube cookie file path when it exists."""
    return str(YOUTUBE_COOKIES_FILE) if YOUTUBE_COOKIES_FILE.exists() else None


def get_instagram_cookies_file() -> str | None:
    """Return the shared Instagram cookie file path when it exists."""
    return str(INSTAGRAM_COOKIES_FILE) if INSTAGRAM_COOKIES_FILE.exists() else None
