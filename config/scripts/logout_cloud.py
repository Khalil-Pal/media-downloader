"""One-time migration helper: move this bot from cloud Bot API to local Bot API."""
from __future__ import annotations

import asyncio

from aiogram import Bot

from config.settings import settings


async def main() -> None:
    bot = Bot(token=settings.bot_token)
    try:
        # Keep pending updates. The local server will receive them after it starts.
        await bot.delete_webhook(drop_pending_updates=False)
        await bot.log_out()
        print("Bot logged out from api.telegram.org; starting local Bot API next.")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
