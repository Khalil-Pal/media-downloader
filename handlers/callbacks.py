from __future__ import annotations
import logging
from aiogram import Bot, Router, F
from aiogram.types import CallbackQuery
from services.user_store import get_user_lang_or_default
from utils import extract_url_from_text, is_valid_url
from utils.i18n import t
from handlers.common import resolve_url

logger = logging.getLogger(__name__)
router = Router(name="callbacks")


@router.callback_query(F.data.startswith("quality:"))
async def cb_quality(callback: CallbackQuery, bot: Bot) -> None:

    await callback.answer()

    user_id = callback.from_user.id
    lang = await get_user_lang_or_default(user_id)

    parts = (callback.data or "").split(":", 2)
    if len(parts) < 3:
        await callback.message.answer(t(lang, "invalid_selection"))
        return

    _, quality, token = parts

    url = resolve_url(token)
    if not url:
        url = extract_url_from_text(token) or token.strip()

    if not is_valid_url(url):
        await callback.message.answer(t(lang, "expired_url"))
        return

    audio_only = quality == "audio"
    effective_quality = "best" if audio_only else quality

    try:
        await callback.message.delete()
    except Exception:
        pass

    from handlers.downloader_handler import _run_download

    await _run_download(
        message=callback.message,
        bot=bot,
        url=url,
        quality=effective_quality,
        audio_only=audio_only,
    )


@router.callback_query(F.data.startswith("cancel:"))
async def cb_cancel(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    lang = await get_user_lang_or_default(user_id)

    await callback.answer(t(lang, "cancelling"))

    from services import cancel_download

    cancel_download(user_id)

    try:
        await callback.message.edit_text(t(lang, "download_cancelled"))
    except Exception:
        pass