from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

from config.settings import settings
from services.user_store import get_all_user_ids, user_count, register_user

logger = logging.getLogger(__name__)
router = Router(name="admin")


def _is_admin(user_id: int) -> bool:
    return bool(settings.admin_id) and user_id == settings.admin_id


@router.message(Command("users"))
async def cmd_users(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    total = user_count()
    await message.answer(
        "<b>User Statistics</b>\n\n"
        "Total registered users: <b>" + str(total) + "</b>",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("addme"))
async def cmd_addme(message: Message) -> None:
    """
    Admin-only: register yourself into the DB manually.
    Run this once after deploy to make sure you're in the users list.
    """
    if not _is_admin(message.from_user.id):
        return
    register_user(message.from_user.id)
    await message.answer(
        "✅ You have been registered in the database.\n"
        "Total users now: <b>" + str(user_count()) + "</b>",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, bot: Bot) -> None:
    if not _is_admin(message.from_user.id):
        return

    # Make sure the admin is always registered
    register_user(message.from_user.id)

    text = message.text or ""
    parts = text.split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "<b>📢 Broadcast Usage</b>\n\n"
            "<code>/broadcast Your message here</code>\n\n"
            "You can use HTML tags:\n"
            "• <code>&lt;b&gt;bold&lt;/b&gt;</code>\n"
            "• <code>&lt;i&gt;italic&lt;/i&gt;</code>\n"
            "• <code>&lt;code&gt;monospace&lt;/code&gt;</code>\n\n"
            "Current users: <b>" + str(user_count()) + "</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    broadcast_text = parts[1].strip()
    user_ids = get_all_user_ids()

    # Always include admin even if list is empty
    if not user_ids:
        register_user(message.from_user.id)
        user_ids = get_all_user_ids()

    if not user_ids:
        await message.answer(
            "⚠️ No users found in database.\n\n"
            "Users are registered automatically when they send /start to the bot.\n"
            "Send /addme to register yourself first.",
            parse_mode=ParseMode.HTML,
        )
        return

    status = await message.answer(
        "📢 Broadcasting to <b>" + str(len(user_ids)) + "</b> users...",
        parse_mode=ParseMode.HTML,
    )

    sent = 0
    failed = 0
    blocked = 0

    for uid in user_ids:
        try:
            await bot.send_message(uid, broadcast_text, parse_mode=ParseMode.HTML)
            sent += 1
        except Exception as exc:
            err = str(exc).lower()
            if "blocked" in err or "deactivated" in err or "not found" in err or "forbidden" in err:
                blocked += 1
            else:
                failed += 1
                logger.warning("Broadcast failed for user %d: %s", uid, exc)
        await asyncio.sleep(0.05)

    await status.edit_text(
        "<b>✅ Broadcast complete</b>\n\n"
        "👥 Total: <b>" + str(len(user_ids)) + "</b>\n"
        "✅ Sent: <b>" + str(sent) + "</b>\n"
        "🚫 Blocked/deleted: <b>" + str(blocked) + "</b>\n"
        "❌ Failed: <b>" + str(failed) + "</b>",
        parse_mode=ParseMode.HTML,
    )