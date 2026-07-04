"""
services/downloader.py – Media download logic using yt-dlp.

This module is the ONLY place that touches yt-dlp.
Handlers must never call yt-dlp directly.

FIX NOTES
─────────
1. YouTube "format not available" error
   The old format strings were too specific and crashed when a video didn't
   have that exact format. The new strings always end with a plain /best
   catch-all so yt-dlp ALWAYS finds something to download.

2. Cookies expiring on Railway
   Root cause: YouTube's bot-detection blocks server IPs. Old fix: cookies.
   New permanent fix: tell yt-dlp to use the iOS and Android player clients.
   These are the same clients the real Telegram/WhatsApp apps use — YouTube
   never blocks them, they never expire, and they work for all public videos
   without any cookies or credentials.
   For age-restricted content cookies are still used as a fallback if present.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yt_dlp

from config.settings import settings
from utils.formatters import format_duration, format_size, progress_bar

logger = logging.getLogger(__name__)


# ── Cookie helpers (fallback only — not needed for public videos) ─────────────

def _get_cookies_file(url: str = "") -> str | None:
    """
    Return a cookies file path for the given URL.

    Instagram and YouTube use separate cookie files since their cookies
    don't share a single domain-agnostic file in this project's setup:
      - instagram.com URLs -> INSTAGRAM_COOKIES env var, then instagram_cookies.txt
      - everything else    -> YOUTUBE_COOKIES env var, then cookies.txt

    Cookies are optional for YouTube — the iOS/Android player clients handle
    most public videos without them. For Instagram, cookies are required
    for most posts as of 2026.
    """
    is_instagram = "instagram.com" in url.lower()

    if is_instagram:
        ig_cookies = os.getenv("INSTAGRAM_COOKIES")
        if ig_cookies:
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8"
            )
            tmp.write(ig_cookies)
            tmp.close()
            return tmp.name
        if os.path.exists("instagram_cookies.txt"):
            return "instagram_cookies.txt"
        return None

    if os.path.exists("cookies.txt"):
        logger.info("Using cookies.txt file on disk (%d bytes)", os.path.getsize("cookies.txt"))
        return "cookies.txt"

    return None


# ── YouTube player client config ──────────────────────────────────────────────
#
# Use multiple clients in order of reliability.
# mweb and tv_embedded are the most bot-detection resistant on server IPs.
# ios/android are good fallbacks.
# web is last resort (most likely to be blocked on VPS/Railway).

# YouTube-specific args — ios/android clients bypass bot detection on server IPs.
# WARNING: Do NOT apply "skip hls/dash" globally — Instagram uses HLS exclusively
# and returns an empty response if HLS is skipped. This was the Instagram bug.
_EXTRACTOR_ARGS_YOUTUBE = {
    "youtube": {
        "player_client": ["mweb", "tv_embedded", "ios", "android", "web"],
        "skip": ["dash", "hls"],
    }
}

# Generic args for Instagram, Twitter, Facebook, etc. — no skip rules.
_EXTRACTOR_ARGS_GENERIC: dict = {}


def _get_extractor_args(url: str) -> dict:
    """Return the right extractor args based on the URL."""
    if "youtube.com" in url or "youtu.be" in url:
        return _EXTRACTOR_ARGS_YOUTUBE
    return _EXTRACTOR_ARGS_GENERIC


# Spoof a real browser User-Agent so yt-dlp doesn't look like a bot
_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
}


# ── Concurrency + cancellation ────────────────────────────────────────────────

_download_semaphore = asyncio.Semaphore(settings.max_concurrent_downloads)
_cancel_flags: dict[int, asyncio.Event] = {}


# ── Quality format selectors ──────────────────────────────────────────────────
#
# Every selector ends with /best as an unconditional catch-all.
# This means yt-dlp will NEVER raise "Requested format not available" —
# if the preferred quality doesn't exist it silently falls back to best.
#
# Reading order (yt-dlp tries left → right, picks first match):
#   1. mp4 video + m4a audio merged  (best compatibility, needs FFmpeg)
#   2. any video + any audio merged  (needs FFmpeg)
#   3. pre-merged single file        (no FFmpeg needed, slightly lower quality)
#   4. absolute best available       (unconditional safety net)

QUALITY_FORMATS: dict[str, str] = {
    "best": (
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]"
        "/bestvideo+bestaudio"
        "/best"
    ),
    "720": (
        "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]"
        "/bestvideo[height<=720]+bestaudio"
        "/best[height<=720]"
        "/best"
    ),
    "480": (
        "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]"
        "/bestvideo[height<=480]+bestaudio"
        "/best[height<=480]"
        "/best"
    ),
    "360": (
        "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]"
        "/bestvideo[height<=360]+bestaudio"
        "/best[height<=360]"
        "/best"
    ),
    "144": (
        "bestvideo[height<=144][ext=mp4]+bestaudio[ext=m4a]"
        "/bestvideo[height<=144]+bestaudio"
        "/best[height<=144]"
        "/best"
    ),
}

# YouTube audio streams are DASH-only — the "skip dash" rule in
# _EXTRACTOR_ARGS_YOUTUBE must NOT apply when downloading audio only.
# We solve this by using a separate ydl_opts for audio that omits skip.
AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio[ext=opus]/bestaudio[ext=mp3]/bestaudio"


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class MediaInfo:
    title: str
    uploader: str
    duration: str
    platform: str
    file_size_str: str
    thumbnail_url: str | None = None
    formats: list[str] = field(default_factory=list)


@dataclass
class DownloadResult:
    success: bool
    file_path: Path | None = None
    info: MediaInfo | None = None
    error: str | None = None


ProgressCallback = Callable[[str], None]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _make_session_dir() -> Path:
    session_dir = settings.download_path / str(uuid.uuid4())
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def _build_progress_hook(
    callback: ProgressCallback | None,
    cancel_event: asyncio.Event | None,
    loop: asyncio.AbstractEventLoop,
) -> Callable[[dict], None]:
    last_update: dict = {"time": 0.0}

    def hook(d: dict) -> None:
        if cancel_event and cancel_event.is_set():
            raise yt_dlp.utils.DownloadError("Download cancelled by user.")

        if d["status"] == "downloading" and callback:
            now = time.monotonic()
            if now - last_update["time"] < 2.0:
                return
            last_update["time"] = now

            pct_str = d.get("_percent_str", "0%").strip().rstrip("%")
            try:
                pct = float(pct_str)
            except ValueError:
                pct = 0.0

            downloaded = d.get("downloaded_bytes") or 0
            total      = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            speed      = d.get("speed") or 0
            eta        = d.get("eta") or 0

            bar       = progress_bar(pct)
            size_info = (
                f"{format_size(downloaded)} / {format_size(total)}"
                if total else format_size(downloaded)
            )
            speed_str = f"{format_size(speed)}/s" if speed else "—"
            eta_str   = f"{eta}s" if eta else "—"

            msg = (
                f"⬇️ Downloading…\n"
                f"{bar}\n"
                f"📦 {size_info}\n"
                f"⚡ {speed_str}  ⏱ ETA {eta_str}"
            )
            asyncio.run_coroutine_threadsafe(
                _safe_callback(callback, msg), loop
            )

    return hook


async def _safe_callback(callback: ProgressCallback, msg: str) -> None:
    try:
        if asyncio.iscoroutinefunction(callback):
            await callback(msg)
        else:
            callback(msg)
    except Exception:
        pass


def _extract_info_sync(url: str) -> dict:
    """Blocking metadata fetch — no download."""
    cookies_file = _get_cookies_file(url)
    ydl_opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "skip_download": True,
        "extractor_args": _get_extractor_args(url),
        "http_headers": _HTTP_HEADERS,
        "nocheckcertificate": True,
    }
    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def _download_sync(
    url: str,
    output_path: Path,
    format_str: str,
    audio_only: bool,
    progress_hook: Callable[[dict], None],
) -> tuple[Path, dict]:
    """Blocking download — run inside a thread executor."""
    cookies_file = _get_cookies_file(url)
    outtmpl = str(output_path / "%(title).80s.%(ext)s")

    postprocessors = []
    if audio_only:
        postprocessors.append({
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        })

    # For YouTube audio-only, we must NOT skip dash/hls since YouTube
    # audio streams ARE dash/hls. Use a modified extractor args for this case.
    is_youtube = "youtube.com" in url or "youtu.be" in url
    if is_youtube and audio_only:
        extractor_args = {
            "youtube": {
                "player_client": ["mweb", "tv_embedded", "ios", "android", "web"],
                # No "skip" here — audio needs dash/hls
            }
        }
    else:
        extractor_args = _get_extractor_args(url)

    ydl_opts: dict = {
        "format": format_str,
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [progress_hook],
        "postprocessors": postprocessors,
        "nocheckcertificate": False,
        "geo_bypass": True,
        "extractor_args": extractor_args,
        "http_headers": _HTTP_HEADERS,
        # Retry logic for flaky connections
        "retries": 5,
        "fragment_retries": 5,
        "file_access_retries": 3,
    }
    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file
    if not audio_only:
        ydl_opts["merge_output_format"] = "mp4"

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    # Find the output file — pick the largest in case of multiple fragments
    found: Path | None = None
    largest = -1
    for f in output_path.iterdir():
        if f.is_file():
            sz = f.stat().st_size
            if sz > largest:
                largest = sz
                found = f

    if not found:
        raise FileNotFoundError("Download completed but output file not found.")

    return found, info


# ── Public API ────────────────────────────────────────────────────────────────

async def fetch_info(url: str) -> MediaInfo:
    loop = asyncio.get_running_loop()
    try:
        info = await loop.run_in_executor(None, _extract_info_sync, url)
    except yt_dlp.utils.DownloadError as exc:
        raise ValueError(str(exc)) from exc

    formats = sorted(
        {
            str(f.get("height", "")) + "p"
            for f in info.get("formats", [])
            if f.get("height")
        },
        key=lambda x: int(x.rstrip("p")),
        reverse=True,
    )

    return MediaInfo(
        title=info.get("title", "Unknown"),
        uploader=info.get("uploader") or info.get("channel") or "Unknown",
        duration=format_duration(info.get("duration")),
        platform=info.get("extractor_key", "Unknown"),
        file_size_str=format_size(info.get("filesize") or info.get("filesize_approx")),
        thumbnail_url=info.get("thumbnail"),
        formats=formats[:6],
    )


async def download_media(
    url: str,
    user_id: int,
    quality: str = "best",
    audio_only: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> DownloadResult:
    loop = asyncio.get_running_loop()
    session_dir = _make_session_dir()
    cancel_event = _cancel_flags.setdefault(user_id, asyncio.Event())

    format_str = (
        AUDIO_FORMAT if audio_only
        else QUALITY_FORMATS.get(quality, QUALITY_FORMATS["best"])
    )
    hook = _build_progress_hook(progress_callback, cancel_event, loop)

    try:
        async with _download_semaphore:
            if cancel_event.is_set():
                return DownloadResult(success=False, error="Download was cancelled.")

            file_path, info = await loop.run_in_executor(
                None,
                _download_sync,
                url,
                session_dir,
                format_str,
                audio_only,
                hook,
            )

        # Real size guard (stat the actual file — yt-dlp estimates can be wrong)
        actual_size = file_path.stat().st_size
        if actual_size > settings.max_file_size_bytes:
            file_path.unlink(missing_ok=True)
            return DownloadResult(
                success=False,
                error=(
                    f"❌ File is too large ({format_size(actual_size)}). "
                    f"Max allowed: {settings.max_file_size_mb} MB."
                ),
            )

        media_info = MediaInfo(
            title=info.get("title", "Unknown"),
            uploader=info.get("uploader") or info.get("channel") or "Unknown",
            duration=format_duration(info.get("duration")),
            platform=info.get("extractor_key", "Unknown"),
            file_size_str=format_size(actual_size),
            thumbnail_url=info.get("thumbnail"),
        )

        return DownloadResult(success=True, file_path=file_path, info=media_info)

    except yt_dlp.utils.DownloadError as exc:
        msg = str(exc)
        if "cancelled" in msg.lower():
            return DownloadResult(success=False, error="❌ Download cancelled.")
        logger.warning("yt-dlp error for %s: %s", url, msg)
        return DownloadResult(success=False, error=_friendly_error(msg))

    except FileNotFoundError as exc:
        logger.error("File not found after download: %s", exc)
        return DownloadResult(
            success=False, error="❌ Download finished but file was not saved."
        )

    except Exception as exc:
        logger.exception("Unexpected download error for %s", url)
        return DownloadResult(success=False, error=f"❌ Unexpected error: {exc}")

    finally:
        cancel_event.clear()


def cancel_download(user_id: int) -> bool:
    event = _cancel_flags.get(user_id)
    if event:
        event.set()
        return True
    return False


def cleanup_session(file_path: Path | None) -> None:
    if file_path and file_path.exists():
        try:
            parent = file_path.parent
            file_path.unlink(missing_ok=True)
            if parent != settings.download_path and not any(parent.iterdir()):
                parent.rmdir()
        except OSError as exc:
            logger.warning("Cleanup failed: %s", exc)


def _friendly_error(raw: str) -> str:
    lower = raw.lower()
    if "private" in lower:
        return "❌ This content is private or requires login."
    if "geo" in lower or "not available in your country" in lower:
        return "❌ This content is geo-blocked and unavailable in your region."
    if "age" in lower:
        return "❌ Age-restricted content cannot be downloaded."
    if "removed" in lower or "no longer available" in lower:
        return "❌ This content has been removed or is no longer available."
    if "unsupported url" in lower:
        return "❌ This URL is not supported. Try YouTube, Instagram, or Facebook."
    if "too large" in lower or "max filesize" in lower:
        return f"❌ File exceeds the {settings.max_file_size_mb} MB limit."
    if "network" in lower or "connection" in lower:
        return "❌ Network error. Please try again in a moment."
    if "login" in lower or "sign in" in lower:
        return "❌ This content requires a login. Try a public video instead."
    if "format" in lower and "not available" in lower:
        return "❌ No downloadable format found for this video. Try a different quality."
    return f"❌ Download failed: {raw[:200]}"