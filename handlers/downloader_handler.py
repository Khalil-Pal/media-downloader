"""
handlers/downloader_handler.py – /download, /audio commands + plain URL messages.

Files up to 50 MB are sent with the normal Bot API (aiogram).
Files larger than 50 MB are sent by the SAME bot account through Telethon/MTProto.
This file deliberately does not require Telegram's Local Bot API server.
"""
from __future__ import annotations

import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

from handlers.common import quality_keyboard
from services import cleanup_session, download_media, fetch_info, stats
from services.downloader import get_video_dimensions
from services.telethon_uploader import upload_large_file
from services.user_store import get_user_lang_or_default, get_user_mode_or_default, register_user
from utils import (
    detect_platform,
    extract_url_from_text,
    is_valid_url,
    rate_limiter,
    truncate,
)
from utils.i18n import t
from handlers.language import ensure_mode_selected

logger = logging.getLogger(__name__)
router = Router(name="downloader")

_active_downloads: set[int] = set()

# Telegram's HTTP Bot API upload limit. Larger files use Telethon below.
SMALL_FILE_LIMIT = 50 * 1024 * 1024


async def _run_download(message: Message, bot: Bot, url: str, quality: str = "best", audio_only: bool = False) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]
    lang = await get_user_lang_or_default(user_id)

    allowed, reason = await rate_limiter.check(user_id)
    if not allowed:
        await message.answer(reason)
        return

    if user_id in _active_downloads:
        await message.answer(t(lang, "active_download_warning"))
        return

    _active_downloads.add(user_id)
    status_msg = await message.answer(t(lang, "fetching_info"))
    result = None

    try:
        try:
            info = await fetch_info(url)
        except ValueError as exc:
            await status_msg.edit_text(t(lang, "info_error", error=str(exc)))
            await stats.record_failure(user_id)
            return

        platform = detect_platform(url)
        mode = "Audio" if audio_only else quality.upper()
        preview = (
            f"<b>{escape(truncate(info.title, 80))}</b>\n"
            f"{escape(info.uploader)}\n"
            f"{escape(info.duration)}\n"
            f"{escape(platform)}\n\n"
            f"Downloading {escape(mode)}"
        )
        await status_msg.edit_text(preview, parse_mode="HTML")

        async def on_progress(msg_text: str) -> None:
            try:
                await status_msg.edit_text(msg_text)
            except Exception as exc:
                # Telegram may reject an edit when the visible text did not change.
                logger.debug("Could not update download progress message: %s", exc)

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
            f"<b>{escape(truncate(result.info.title, 60))}</b>\n"
            f"{escape(result.info.uploader)}  |  "
            f"{escape(result.info.duration)}  |  "
            f"{escape(result.info.file_size_str)}"
        )

        actual_size = result.file_path.stat().st_size

        try:
            if actual_size > SMALL_FILE_LIMIT:
                # IMPORTANT: Telethon is authenticated with BOT_TOKEN in
                # services/telethon_uploader.py. This sends from the bot,
                # not from the owner's personal Telegram account.
                width: int | None = None
                height: int | None = None
                duration: int | None = None

                if not audio_only:
                    width, height = get_video_dimensions(result.file_path)
                    duration = result.info.duration_seconds if result.info else None
                    if not width or not height:
                        logger.debug(
                            "Could not determine video dimensions for large upload: %s",
                            result.file_path.name,
                        )

                await upload_large_file(
                    chat_id=message.chat.id,
                    file_path=result.file_path,
                    caption=caption,
                    is_audio=audio_only,
                    width=width,
                    height=height,
                    duration=duration,
                )
            else:
                file_input = FSInputFile(result.file_path)

                if audio_only or result.file_path.suffix.lower() == ".mp3":
                    await bot.send_audio(
                        chat_id=message.chat.id,
                        audio=file_input,
                        caption=caption,
                        parse_mode="HTML",
                        title=result.info.title,
                        performer=result.info.uploader,
                    )
                else:
                    # Explicit dimensions prevent Telegram mobile clients from
                    # incorrectly guessing the display frame.
                    width, height = get_video_dimensions(result.file_path)
                    video_kwargs: dict[str, int] = {}
                    if width and height:
                        video_kwargs = {"width": width, "height": height}

                    await bot.send_video(
                        chat_id=message.chat.id,
                        video=file_input,
                        caption=caption,
                        parse_mode="HTML",
                        supports_streaming=True,
                        **video_kwargs,
                    )

            await status_msg.delete()
            await stats.record_success(user_id)

        except Exception as exc:
            logger.exception("Upload error for %s", result.file_path.name)
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


@router.message(Command("download"))
async def cmd_download(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]
    await register_user(user_id)
    lang = await get_user_lang_or_default(user_id)
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
    await register_user(user_id)
    lang = await get_user_lang_or_default(user_id)
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
    await register_user(message.from_user.id)  # type: ignore[union-attr]
    lang = await get_user_lang_or_default(message.from_user.id)  # type: ignore[union-attr]
    if not await ensure_mode_selected(message, lang):
        return

    url = extract_url_from_text(message.text or "")

    if not url or not is_valid_url(url):
        return

    mode = await get_user_mode_or_default(message.from_user.id)  # type: ignore[union-attr]
    if mode == "converter":
        await message.answer(t(lang, "mode_need_downloader"))
        return

    await message.answer(
        t(lang, "link_detected", url=truncate(url, 70)),
        reply_markup=quality_keyboard(url, lang=lang),
    )
