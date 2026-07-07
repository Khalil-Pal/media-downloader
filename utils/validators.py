"""
utils/validators.py – URL validation and platform detection
"""
from __future__ import annotations

import ipaddress
import re
import socket
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
        r"threads\.net/@[\w.]+/post/", re.IGNORECASE
    ),
    "Facebook": re.compile(
        r"(facebook\.com|fb\.watch)/.*(videos|watch|reel)", re.IGNORECASE
    ),
    "Twitter/X": re.compile(
        r"(twitter\.com|x\.com)/.*/status/", re.IGNORECASE
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

# Hostnames that should never be fetched directly.
BLOCKED_HOSTNAMES: frozenset[str] = frozenset({"localhost"})


def _is_public_ip(raw_ip: str) -> bool:
    """Return True only for globally routable IPv4/IPv6 addresses."""
    try:
        return ipaddress.ip_address(raw_ip).is_global
    except ValueError:
        return False


def _host_resolves_publicly(host: str, port: int | None) -> bool:
    """Reject hosts that resolve to private, loopback, link-local, or reserved IPs."""
    normalized = host.rstrip(".").lower()
    if not normalized or normalized in BLOCKED_HOSTNAMES:
        return False

    try:
        return ipaddress.ip_address(normalized).is_global
    except ValueError:
        pass

    try:
        ascii_host = normalized.encode("idna").decode("ascii")
        addresses = socket.getaddrinfo(
            ascii_host,
            port or 443,
            type=socket.SOCK_STREAM,
        )
    except (OSError, UnicodeError):
        return False

    resolved_ips = {item[4][0] for item in addresses}
    return bool(resolved_ips) and all(_is_public_ip(ip) for ip in resolved_ips)


def is_valid_url(url: str) -> bool:
    """Return True if *url* is HTTP/S and resolves only to public IPs."""
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    return _host_resolves_publicly(parsed.hostname, port)


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
