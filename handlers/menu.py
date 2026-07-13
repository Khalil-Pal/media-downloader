"""Main menu flow for Sandy Squirrel."""
from __future__ import annotations

import logging
from html import escape
from pathlib import Path

from aiogram import F, Router
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup, Message, User
from aiogram.utils.keyboard import InlineKeyboardBuilder

from services.user_store import get_user_lang_or_default, set_user_mode
from utils.i18n import t

logger = logging.getLogger(__name__)
router = Router(name="menu")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MENU_PHOTO_CANDIDATES = (
    PROJECT_ROOT / "main_menu.jpg",
)


def main_menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=t(lang, "menu_btn_download"), callback_data="menu:download")
    builder.button(text=t(lang, "menu_btn_convert"), callback_data="menu:convert")
    builder.button(text=t(lang, "menu_btn_plans"), callback_data="menu:plans")
    builder.button(text=t(lang, "menu_btn_my_plan"), callback_data="menu:my_plan")
    builder.button(text=t(lang, "menu_btn_about"), callback_data="menu:about")
    builder.button(text=t(lang, "menu_btn_language"), callback_data="menu:language")
    builder.adjust(2, 2, 2)
    return builder.as_markup()


def plans_keyboard(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "plan_btn_downloader_pro"),
        callback_data="plan:downloader_pro",
    )
    builder.button(
        text=t(lang, "plan_btn_converter_pro"),
        callback_data="plan:converter_pro",
    )
    builder.button(
        text=t(lang, "plan_btn_all_in_one"),
        callback_data="plan:all_in_one",
    )
    builder.button(
        text=t(lang, "plan_btn_annual"),
        callback_data="plan:annual",
    )
    builder.button(
        text=t(lang, "plan_btn_starter_pack"),
        callback_data="plan:starter_pack",
    )
    builder.button(
        text=t(lang, "plan_btn_pro_pack"),
        callback_data="plan:pro_pack",
    )
    builder.button(
        text=t(lang, "plan_btn_ultra_pack"),
        callback_data="plan:ultra_pack",
    )
    builder.button(text=t(lang, "btn_back_main_menu"), callback_data="menu:back")
    builder.adjust(1)
    return builder.as_markup()


def _menu_photo_path() -> Path | None:
    return next((path for path in MENU_PHOTO_CANDIDATES if path.is_file()), None)


def _display_name(user: User | None, lang: str) -> str:
    if user and user.username:
        return "@" + user.username
    if user and user.first_name:
        return user.first_name
    return t(lang, "friend_name")


async def show_main_menu(
    message: Message,
    lang: str | None = None,
    user: User | None = None,
) -> None:
    actor = user or message.from_user
    user_id = actor.id if actor else 0
    effective_lang = lang or await get_user_lang_or_default(user_id)
    photo_path = _menu_photo_path()

    if photo_path:
        try:
            await message.answer_photo(photo=FSInputFile(photo_path))
        except Exception as exc:
            logger.warning("Could not send main menu photo %s: %s", photo_path, exc)

    username = escape(_display_name(actor, effective_lang))
    await message.answer(
        t(effective_lang, "main_menu_welcome", username=username),
        reply_markup=main_menu_keyboard(effective_lang),
    )


@router.callback_query(F.data.startswith("menu:"))
async def cb_main_menu(callback: CallbackQuery) -> None:
    await callback.answer()

    if callback.message is None:
        return

    action = (callback.data or "").split(":", 1)[1]
    user_id = callback.from_user.id
    lang = await get_user_lang_or_default(user_id)

    if action == "back":
        await show_main_menu(callback.message, lang, user=callback.from_user)
        return

    if action == "download":
        await set_user_mode(user_id, "downloader")
        await callback.message.answer(t(lang, "menu_download_instruction"))
        return

    if action == "convert":
        await set_user_mode(user_id, "converter")
        await callback.message.answer(t(lang, "menu_convert_instruction"))
        return

    if action == "plans":
        await callback.message.answer(
            t(lang, "menu_plans"),
            reply_markup=plans_keyboard(lang),
        )
        return

    if action == "my_plan":
        await callback.message.answer(t(lang, "menu_my_plan_free"))
        return

    if action == "about":
        await callback.message.answer(t(lang, "menu_about"), parse_mode=None)
        return

    if action == "language":
        from handlers.language import language_keyboard

        await callback.message.answer(
            t(None, "choose_language"),
            reply_markup=language_keyboard(),
        )


@router.callback_query(F.data.startswith("plan:"))
async def cb_plan_choice(callback: CallbackQuery) -> None:
    await callback.answer()

    if callback.message is None:
        return

    lang = await get_user_lang_or_default(callback.from_user.id)
    await callback.message.answer(t(lang, "plan_payment_disabled"))
