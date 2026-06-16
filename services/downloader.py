"""
services/downloader.py – Media download logic using yt-dlp.

This module is the ONLY place that touches yt-dlp.
Handlers must never call yt-dlp directly.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Callable

import yt_dlp

from config.settings import settings
from utils.formatters import format_duration, format_size, progress_bar

logger = logging.getLogger(__name__)

import os
import tempfile


def _get_cookies_file() -> str | None:
    """Write YOUTUBE_COOKIES env var to a temp file and return its path."""
    cookies_content = os.getenv("YOUTUBE_COOKIES")
    if not cookies_content:
        # Fall back to local file if it exists
        if os.path.exists("cookies.txt"):
            return "cookies.txt"
        return None

    # Write to a temporary file
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        delete=False,
        encoding="utf-8"
    )
    tmp.write(cookies_content)
    tmp.close()
    return tmp.name

# Semaphore caps global concurrent downloads
_download_semaphore = asyncio.Semaphore(settings.max_concurrent_downloads)

# Per-user cancellation tokens: user_id → asyncio.Event (set = cancel requested)
_cancel_flags: dict[int, asyncio.Event] = {}

QUALITY_FORMATS: dict[str, str] = {
    "best": "best/bestvideo+bestaudio",
    "720": "best[height<=720]/bestvideo[height<=720]+bestaudio/best",
    "480": "best[height<=480]/bestvideo[height<=480]+bestaudio/best",
    "360": "best[height<=360]/bestvideo[height<=360]+bestaudio/best",
    "144": "best[height<=144]/bestvideo[height<=144]+bestaudio/best",
}

AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio"


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


def _make_session_dir() -> Path:
    """Create a unique temp directory for this download session."""
    session_dir = settings.download_path / str(uuid.uuid4())
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def _build_progress_hook(
    callback: ProgressCallback | None,
    cancel_event: asyncio.Event | None,
    loop: asyncio.AbstractEventLoop,
) -> Callable[[dict], None]:
    """Return a yt-dlp progress hook that fires *callback* and checks cancellation."""
    last_update: dict = {"time": 0.0}

    def hook(d: dict) -> None:
        # Check cancellation from the main thread
        if cancel_event and cancel_event.is_set():
            raise yt_dlp.utils.DownloadError("Download cancelled by user.")

        if d["status"] == "downloading" and callback:
            now = time.monotonic()
            if now - last_update["time"] < 2.0:  # throttle to every 2 s
                return
            last_update["time"] = now

            pct_str = d.get("_percent_str", "0%").strip().rstrip("%")
            try:
                pct = float(pct_str)
            except ValueError:
                pct = 0.0

            downloaded = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            speed = d.get("speed") or 0
            eta = d.get("eta") or 0

            bar = progress_bar(pct)
            size_info = (
                f"{format_size(downloaded)} / {format_size(total)}"
                if total
                else format_size(downloaded)
            )
            speed_str = f"{format_size(speed)}/s" if speed else "—"
            eta_str = f"{eta}s" if eta else "—"

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
        pass  # Never let callback errors kill the download


def _extract_info_sync(url: str) -> dict:
    """Blocking yt-dlp info extraction (run in executor)."""
    cookies_file = _get_cookies_file()
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "skip_download": True,
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
    """Blocking download (run in executor). Returns (file_path, info_dict)."""
    cookies_file = _get_cookies_file()
    outtmpl = str(output_path / "%(title).80s.%(ext)s")

    postprocessors = []
    if audio_only:
        postprocessors.append(
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        )

    ydl_opts: dict = {
        "format": format_str,
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [progress_hook],
        "postprocessors": postprocessors,
        "nocheckcertificate": False,
        "geo_bypass": True,
        "max_filesize": settings.max_file_size_bytes,
    }
    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    # Locate the downloaded file
    found: Path | None = None
    for f in output_path.iterdir():
        if f.is_file():
            found = f
            break

    if not found:
        raise FileNotFoundError("Download completed but output file not found.")

    return found, info


async def fetch_info(url: str) -> MediaInfo:
    """
    Asynchronously fetch metadata for *url* without downloading.
    Raises ValueError on failure.
    """
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
        formats=formats[:6],  # Top 6 quality options
    )


async def download_media(
    url: str,
    user_id: int,
    quality: str = "best",
    audio_only: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> DownloadResult:
    """
    Download media and return a DownloadResult.
    Fully async: blocking I/O runs in a thread-pool executor.
    """
    loop = asyncio.get_running_loop()
    session_dir = _make_session_dir()
    cancel_event = _cancel_flags.setdefault(user_id, asyncio.Event())

    format_str = AUDIO_FORMAT if audio_only else QUALITY_FORMATS.get(quality, QUALITY_FORMATS["best"])
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

        # Guard: double-check size after download (in case yt-dlp estimate was off)
        actual_size = file_path.stat().st_size
        if actual_size > settings.max_file_size_bytes:
            file_path.unlink(missing_ok=True)
            return DownloadResult(
                success=False,
                error=(
                    f"❌ File is too large ({format_size(actual_size)}). "
                    f"Max allowed: {settings.max_file_size_mb}MB."
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
        return DownloadResult(success=False, error="❌ Download finished but file was not saved.")

    except Exception as exc:
        logger.exception("Unexpected download error for %s", url)
        return DownloadResult(success=False, error=f"❌ Unexpected error: {exc}")

    finally:
        # Clear cancel flag so next download starts fresh
        cancel_event.clear()


def cancel_download(user_id: int) -> bool:
    """Signal cancellation for *user_id*. Returns True if a flag existed."""
    event = _cancel_flags.get(user_id)
    if event:
        event.set()
        return True
    return False


def cleanup_session(file_path: Path | None) -> None:
    """Remove the session directory that contains *file_path*."""
    if file_path and file_path.exists():
        try:
            parent = file_path.parent
            file_path.unlink(missing_ok=True)
            # Remove directory only if it's now empty
            if parent != settings.download_path and not any(parent.iterdir()):
                parent.rmdir()
        except OSError as exc:
            logger.warning("Cleanup failed: %s", exc)


def _friendly_error(raw: str) -> str:
    """Map yt-dlp error messages to user-friendly strings."""
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
        return f"❌ File exceeds the {settings.max_file_size_mb}MB limit."
    if "network" in lower or "connection" in lower:
        return "❌ Network error. Please try again in a moment."
    return f"❌ Download failed: {raw[:200]}"
