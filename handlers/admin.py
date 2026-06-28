from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

from config.settings import settings
from services.user_store import get_all_user_ids, user_count

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
        "Total registered users: <b>" + str(total) + "</b>"
    )


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, bot: Bot) -> None:
    if not _is_admin(message.from_user.id):
        return
    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "<b>Broadcast Usage</b>\n\n"
            "<code>/broadcast Your message here</code>\n\n"
            "Sends to all registered users.\n"
            "Current users: <b>" + str(user_count()) + "</b>"
        )
        return
    broadcast_text = parts[1].strip()
    user_ids = get_all_user_ids()
    if not user_ids:
        await message.answer("No users to broadcast to yet.")
        return
    status = await message.answer(
        "Broadcasting to <b>" + str(len(user_ids)) + "</b> users..."
    )
    sent = 0
    failed = 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, broadcast_text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await status.edit_text(
        "<b>Broadcast complete</b>\n\n"
        "Sent: <b>" + str(sent) + "</b>\n"
        "Failed (blocked/deleted): <b>" + str(failed) + "</b>"
    )