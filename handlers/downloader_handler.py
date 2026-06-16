"""
handlers/downloader_handler.py – /download, /audio commands + plain URL messages.
"""
from __future__ import annotations

import logging

from aiogram import Bot, Router, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

from services import download_media, fetch_info, cleanup_session, stats
from services.telethon_uploader import upload_large_file
from utils import (
    is_valid_url,
    detect_platform,
    extract_url_from_text,
    truncate,
    rate_limiter,
)
from utils.schedule import is_open, get_status_message
from handlers.common import quality_keyboard

logger = logging.getLogger(__name__)
router = Router(name="downloader")

_active_downloads: set[int] = set()

SMALL_FILE_LIMIT = 50 * 1024 * 1024  # 50MB


async def _run_download(
    message: Message,
    bot: Bot,
    url: str,
    quality: str = "best",
    audio_only: bool = False,
) -> None:
    """Core download flow shared by all entry points."""
    user_id = message.from_user.id  # type: ignore[union-attr]

    # ── Working hours check ───────────────────────────────────────
    if not is_open():
        await message.answer(get_status_message())
        return

    # ── Rate limit check ──────────────────────────────────────────
    allowed, reason = await rate_limiter.check(user_id)
    if not allowed:
        await message.answer(reason)
        return

    # ── One download at a time per user ───────────────────────────
    if user_id in _active_downloads:
        await message.answer(
            "⚠️ You already have an active download. "
            "Use /cancel to stop it first."
        )
        return

    _active_downloads.add(user_id)
    status_msg = await message.answer("🔍 Fetching media info…")
    result = None

    try:
        # ── Fetch metadata ────────────────────────────────────────
        try:
            info = await fetch_info(url)
        except ValueError as exc:
            await status_msg.edit_text(f"❌ Could not load media info:\n{exc}")
            await stats.record_failure(user_id)
            return

        platform = detect_platform(url)
        mode = "🎵 Audio" if audio_only else f"📹 {quality.upper()}"
        preview = (
            f"📋 *{truncate(info.title, 80)}*\n"
            f"👤 {info.uploader}\n"
            f"⏱ {info.duration}\n"
            f"🌐 {platform}\n\n"
            f"⬇️ Starting download… _{mode}_"
        )
        await status_msg.edit_text(preview, parse_mode="Markdown")

        # ── Progress callback ─────────────────────────────────────
        async def on_progress(msg_text: str) -> None:
            try:
                await status_msg.edit_text(msg_text)
            except Exception:
                pass

        # ── Download ──────────────────────────────────────────────
        result = await download_media(
            url=url,
            user_id=user_id,
            quality=quality,
            audio_only=audio_only,
            progress_callback=on_progress,
        )

        if not result.success or not result.file_path:
            await status_msg.edit_text(result.error or "❌ Download failed.")
            await stats.record_failure(user_id)
            return

        # ── Upload to Telegram ────────────────────────────────────
        await status_msg.edit_text("📤 Uploading to Telegram…")
        caption = (
            f"🐿️ *{truncate(result.info.title, 60)}*\n"  # type: ignore[union-attr]
            f"👤 {result.info.uploader}  |  "  # type: ignore[union-attr]
            f"⏱ {result.info.duration}  |  "  # type: ignore[union-attr]
            f"📦 {result.info.file_size_str}"  # type: ignore[union-attr]
        )

        actual_size = result.file_path.stat().st_size

        try:
            if actual_size > SMALL_FILE_LIMIT:
                # Large file — use Telethon
                await upload_large_file(
                    chat_id=message.chat.id,
                    file_path=result.file_path,
                    caption=caption,
                    is_audio=audio_only,
                )
            else:
                # Small file — use aiogram
                file_input = FSInputFile(result.file_path)
                if audio_only or result.file_path.suffix.lower() == ".mp3":
                    await bot.send_audio(
                        chat_id=message.chat.id,
                        audio=file_input,
                        caption=caption,
                        parse_mode="Markdown",
                        title=result.info.title,
                        performer=result.info.uploader,
                    )
                else:
                    await bot.send_video(
                        chat_id=message.chat.id,
                        video=file_input,
                        caption=caption,
                        parse_mode="Markdown",
                        supports_streaming=True,
                    )

            await status_msg.delete()
            await stats.record_success(user_id)

        except Exception as exc:
            logger.error("Upload error: %s", exc)
            await status_msg.edit_text(
                f"❌ Upload failed: {exc}\n\n"
                "The file may be too large or Telegram timed out."
            )
            await stats.record_failure(user_id)