"""
handlers/callbacks.py – Inline keyboard callback handlers (quality selection, cancel)
"""
from __future__ import annotations

import logging

from aiogram import Bot, Router, F
from aiogram.types import CallbackQuery

from utils import extract_url_from_text, is_valid_url

logger = logging.getLogger(__name__)
router = Router(name="callbacks")


@router.callback_query(F.data.startswith("quality:"))
async def cb_quality(callback: CallbackQuery, bot: Bot) -> None:
    """
    Callback data format: quality:<quality>:<url>
    quality is one of: best, 720, 480, 360, 144, audio
    """
    await callback.answer()

    parts = (callback.data or "").split(":", 2)
    if len(parts) < 3:
        await callback.message.answer("❌ Invalid selection.")  # type: ignore[union-attr]
        return

    _, quality, raw_url = parts
    url = extract_url_from_text(raw_url) or raw_url.strip()

    if not is_valid_url(url):
        await callback.message.answer("❌ The URL in this request is no longer valid.")  # type: ignore[union-attr]
        return

    audio_only = quality == "audio"
    effective_quality = "best" if audio_only else quality

    # Remove the quality picker message
    try:
        await callback.message.delete()  # type: ignore[union-attr]
    except Exception:
        pass

    # Delegate to the download flow (imported here to avoid circular imports)
    from handlers.downloader_handler import _run_download

    await _run_download(
        message=callback.message,  # type: ignore[arg-type]
        bot=bot,
        url=url,
        quality=effective_quality,
        audio_only=audio_only,
    )


@router.callback_query(F.data.startswith("cancel:"))
async def cb_cancel(callback: CallbackQuery) -> None:
    await callback.answer("Cancelling…")

    from services import cancel_download

    user_id = callback.from_user.id
    cancel_download(user_id)

    try:
        await callback.message.edit_text("🛑 Download cancelled.")  # type: ignore[union-attr]
    except Exception:
        pass
