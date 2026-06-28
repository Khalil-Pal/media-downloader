"""
services/downloader.py – Media download logic using yt-dlp.
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

# Path to the bundled cookies.txt in the project root
_LOCAL_COOKIES_FILE = Path(__file__).parent.parent / "cookies.txt"

# ── Cookie helpers ────────────────────────────────────────────────────────────

def _get_cookies_file(env_var: str, fallback_file: Path | None = None) -> str | None:
    """Load cookies from an env var, or fall back to a file on disk."""
    content = os.getenv(env_var, "").strip()
    if content:
        content = content.replace("\\n", "\n")
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write(content)
        tmp.close()
        logger.info("Loaded %s from env (%d lines)", env_var, content.count("\n"))
        return tmp.name
    if fallback_file and fallback_file.exists():
        logger.info("Using fallback cookies file: %s", fallback_file)
        return str(fallback_file)
    return None


# ── yt-dlp base options ───────────────────────────────────────────────────────

_BASE_OPTS: dict = {
    "quiet": True,
    "no_warnings": True,
    "geo_bypass": True,
    "nocheckcertificate": True,
    "socket_timeout": 30,
    "retries": 5,
    "fragment_retries": 5,
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        ),
    },
}

_YT_EXTRACTOR_ARGS = {
    "youtube": {
        "player_client": ["ios", "android", "tv_embedded", "web"],
    }
}


def _build_opts(url: str, extra: dict | None = None) -> dict:
    opts = {**_BASE_OPTS}

    # YouTube-specific options
    if any(x in url for x in ("youtube.com", "youtu.be")):
        opts["extractor_args"] = _YT_EXTRACTOR_ARGS
         # Use YOUTUBE_COOKIES env var, falling back to the bundled cookies.txt
        cookies = _get_cookies_file("YOUTUBE_COOKIES", fallback_file=_LOCAL_COOKIES_FILE)
        if cookies:
            opts["cookiefile"] = cookies

    # Instagram-specific options
    elif "instagram.com" in url:
        # Use Instagram's mobile v1 API — less restricted than the web GraphQL API
        opts["extractor_args"] = {"instagram": {"api": ["v1"]}}
        cookies = _get_cookies_file("INSTAGRAM_COOKIES")
        if cookies:
            opts["cookiefile"] = cookies

    if extra:
        opts.update(extra)
    return opts


# ── Concurrency + cancellation ────────────────────────────────────────────────

_download_semaphore = asyncio.Semaphore(settings.max_concurrent_downloads)
_cancel_flags: dict[int, asyncio.Event] = {}


# ── Quality format selectors ──────────────────────────────────────────────────

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

AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio"


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
            asyncio.run_coroutine_threadsafe(_safe_callback(callback, msg), loop)

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
    opts = _build_opts(url, {"skip_download": True, "extract_flat": False})
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def _download_sync(
    url: str,
    output_path: Path,
    format_str: str,
    audio_only: bool,
    progress_hook: Callable[[dict], None],
) -> tuple[Path, dict]:
    outtmpl = str(output_path / "%(title).80s.%(ext)s")

    postprocessors = []
    if audio_only:
        postprocessors.append({
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        })

    opts = _build_opts(url, {
        "format": format_str,
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "progress_hooks": [progress_hook],
        "postprocessors": postprocessors,
    })

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

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
                None, _download_sync, url, session_dir, format_str, audio_only, hook,
            )

        actual_size = file_path.stat().st_size
        if actual_size > settings.max_file_size_bytes:
            file_path.unlink(missing_ok=True)
            return DownloadResult(
                success=False,
                error=f"❌ File is too large ({format_size(actual_size)}). Max: {settings.max_file_size_mb} MB.",
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

    except FileNotFoundError:
        return DownloadResult(success=False, error="❌ Download finished but file was not saved.")

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
    if "sign in" in lower or "429" in lower or "confirm" in lower:
        return "❌ This platform is blocking downloads from this server. Try again later."
    if "empty" in lower and "instagram" in lower:
        return "❌ Instagram requires login to download this content. Contact the bot admin."
    if "drm" in lower:
        return "❌ This video is DRM protected and cannot be downloaded."
    if "private" in lower:
        return "❌ This content is private or requires login."
    if "geo" in lower or "not available in your country" in lower:
        return "❌ This content is geo-blocked."
    if "age" in lower:
        return "❌ Age-restricted content cannot be downloaded."
    if "removed" in lower or "no longer available" in lower:
        return "❌ This content has been removed."
    if "unsupported url" in lower:
        return "❌ This URL is not supported."
    if "too large" in lower:
        return f"❌ File exceeds the {settings.max_file_size_mb} MB limit."
    if "network" in lower or "connection" in lower:
        return "❌ Network error. Please try again."
    if "login" in lower:
        return "❌ This content requires login."
    if "format" in lower and "not available" in lower:
        return "❌ No downloadable format found. Try a different quality."
    return f"❌ Download failed: {raw[:200]}"