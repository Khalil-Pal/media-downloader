"""
handlers/language.py – Language selection handler.

Covers:
  • /language command  →  shows the 3-language picker
  • setlang:<code> callback  →  saves the choice and confirms it
  • /mode command  →  shows the downloader/converter mode picker
"""
from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from services.user_store import (
    get_user_lang_or_default,
    get_user_mode,
    set_user_lang,
    set_user_mode,
)
from utils.i18n import t

logger = logging.getLogger(__name__)
router = Router(name="language")


def language_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=t(None, "btn_english"), callback_data="setlang:en")
    builder.button(text=t(None, "btn_arabic"), callback_data="setlang:ar")
    builder.button(text=t(None, "btn_russian"), callback_data="setlang:ru")
    builder.adjust(1)
    return builder.as_markup()


def mode_keyboard(
    lang: str,
    current_mode: str | None = None,
    context: str = "switch",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    downloader_text = t(lang, "btn_mode_downloader")
    converter_text = t(lang, "btn_mode_converter")

    if current_mode == "downloader":
        downloader_text += " " + t(lang, "mode_current_suffix")
    elif current_mode == "converter":
        converter_text += " " + t(lang, "mode_current_suffix")

    builder.button(text=downloader_text, callback_data=f"setmode:downloader:{context}")
    builder.button(text=converter_text, callback_data=f"setmode:converter:{context}")
    builder.adjust(1)
    return builder.as_markup()


async def prompt_mode_selection(
    message: Message,
    lang: str,
    current_mode: str | None = None,
    context: str = "onboard",
) -> None:
    await message.answer(
        t(lang, "choose_mode"),
        reply_markup=mode_keyboard(lang, current_mode=current_mode, context=context),
    )


async def ensure_mode_selected(message: Message, lang: str | None = None) -> bool:
    user_id = message.from_user.id  # type: ignore[union-attr]
    current_mode = await get_user_mode(user_id)
    if current_mode:
        return True

    effective_lang = lang or await get_user_lang_or_default(user_id)
    await prompt_mode_selection(message, effective_lang, context="onboard")
    return False


@router.message(Command("language"))
async def cmd_language(message: Message) -> None:
    await message.answer(
        t(None, "choose_language"),
        reply_markup=language_keyboard(),
    )


@router.message(Command("mode"))
async def cmd_mode(message: Message) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]
    lang = await get_user_lang_or_default(user_id)
    current_mode = await get_user_mode(user_id)
    await prompt_mode_selection(
        message,
        lang,
        current_mode=current_mode,
        context="switch",
    )


@router.callback_query(F.data.startswith("setlang:"))
async def cb_set_language(callback: CallbackQuery) -> None:
    await callback.answer()

    lang = (callback.data or "").split(":", 1)[1]
    if lang not in ("en", "ar", "ru"):
        return

    user_id = callback.from_user.id
    current_mode = await get_user_mode(user_id)
    await set_user_lang(user_id, lang)

    try:
        await callback.message.delete()  # type: ignore[union-attr]
    except Exception:
        pass

    await callback.message.answer(t(lang, "language_changed"))
    if current_mode is None:
        await prompt_mode_selection(callback.message, lang, context="onboard")
    else:
        await callback.message.answer(t(lang, "welcome"), parse_mode="Markdown")


@router.callback_query(F.data.startswith("setmode:"))
async def cb_set_mode(callback: CallbackQuery) -> None:
    await callback.answer()

    parts = (callback.data or "").split(":")
    if len(parts) < 2:
        return

    mode = parts[1]
    context = parts[2] if len(parts) > 2 else "switch"
    if mode not in ("downloader", "converter"):
        return

    user_id = callback.from_user.id
    lang = await get_user_lang_or_default(user_id)
    await set_user_mode(user_id, mode)

    try:
        await callback.message.delete()  # type: ignore[union-attr]
    except Exception:
        pass

    await callback.message.answer(
        t(lang, "mode_changed", mode=t(lang, f"mode_name_{mode}"))
    )
    if context == "onboard":
        await callback.message.answer(t(lang, "welcome"), parse_mode="Markdown")
