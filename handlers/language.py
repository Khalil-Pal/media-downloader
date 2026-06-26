"""
handlers/language.py – Language selection handler.

Covers:
  • /language command  →  shows the 3-language picker
  • setlang:<code> callback  →  saves the choice and confirms it
"""
from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from services.user_store import set_user_lang, get_user_lang_or_default
from utils.i18n import t

logger = logging.getLogger(__name__)
router = Router(name="language")


def language_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="English 🇺🇸",  callback_data="setlang:en")
    builder.button(text="العربية 🇵🇸",  callback_data="setlang:ar")
    builder.button(text="Русский 🇷🇺",  callback_data="setlang:ru")
    builder.adjust(1)
    return builder.as_markup()


@router.message(Command("language"))
async def cmd_language(message: Message) -> None:
    await message.answer(
        t(None, "choose_language"),
        reply_markup=language_keyboard(),
    )


@router.callback_query(F.data.startswith("setlang:"))
async def cb_set_language(callback: CallbackQuery) -> None:
    await callback.answer()

    lang = (callback.data or "").split(":", 1)[1]
    if lang not in ("en", "ar", "ru"):
        return

    user_id = callback.from_user.id
    set_user_lang(user_id, lang)

    try:
        await callback.message.delete()  # type: ignore[union-attr]
    except Exception:
        pass

    await callback.message.answer(  # type: ignore[union-attr]
        t(lang, "language_changed")
    )

    effective_lang = get_user_lang_or_default(user_id)
    await callback.message.answer(  # type: ignore[union-attr]
        t(effective_lang, "welcome"),
        parse_mode="Markdown",
    )
