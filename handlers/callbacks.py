"""
handlers/callbacks.py – Inline keyboard callback handlers (quality selection, cancel)
"""
from __future__ import annotations

import logging

from aiogram import Bot, Router, F
from aiogram.types import CallbackQuery

from utils import extract_url_from_text, is_valid_url
from handlers.common import resolve_url

logger = logging.getLogger(__name__)
router = Router(name="callbacks")


@router.callback_query(F.data.startswith("quality:"))
async def cb_quality(callback: CallbackQuery, bot: Bot) -> None:
    """
    Callback data format: quality:<quality>:<token>
    quality is one of: best, 720, 480, 360, 144, audio

    The third field is a 12-char hex token produced by handlers.common._store_url().
    We look it up via resolve_url() to recover the original (possibly long) URL
    without being constrained by Telegram's 64-byte callback_data limit.
    """
    await callback.answer()

    parts = (callback.data or "").split(":", 2)
    if len(parts) < 3:
        await callback.message.answer("❌ Invalid selection.")  # type: ignore[union-attr]
        return

    _, quality, token = parts

    # Try token lookup first; fall back to treating the field as a raw URL
    # (backward-compat for any outstanding keyboards generated before this change)
    url = resolve_url(token)
    if not url:
        url = extract_url_from_text(token) or token.strip()

    if not is_valid_url(url):
        await callback.message.answer(  # type: ignore[union-attr]
            "❌ The URL for this request has expired or is invalid. "
            "Please send the link again."
        )
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