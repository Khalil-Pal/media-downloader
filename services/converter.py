"""
services/converter.py - File conversion logic.

This module is the only place that shells out to ffmpeg/LibreOffice.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

CONVERSION_TIMEOUT_SECONDS = 120
IMAGE_FORMATS = {"png", "jpg", "jpeg", "webp"}
OFFICE_TO_PDF = {"docx", "xlsx", "pptx"}
MARKDOWN_FORMATS = {"md", "markdown"}
AUDIO_VIDEO_EXTENSIONS = {
    "3gp", "aac", "aiff", "ape", "avi", "flac", "flv", "m4a", "m4v",
    "mkv", "mov", "mp3", "mp4", "mpeg", "mpg", "oga", "ogg", "opus",
    "ts", "wav", "webm", "wma", "wmv",
}

_conversion_semaphore = asyncio.Semaphore(settings.max_concurrent_conversions)


@dataclass(frozen=True)
class ConversionOption:
    target_format: str
    label: str


class ConversionError(Exception):
    """Known conversion failure mapped to a translated user-facing message."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _extension(filename: str | None) -> str:
    if not filename:
        return ""
    suffix = Path(filename).suffix.lower().lstrip(".")
    return "jpg" if suffix == "jpeg" else suffix


def supported_targets(filename: str | None, mime_type: str | None = None) -> list[ConversionOption]:
    """Return Tier 1 target formats for a file name / MIME type."""
    ext = _extension(filename)
    mime = (mime_type or "").lower()

    if ext in IMAGE_FORMATS:
        source = "jpg" if ext == "jpeg" else ext
        return [
            ConversionOption(target, f"-> {target.upper()}")
            for target in ("png", "jpg", "webp")
            if target != source
        ]

    if ext in OFFICE_TO_PDF or ext in MARKDOWN_FORMATS:
        return [ConversionOption("pdf", "-> PDF")]

    if (mime.startswith("audio/") or mime.startswith("video/") or ext in AUDIO_VIDEO_EXTENSIONS) and ext != "mp3":
        return [ConversionOption("mp3", "-> MP3")]

    return []


def create_conversion_session() -> Path:
    session_dir = settings.download_path / str(uuid.uuid4())
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def cleanup_conversion_session(path: Path | None) -> None:
    if not path:
        return

    try:
        download_root = settings.download_path.resolve()
        target = path.resolve()
        if target == download_root or download_root not in target.parents:
            logger.warning("Refusing conversion cleanup outside download directory: %s", path)
            return
        if target.exists():
            shutil.rmtree(target)
    except OSError as exc:
        logger.warning("Conversion cleanup failed: %s", exc)


def friendly_conversion_error(exc: Exception) -> str:
    if isinstance(exc, ConversionError):
        return {
            "too_large": "conversion_error_too_large",
            "timeout": "conversion_error_timeout",
            "unsupported": "conversion_error_unsupported",
            "missing_tool": "conversion_error_missing_tool",
            "tool_failed": "conversion_error_failed",
            "empty_output": "conversion_error_failed",
        }.get(exc.code, "conversion_error_failed")
    return "conversion_error_failed"


async def convert_file(input_path: Path, target_format: str, mime_type: str | None = None) -> Path:
    """Convert one local file to one supported Tier 1 target format."""
    target = target_format.lower().lstrip(".")
    if target == "jpeg":
        target = "jpg"

    async with _conversion_semaphore:
        if input_path.stat().st_size > settings.max_convert_file_size_bytes:
            raise ConversionError("too_large")

        targets = {option.target_format for option in supported_targets(input_path.name, mime_type)}
        if target not in targets:
            raise ConversionError("unsupported")

        source_ext = _extension(input_path.name)
        output_path = input_path.with_name(f"{input_path.stem}.{target}")

        if target == "mp3":
            await _convert_to_mp3(input_path, output_path)
        elif source_ext in IMAGE_FORMATS and target in IMAGE_FORMATS:
            await asyncio.to_thread(_convert_image, input_path, output_path, target)
        elif source_ext in OFFICE_TO_PDF and target == "pdf":
            output_path = await _convert_office_to_pdf(input_path)
        elif source_ext in MARKDOWN_FORMATS and target == "pdf":
            await asyncio.to_thread(_convert_markdown_to_pdf, input_path, output_path)
        else:
            raise ConversionError("unsupported")

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise ConversionError("empty_output")
        return output_path


async def _run_process(command: list[str], tool_name: str) -> None:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        logger.warning("%s is not installed or not on PATH.", tool_name)
        raise ConversionError("missing_tool") from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=CONVERSION_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.communicate()
        logger.warning("%s conversion timed out.", tool_name)
        raise ConversionError("timeout") from exc

    if process.returncode != 0:
        details = (stderr or stdout or b"").decode("utf-8", errors="replace").strip()
        logger.warning("%s conversion failed: %s", tool_name, details[-1000:])
        raise ConversionError("tool_failed")


async def _convert_to_mp3(input_path: Path, output_path: Path) -> None:
    output_path.unlink(missing_ok=True)
    await _run_process(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(input_path),
            "-vn", "-codec:a", "libmp3lame", "-b:a", "192k",
            str(output_path),
        ],
        "ffmpeg",
    )


def _convert_image(input_path: Path, output_path: Path, target_format: str) -> None:
    try:
        from PIL import Image
    except ModuleNotFoundError as exc:
        raise ConversionError("missing_tool") from exc

    output_path.unlink(missing_ok=True)
    with Image.open(input_path) as image:
        if target_format == "jpg":
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                rgba = image.convert("RGBA")
                background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                background.alpha_composite(rgba)
                converted = background.convert("RGB")
            else:
                converted = image.convert("RGB")
            converted.save(output_path, format="JPEG", quality=95, optimize=True)
            return

        save_format = "PNG" if target_format == "png" else "WEBP"
        image.save(output_path, format=save_format)


async def _convert_office_to_pdf(input_path: Path) -> Path:
    output_path = input_path.with_suffix(".pdf")
    output_path.unlink(missing_ok=True)
    await _run_process(
        [
            "soffice", "--headless", "--convert-to", "pdf",
            "--outdir", str(input_path.parent),
            str(input_path),
        ],
        "LibreOffice",
    )
    return output_path


def _convert_markdown_to_pdf(input_path: Path, output_path: Path) -> None:
    try:
        import markdown
        from weasyprint import HTML
    except ModuleNotFoundError as exc:
        raise ConversionError("missing_tool") from exc

    output_path.unlink(missing_ok=True)
    source = input_path.read_text(encoding="utf-8", errors="replace")
    body = markdown.markdown(source, extensions=["extra", "sane_lists", "tables"])
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>body{font-family:Arial,sans-serif;line-height:1.5;margin:32px;}"
        "pre,code{font-family:Consolas,monospace;} img{max-width:100%;}</style>"
        "</head><body>" + body + "</body></html>"
    )
    HTML(string=html, base_url=str(input_path.parent)).write_pdf(str(output_path))

