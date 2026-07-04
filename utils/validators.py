"""
utils/validators.py – URL validation and platform detection
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# Platforms explicitly supported by yt-dlp that we advertise.
# Threads uses the Instagram/Meta extractor in yt-dlp (no extra install needed).
SUPPORTED_PLATFORMS: dict[str, re.Pattern[str]] = {
    "YouTube": re.compile(
        r"(youtube\.com/(watch|shorts|live)|youtu\.be/)", re.IGNORECASE
    ),
    "Instagram": re.compile(
        r"instagram\.com/(p|reel|tv)/", re.IGNORECASE
    ),
    "Threads": re.compile(
        r"threads\.(net|com)/@[\w.]+/post/", re.IGNORECASE
    ),
    "Facebook": re.compile(
        r"(facebook\.com|fb\.watch)/.*(videos|watch|reel)", re.IGNORECASE
    ),
    "Twitter/X": re.compile(
        r"(twitter\.com|x\.com)/.*/status/", re.IGNORECASE
    ),
    "TikTok": re.compile(
        r"tiktok\.com/", re.IGNORECASE
    ),
    "Vimeo": re.compile(
        r"vimeo\.com/\d+", re.IGNORECASE
    ),
    "Reddit": re.compile(
        r"reddit\.com/.*/comments/", re.IGNORECASE
    ),
    "Dailymotion": re.compile(
        r"dailymotion\.com/video/", re.IGNORECASE
    ),
}

# Domains that are explicitly blocked regardless of content
BLOCKED_DOMAINS: frozenset[str] = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "10.",
        "192.168.",
        "169.254.",
        # Known harmful / piracy sites can be added here
    }
)

_URL_RE = re.compile(
    r"^https?://"
    r"(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,}"
    r"(?::\d+)?(?:/[^\s]*)?$",
    re.IGNORECASE,
)


def is_valid_url(url: str) -> bool:
    """Return True if *url* looks like a reachable HTTP/S URL."""
    url = url.strip()
    if not _URL_RE.match(url):
        return False
    parsed = urlparse(url)
    host = parsed.hostname or ""
    for blocked in BLOCKED_DOMAINS:
        if host.startswith(blocked) or host == blocked:
            return False
    return True


def detect_platform(url: str) -> str:
    """Return a human-readable platform name or 'Unknown'."""
    for name, pattern in SUPPORTED_PLATFORMS.items():
        if pattern.search(url):
            return name
    return "Unknown"


def extract_url_from_text(text: str) -> str | None:
    """Pull the first HTTP/S URL out of arbitrary user text."""
    match = re.search(r"https?://\S+", text)
    if match:
        candidate = match.group(0).rstrip(".,;!?)")
        return candidate if is_valid_url(candidate) else None
    return None