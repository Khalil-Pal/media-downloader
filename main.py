"""
main.py – Sandy Squirrel Bot entry point

Start with:  python main.py
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config.settings import settings
from config.logging_config import setup_logging
from handlers import main_router

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("🐿️  Sandy Squirrel is waking up…")

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(main_router)

    # Graceful startup log
    me = await bot.get_me()
    logger.info("✅ Bot started as @%s (id=%d)", me.username, me.id)
    logger.info(
        "⚙️  Config: max_file=%dMB | concurrency=%d | rate=%d/%ds",
        settings.max_file_size_mb,
        settings.max_concurrent_downloads,
        settings.rate_limit_max,
        settings.rate_limit_window,
    )

    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()
        logger.info("🐿️  Sandy Squirrel is going to sleep. Goodbye!")


if __name__ == "__main__":
    asyncio.run(main())
