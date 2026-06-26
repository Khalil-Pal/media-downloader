"""
handlers/downloader_handler.py – /download, /audio commands + plain URL messages.
"""
from __future__ import annotations

import logging

from aiogram import Bot, Router, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

from services import download_media, fetch_info, cleanup_session, stats
from services.user_store import (get_user_lang_or_default)
from utils import (
    is_valid_url,
    detect_platform,
    extract_url_from_text,
    truncate,
    rate_limiter,
)
from utils.i18n import t
from handlers.common import quality_keyboard

logger = logging.getLogger(__name__)
router = Router(name="downloader")

_active_downloads: set[int] = set()

# Files at or below this size upload via the Bot API (aiogram FSInputFile).
# Files above it route through Telethon (MTProto) which supports up to 2 GB.
SMALL_FILE_LIMIT = 50 * 1024 * 1024  # 50 MB


async def _run_download(
    message: Message,
    bot: Bot,
    url: str,
    quality: str = "best",
    audio_only: bool = False,
) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]
    lang = get_user_lang_or_default(user_id)
    # ── Rate limit check ──────────────────────────────────────────────────
    allowed, reason = await rate_limiter.check(user_id)
    if not allowed:
        await message.answer(reason)
        return

    # ── One download at a time per user ───────────────────────────────────
    if user_id in _active_downloads:
        await message.answer(t(lang, "active_download_warning"))
        return

    _active_downloads.add(user_id)
    status_msg = await message.answer(t(lang, "fetching_info"))
    result = None

    try:
        # ── Metadata fetch ────────────────────────────────────────────────
        try:
            info = await fetch_info(url)
        except ValueError as exc:
            await status_msg.edit_text(t(lang, "info_error", error=str(exc)))
            await stats.record_failure(user_id)
            return

        platform = detect_platform(url)
        mode = "🎵 Audio" if audio_only else f"📹 {quality.upper()}"
        preview = (
            f"📋 *{truncate(info.title, 80)}*\n"
            f"👤 {info.uploader}\n"
            f"⏱ {info.duration}\n"
            f"🌐 {platform}\n\n"
            f"⬇️ _{mode}_"
        )
        await status_msg.edit_text(preview, parse_mode="Markdown")

        # ── Progress callback (edits the status message in-place) ─────────
        async def on_progress(msg_text: str) -> None:
            try:
                await status_msg.edit_text(msg_text)
            except Exception:
                pass

        # ── Download ──────────────────────────────────────────────────────
        result = await download_media(
            url=url,
            user_id=user_id,
            quality=quality,
            audio_only=audio_only,
            progress_callback=on_progress,
        )

        if not result.success or not result.file_path:
            await status_msg.edit_text(result.error or t(lang, "download_failed"))
            await stats.record_failure(user_id)
            return

        await status_msg.edit_text(t(lang, "uploading"))

        caption = (
            f"🐿️ *{truncate(result.info.title, 60)}*\n"
            f"👤 {result.info.uploader}  |  "
            f"⏱ {result.info.duration}  |  "
            f"📦 {result.info.file_size_str}"
        )

        actual_size = result.file_path.stat().st_size

        # ── Upload ────────────────────────────────────────────────────────
        try:
            if actual_size > SMALL_FILE_LIMIT:

                from services.telethon_uploader import upload_large_file
                await upload_large_file(
                    chat_id=message.chat.id,
                    file_path=result.file_path,
                    caption=caption,
                    is_audio=audio_only,
                )
            else:

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
            await status_msg.edit_text(t(lang, "upload_failed", error=str(exc)))
            await stats.record_failure(user_id)

    except Exception as exc:
        logger.exception("Unexpected error in _run_download")
        await status_msg.edit_text(t(lang, "unexpected_error", error=str(exc)))
        await stats.record_failure(user_id)

    finally:
        _active_downloads.discard(user_id)
        if result is not None and result.file_path:
            cleanup_session(result.file_path)


# ── Command handlers ──────────────────────────────────────────────────────────

@router.message(Command("download"))
async def cmd_download(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]
    lang = get_user_lang_or_default(user_id)
    text = message.text or ""
    parts = text.split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip():
        await message.answer(t(lang, "download_usage"), parse_mode="Markdown")
        return

    url = extract_url_from_text(parts[1])
    if not url:
        await message.answer(t(lang, "invalid_url"))
        return

    await message.answer(
        t(lang, "choose_quality", url=truncate(url, 60)),
        parse_mode="Markdown",
        reply_markup=quality_keyboard(url, lang=lang),
    )

    @router.message(Command("audio"))
    async def cmd_audio(message: Message, bot: Bot) -> None:
        user_id = message.from_user.id  # type: ignore[union-attr]
        lang = get_user_lang_or_default(user_id)
        text = message.text or ""
        parts = text.split(maxsplit=1)

        if len(parts) < 2 or not parts[1].strip():
            await message.answer(t(lang, "audio_usage"), parse_mode="Markdown")
            return

        url = extract_url_from_text(parts[1])
        if not url:
            await message.answer(t(lang, "invalid_url"))
        return

    await _run_download(message, bot, url, audio_only=True)


@router.message(F.text & F.text.regexp(r"https?://\S+"))
async def handle_url_message(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]
    lang = get_user_lang_or_default(user_id)
    text = message.text or ""
    url = extract_url_from_text(text)

    if not url or not is_valid_url(url):
        return

    await message.answer(
          t(lang, "link_detected", url=truncate(url, 70)),
        reply_markup=quality_keyboard(url, lang=lang),
    )