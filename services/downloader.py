"""
services/downloader.py – Media download logic using yt-dlp.

This module is the ONLY place that touches yt-dlp.
Handlers must never call yt-dlp directly.

FIX NOTES
─────────
YouTube can return HTTP 403 after metadata has loaded because YouTube may
require a short-lived Proof-of-Origin (PO) token for Google Video Server
requests. This file uses yt-dlp's recommended current approach:

- use the `mweb` YouTube client;
- load the external `bgutil-ytdlp-pot-provider` plugin;
- point the plugin to its Deno-based generator installed by the Dockerfile.

Instagram cookie handling is intentionally left unchanged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
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

def _get_cookies_file(url: str = "") -> tuple[str | None, bool]:
    """Return an optional cookies file path and whether it is temporary.

    Instagram keeps its existing cookie support. YouTube cookies are deliberately
    opt-in: an old cookies.txt copied into a cloud container can make every
    request look suspicious and lead to HTTP 403 responses.
    """
    is_instagram = "instagram.com" in url.lower()

    if is_instagram:
        ig_cookies = os.getenv("INSTAGRAM_COOKIES", "").strip()
        if ig_cookies:
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8"
            )
            tmp.write(ig_cookies)
            tmp.close()
            return tmp.name, True
        if os.path.exists("instagram_cookies.txt"):
            return "instagram_cookies.txt", False
        return None, False

    # Never silently use cookies.txt for YouTube. Set both variables in Railway
    # only when account access is genuinely required for a specific video.
    use_youtube_cookies = os.getenv("USE_YOUTUBE_COOKIES", "false").lower() == "true"
    youtube_cookies = os.getenv("YOUTUBE_COOKIES", "").strip()
    if use_youtube_cookies and youtube_cookies:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        tmp.write(youtube_cookies)
        tmp.close()
        return tmp.name, True

    return None, False


def _remove_temp_cookies_file(cookies_file: str | None, is_temporary: bool) -> None:
    """Remove cookie files created from env vars after yt-dlp has used them."""
    if not cookies_file or not is_temporary:
        return
    try:
        Path(cookies_file).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not remove temporary cookies file: %s", exc)


# ── YouTube extraction configuration ─────────────────────────────────────────
#
# YouTube can require a fresh, video-bound Proof-of-Origin (PO) token while
# requesting the real media stream. The installed bgutil provider generates
# that token automatically for the `mweb` client. This is YouTube-only.
#
# Do not add a YouTube `skip: ["dash", "hls"]` rule. That can remove the
# separate audio streams required for MP3 extraction.
YOUTUBE_POT_SERVER_HOME = os.getenv(
    "YOUTUBE_POT_SERVER_HOME",
    "/opt/bgutil-ytdlp-pot-provider/server",
)


def _is_youtube_url(url: str) -> bool:
    normalized = url.lower()
    return "youtube.com" in normalized or "youtu.be" in normalized


def _is_instagram_url(url: str) -> bool:
    normalized = url.lower()
    return "instagram.com" in normalized


_EXTRACTOR_ARGS_YOUTUBE: dict = {
    "youtube": {
        "player_client": ["mweb"],
    },
    "youtubepot-bgutilscript": {
        "server_home": [YOUTUBE_POT_SERVER_HOME],
    },
}
_EXTRACTOR_ARGS_GENERIC: dict = {}


def _get_extractor_args(url: str) -> dict:
    """Return YouTube-only extractor arguments when the URL is YouTube."""
    return _EXTRACTOR_ARGS_YOUTUBE if _is_youtube_url(url) else _EXTRACTOR_ARGS_GENERIC


def _get_runtime_options(url: str) -> dict:
    """Tell yt-dlp where the Deno runtime is for the YouTube POT provider."""
    if not _is_youtube_url(url):
        return {}

    return {
        "js_runtimes": {
            "deno": {
                "path": os.getenv("DENO_PATH", "/usr/local/bin/deno"),
            }
        }
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

# Prefer H.264 video + AAC audio. An .mp4 *container* alone does not guarantee
# mobile compatibility: it can still contain VP9/AV1/Opus streams.
QUALITY_FORMATS: dict[str, str] = {
    # Select the best available media.  Video is converted to a Telegram-safe
    # H.264/AAC MP4 after download, so do not restrict source codecs here.
    "best": "bv*[vcodec^=avc1]+ba[acodec^=mp4a]/bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b",
    "720": "bv*[height<=720][vcodec^=avc1]+ba[acodec^=mp4a]/bv*[height<=720][ext=mp4]+ba[ext=m4a]/bv*[height<=720]+ba/b[height<=720]/b",
    "480": "bv*[height<=480][vcodec^=avc1]+ba[acodec^=mp4a]/bv*[height<=480][ext=mp4]+ba[ext=m4a]/bv*[height<=480]+ba/b[height<=480]/b",
    "360": "bv*[height<=360][vcodec^=avc1]+ba[acodec^=mp4a]/bv*[height<=360][ext=mp4]+ba[ext=m4a]/bv*[height<=360]+ba/b[height<=360]/b",
    "144": "bv*[height<=144][vcodec^=avc1]+ba[acodec^=mp4a]/bv*[height<=144][ext=mp4]+ba[ext=m4a]/bv*[height<=144]+ba/b[height<=144]/b",
}

# Match yt-dlp's recommended audio-selection order, then convert with FFmpeg.
AUDIO_FORMAT = "ba/b"


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


def _extract_instagram_info_sync(url: str) -> dict:
    """Blocking Instagram metadata fallback using instaloader."""
    try:
        from services.instagram_downloader import fetch_instagram_info
    except ModuleNotFoundError as exc:
        raise yt_dlp.utils.DownloadError(
            "Instagram fallback requires the instaloader package."
        ) from exc

    info = fetch_instagram_info(url)
    if not info:
        raise yt_dlp.utils.DownloadError("Instagram fallback could not fetch metadata.")
    if not info.get("is_video", True):
        raise yt_dlp.utils.DownloadError("Instagram post does not contain a video.")

    return {
        "title": info.get("title") or "Instagram video",
        "uploader": info.get("uploader") or "Instagram",
        "duration": info.get("duration") or 0,
        "extractor_key": "Instagram",
        "filesize": None,
        "filesize_approx": None,
        "thumbnail": None,
        "formats": [],
    }


def _extract_info_sync(url: str) -> dict:
    """Blocking metadata fetch — no download."""
    cookies_file, temporary_cookies_file = _get_cookies_file(url)
    ydl_opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "skip_download": True,
        "extractor_args": _get_extractor_args(url),
        "nocheckcertificate": True,
        **_get_runtime_options(url),
    }
    if _is_youtube_url(url):
        # Smaller range requests can be more reliable on cloud-hosted IPs.
        ydl_opts["http_chunk_size"] = 10 * 1024 * 1024

    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError:
        if _is_instagram_url(url):
            logger.info("yt-dlp Instagram metadata failed; trying instaloader fallback.")
            return _extract_instagram_info_sync(url)
        raise
    finally:
        _remove_temp_cookies_file(cookies_file, temporary_cookies_file)


def get_video_dimensions(video_path: Path) -> tuple[int | None, int | None]:
    """Return encoded video dimensions for Telegram metadata."""
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x", str(video_path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        raw = completed.stdout.strip()
        width_text, height_text = raw.split("x", 1)
        width, height = int(width_text), int(height_text)
        if width > 0 and height > 0:
            return width, height
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError):
        logger.warning("Could not read video dimensions for %s", video_path.name)
    return None, None


def _telegram_output_path(video_path: Path) -> Path:
    """Build a safe MP4 name even when the source title contains long Unicode text."""
    suffix = ".telegram.mp4"
    # Linux limits a single filename component to 255 bytes, not characters.
    # Media titles in Arabic/Russian can exceed that after adding the suffix.
    stem = video_path.stem
    while stem and len((stem + suffix).encode("utf-8")) > 220:
        stem = stem[:-1]
    return video_path.with_name(f"{stem or 'video'}{suffix}")


def _probe_streams(video_path: Path) -> dict:
    """Read only the stream facts needed to choose remuxing or transcoding."""
    command = [
        "ffprobe", "-v", "error", "-show_entries",
        (
            "stream=codec_type,codec_name,pix_fmt,width,height,"
            "sample_aspect_ratio:stream_tags=rotate:stream_side_data=rotation"
        ),
        "-of", "json", str(video_path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout or "{}")
    except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError):
        return {}


def _has_rotation(video_stream: dict) -> bool:
    """Return True for a non-zero rotation in stream tags or side-data."""
    rotate_value = (video_stream.get("tags") or {}).get("rotate")
    try:
        if rotate_value is not None and int(float(rotate_value)) % 360:
            return True
    except (TypeError, ValueError):
        return True

    for side_data in video_stream.get("side_data_list") or []:
        rotation = side_data.get("rotation")
        try:
            if rotation is not None and int(float(rotation)) % 360:
                return True
        except (TypeError, ValueError):
            return True
    return False


def _can_remux_for_telegram(video_path: Path) -> bool:
    """Whether the file is already H.264/AAC and only needs metadata cleanup."""
    data = _probe_streams(video_path)
    streams = data.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not video:
        return False

    video_ok = (
        video.get("codec_name") == "h264"
        and video.get("pix_fmt") in {"yuv420p", "yuvj420p"}
        and video.get("sample_aspect_ratio") in {None, "N/A", "1:1"}
        and not _has_rotation(video)
    )
    audio_ok = audio is None or audio.get("codec_name") == "aac"
    return video_ok and audio_ok


def _run_ffmpeg(command: list[str], source_name: str) -> None:
    """Run FFmpeg and preserve a useful reason in Railway logs on failure."""
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg is required to prepare videos for Telegram.") from exc

    if completed.returncode == 0:
        return

    details = (completed.stderr or completed.stdout or "").strip()
    if not details:
        details = f"FFmpeg exited with status {completed.returncode}."
    logger.warning("FFmpeg failed for %s: %s", source_name, details[-1000:])
    raise RuntimeError(details[-500:])


def _remux_for_telegram(video_path: Path, output_path: Path) -> None:
    """Fast metadata cleanup without decoding/re-encoding a large video."""
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-map_metadata", "-1", "-map_chapters", "-1",
        "-c", "copy",
        "-movflags", "+faststart",
        "-metadata:s:v:0", "rotate=0",
        str(output_path),
    ]
    _run_ffmpeg(command, video_path.name)


def _transcode_for_telegram(video_path: Path, output_path: Path) -> None:
    """Create a mobile-safe H.264/AAC MP4 using low Railway memory/CPU usage."""
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-map_metadata", "-1", "-map_chapters", "-1",
        # Keep displayed aspect ratio and produce square pixels. ffmpeg's
        # normal autorotation bakes the orientation into the video first.
        "-vf", "scale=trunc(ih*dar/2)*2:trunc(ih/2)*2:flags=bicubic,setsar=1",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24",
        "-pix_fmt", "yuv420p", "-tag:v", "avc1",
        "-c:a", "aac", "-b:a", "128k",
        # One encoder thread prevents a single large video from exhausting a
        # small Railway container. The old code used FFmpeg defaults.
        "-threads", "1",
        "-max_muxing_queue_size", "1024",
        "-movflags", "+faststart",
        "-metadata:s:v:0", "rotate=0",
        str(output_path),
    ]
    _run_ffmpeg(command, video_path.name)


def _normalise_video_for_telegram(video_path: Path) -> Path:
    """
    Produce a Telegram-friendly MP4 without turning landscape videos square.

    Ready H.264/AAC videos are remuxed only, which is very fast and avoids the
    memory spike that made downloads above 50 MB fail. Only unusual source
    codecs, rotation, or non-square pixels are fully transcoded.
    """
    output_path = _telegram_output_path(video_path)
    output_path.unlink(missing_ok=True)

    try:
        if _can_remux_for_telegram(video_path):
            _remux_for_telegram(video_path, output_path)
        else:
            _transcode_for_telegram(video_path, output_path)
    except RuntimeError as transcode_error:
        # Large AV1/VP9/4K files can be killed by a small cloud container during
        # full transcode. Sending a cleaned MP4 is better than failing the whole
        # download. The normal format selector already prefers H.264/AAC.
        logger.warning(
            "Full Telegram conversion failed for %s; trying a fast remux instead: %s",
            video_path.name,
            transcode_error,
        )
        output_path.unlink(missing_ok=True)
        try:
            _remux_for_telegram(video_path, output_path)
        except RuntimeError as remux_error:
            raise RuntimeError(
                "FFmpeg could not prepare the video. "
                f"Details: {str(remux_error)[:250]}"
            ) from remux_error

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("FFmpeg finished without creating the Telegram video.")

    width, height = get_video_dimensions(output_path)
    if not width or not height:
        raise RuntimeError("The final Telegram video has no valid dimensions.")

    video_path.unlink(missing_ok=True)
    return output_path


def _extract_audio_from_video(video_path: Path, output_dir: Path) -> Path:
    """Extract a Telegram-friendly MP3 from a fallback video download."""
    output_path = output_dir / "media.mp3"
    output_path.unlink(missing_ok=True)
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-vn", "-codec:a", "libmp3lame", "-b:a", "192k",
        str(output_path),
    ]
    _run_ffmpeg(command, video_path.name)
    video_path.unlink(missing_ok=True)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("FFmpeg finished without creating the Instagram audio.")
    return output_path


def _download_instagram_fallback_sync(
    url: str,
    output_path: Path,
    audio_only: bool,
) -> tuple[Path, dict]:
    """Download Instagram media with instaloader when yt-dlp cannot."""
    try:
        from services.instagram_downloader import download_instagram
    except ModuleNotFoundError as exc:
        raise yt_dlp.utils.DownloadError(
            "Instagram fallback requires the instaloader package."
        ) from exc

    downloaded = download_instagram(url, output_path)
    if not downloaded:
        raise yt_dlp.utils.DownloadError("Instagram fallback could not download media.")

    info = _extract_instagram_info_sync(url)
    if audio_only:
        return _extract_audio_from_video(downloaded, output_path), info
    return _normalise_video_for_telegram(downloaded), info


def _download_sync(
    url: str,
    output_path: Path,
    format_str: str,
    audio_only: bool,
    progress_hook: Callable[[dict], None],
) -> tuple[Path, dict]:
    """Blocking download — run inside a thread executor."""
    cookies_file, temporary_cookies_file = _get_cookies_file(url)
    outtmpl = str(output_path / "media.%(ext)s")

    postprocessors = []
    if audio_only:
        postprocessors.append({
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        })

    ydl_opts: dict = {
        "format": format_str,
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [progress_hook],
        "postprocessors": postprocessors,
        "nocheckcertificate": False,
        "geo_bypass": True,
        "extractor_args": _get_extractor_args(url),
        "noplaylist": True,
        # Retry logic for flaky connections
        "retries": 5,
        "fragment_retries": 5,
        "file_access_retries": 3,
        **_get_runtime_options(url),
    }
    if _is_youtube_url(url):
        # Smaller range requests can be more reliable on cloud-hosted IPs.
        ydl_opts["http_chunk_size"] = 10 * 1024 * 1024

    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file
    if not audio_only:
        ydl_opts["merge_output_format"] = "mp4"

    try:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError:
            if _is_instagram_url(url):
                logger.info("yt-dlp Instagram download failed; trying instaloader fallback.")
                return _download_instagram_fallback_sync(url, output_path, audio_only)
            raise

        # FFmpegExtractAudio should leave a final MP3. Do not pick the largest
        # file: for some YouTube downloads that can be the pre-conversion source.
        if audio_only:
            mp3_files = list(output_path.glob("*.mp3"))
            if not mp3_files:
                raise FileNotFoundError(
                    "Audio download completed, but FFmpeg did not create an MP3 file."
                )
            return max(mp3_files, key=lambda item: item.stat().st_mtime), info

        # Only select complete media files, never .part fragments.
        candidates = [
            f for f in output_path.iterdir()
            if f.is_file() and f.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}
        ]
        if not candidates:
            raise FileNotFoundError("Download completed but output video was not found.")

        found = max(candidates, key=lambda item: item.stat().st_size)
        return _normalise_video_for_telegram(found), info
    finally:
        _remove_temp_cookies_file(cookies_file, temporary_cookies_file)


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
    completed_file: Path | None = None

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
            completed_file = None
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

        completed_file = file_path
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
        if completed_file is None:
            cleanup_session(session_dir)


def cancel_download(user_id: int) -> bool:
    event = _cancel_flags.get(user_id)
    if event:
        event.set()
        return True
    return False


def cleanup_session(path: Path | None) -> None:
    if not path:
        return

    try:
        download_root = settings.download_path.resolve()
        target = path.resolve()

        if target == download_root or download_root not in target.parents:
            logger.warning("Refusing cleanup outside download directory: %s", path)
            return

        if path.is_dir():
            shutil.rmtree(path)
            return

        if path.exists():
            parent = path.parent
            path.unlink(missing_ok=True)
            if parent != settings.download_path and parent.exists() and not any(parent.iterdir()):
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
