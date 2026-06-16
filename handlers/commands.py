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
from handlers.common import WELCOME_TEXT, HELP_TEXT

logger = logging.getLogger(__name__)
router = Router(name="commands")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(WELCOME_TEXT, parse_mode="Markdown")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, parse_mode="Markdown")


@router.message(Command("quality"))
async def cmd_quality(message: Message) -> None:
    text = (
        "🎚 *Available Quality Options*\n\n"
        "• 🥇 *Best* – Highest available resolution + audio\n"
        "• 📺 *720p* – HD (recommended for most use cases)\n"
        "• 📱 *480p* – Standard definition\n"
        "• 🔻 *360p* – Low bandwidth\n"
        "• 🔍 *144p* – Minimum quality\n"
        "• 🎵 *Audio* – MP3 audio only (no video)\n\n"
        "Quality options appear automatically after you send a URL."
    )
    await message.answer(text, parse_mode="Markdown")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]
    if cancel_download(user_id):
        await message.answer("🛑 Cancellation requested. Your download will stop shortly.")
    else:
        await message.answer("ℹ️ No active download to cancel.")


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
