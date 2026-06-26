"""
handlers/commands.py – Telegram command handlers (/start, /help, /quality, /cancel, /stats)
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config.settings import settings
from services import cancel_download, stats
from services.user_store import get_user_lang, get_user_lang_or_default, has_chosen_language
from handlers.language import language_keyboard
from utils.i18n import t
logger = logging.getLogger(__name__)
router = Router(name="commands")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]

    if not has_chosen_language(user_id):
        await message.answer(
            t(None, "choose_language"),
            reply_markup=language_keyboard(),
        )
        return

    lang = get_user_lang_or_default(user_id)
    await message.answer(t(lang, "welcome"), parse_mode="Markdown")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]
    lang = get_user_lang_or_default(user_id)
    await message.answer(t(lang, "help"), parse_mode="Markdown")


@router.message(Command("quality"))
async def cmd_quality(message: Message) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]

    lang = get_user_lang_or_default(user_id)
    await message.answer(t(lang, "quality_options"), parse_mode="Markdown")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]
    lang = get_user_lang_or_default(user_id)
    if cancel_download(user_id):
        await message.answer(t(lang, "cancel_requested"))
    else:
        await message.answer(t(lang, "no_active_download"))


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]

    if settings.admin_id and user_id != settings.admin_id:
        await message.answer("⛔ This command is only available to the bot administrator.")
        return

    snapshot = await stats.get_snapshot()
    text = (
        "📊 *Sandy Squirrel – Statistics*\n\n"
        f"⏱ Uptime: `{snapshot['uptime']}`\n"
        f"✅ Successful downloads: `{snapshot['total_downloads']}`\n"
        f"❌ Failed downloads: `{snapshot['failed_downloads']}`\n"
        f"👥 Unique users: `{snapshot['unique_users']}`"
    )
    await message.answer(text, parse_mode="Markdown")
