"""
handlers/downloader_handler.py – /download, /audio commands + plain URL messages.

Business rule: NO download logic lives here.
All media work is delegated to services/downloader.py.
"""
from __future__ import annotations

import logging

import aiofiles
from aiogram import Bot, Router, F
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    FSInputFile,
    Message,
)

from services import download_media, fetch_info, cleanup_session, stats
from utils import (
    is_valid_url,
    detect_platform,
    extract_url_from_text,
    format_duration,
    truncate,
    rate_limiter,
)
from handlers.common import quality_keyboard, cancel_keyboard

logger = logging.getLogger(__name__)
router = Router(name="downloader")

# Track users currently in a download so we don't start a second one
_active_downloads: set[int] = set()


# ── Shared download orchestration ─────────────────────────────────────────────

async def _run_download(
    message: Message,
    bot: Bot,
    url: str,
    quality: str = "best",
    audio_only: bool = False,
) -> None:
    """Core download flow shared by all entry points."""
    user_id = message.from_user.id  # type: ignore[union-attr]

    # Rate limit check
    allowed, reason = await rate_limiter.check(user_id)
    if not allowed:
        await message.answer(reason)
        return

    # Guard: one download at a time per user
    if user_id in _active_downloads:
        await message.answer(
            "⚠️ You already have an active download. "
            "Use /cancel to stop it first."
        )
        return

    _active_downloads.add(user_id)
    status_msg = await message.answer("🔍 Fetching media info…")

    try:
        # ── Fetch metadata first ───────────────────────────────────────────
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

        # ── Progress callback ──────────────────────────────────────────────
        async def on_progress(msg_text: str) -> None:
            try:
                await status_msg.edit_text(msg_text)
            except Exception:
                pass  # Ignore edit rate-limit errors

        # ── Download ──────────────────────────────────────────────────────
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

        # ── Upload to Telegram ─────────────────────────────────────────────
        await status_msg.edit_text("📤 Uploading to Telegram…")
        caption = (
            f"🐿️ *{truncate(result.info.title, 60)}*\n"  # type: ignore[union-attr]
            f"👤 {result.info.uploader}  |  ⏱ {result.info.duration}  |  📦 {result.info.file_size_str}"  # type: ignore[union-attr]
        )

        file_input = FSInputFile(result.file_path)

        try:
            if audio_only or result.file_path.suffix.lower() == ".mp3":
                await bot.send_audio(
                    chat_id=message.chat.id,
                    audio=file_input,
                    caption=caption,
                    parse_mode="Markdown",
                    title=result.info.title,  # type: ignore[union-attr]
                    performer=result.info.uploader,  # type: ignore[union-attr]
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
                "The file may be too large for Telegram (50 MB limit)."
            )
            await stats.record_failure(user_id)

    finally:
        _active_downloads.discard(user_id)
        if result is not None and result.file_path:  # type: ignore[possibly-undefined]
            cleanup_session(result.file_path)


# ── /download command ──────────────────────────────────────────────────────────

@router.message(Command("download"))
async def cmd_download(message: Message, bot: Bot) -> None:
    text = message.text or ""
    parts = text.split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "📎 Usage: `/download <URL>`\n\n"
            "Example:\n`/download https://youtu.be/dQw4w9WgXcQ`",
            parse_mode="Markdown",
        )
        return

    url = extract_url_from_text(parts[1])
    if not url:
        await message.answer("❌ Invalid URL. Please provide a valid video link.")
        return

    # Show quality picker
    await message.answer(
        f"🎚 Choose your preferred quality for:\n`{truncate(url, 60)}`",
        parse_mode="Markdown",
        reply_markup=quality_keyboard(url),
    )


# ── /audio command ─────────────────────────────────────────────────────────────

@router.message(Command("audio"))
async def cmd_audio(message: Message, bot: Bot) -> None:
    text = message.text or ""
    parts = text.split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "🎵 Usage: `/audio <URL>`\n\n"
            "Example:\n`/audio https://youtu.be/dQw4w9WgXcQ`",
            parse_mode="Markdown",
        )
        return

    url = extract_url_from_text(parts[1])
    if not url:
        await message.answer("❌ Invalid URL. Please provide a valid video link.")
        return

    await _run_download(message, bot, url, audio_only=True)


# ── Plain URL messages ─────────────────────────────────────────────────────────

@router.message(F.text & F.text.regexp(r"https?://\S+"))
async def handle_url_message(message: Message, bot: Bot) -> None:
    """Auto-detect URLs dropped directly into the chat."""
    text = message.text or ""
    url = extract_url_from_text(text)

    if not url or not is_valid_url(url):
        return  # Ignore; let other handlers deal with it

    await message.answer(
        f"🔗 *Link detected!*\n\n`{truncate(url, 70)}`\n\n"
        "Choose what you'd like to do:",
        parse_mode="Markdown",
        reply_markup=quality_keyboard(url),
    )
