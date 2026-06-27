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

from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

from config.settings import settings
from config.logging_config import setup_logging
from handlers import main_router

setup_logging()
logger = logging.getLogger(__name__)

_COMMANDS_EN = [
    BotCommand(command="start",     description="Start the bot"),
    BotCommand(command="help",      description="Help guide"),
    BotCommand(command="download",  description="Download a video"),
    BotCommand(command="audio",     description="Download audio only"),
    BotCommand(command="quality",   description="Quality options"),
    BotCommand(command="cancel",    description="Cancel current download"),
    BotCommand(command="language",  description="Change language"),
]

_COMMANDS_AR = [
    BotCommand(command="start",     description="بدء البوت"),
    BotCommand(command="help",      description="دليل المساعدة"),
    BotCommand(command="download",  description="تحميل فيديو"),
    BotCommand(command="audio",     description="تحميل الصوت فقط"),
    BotCommand(command="quality",   description="خيارات الجودة"),
    BotCommand(command="cancel",    description="إلغاء التحميل الحالي"),
    BotCommand(command="language",  description="تغيير اللغة"),
]

_COMMANDS_RU = [
    BotCommand(command="start",     description="Запустить бота"),
    BotCommand(command="help",      description="Руководство помощи"),
    BotCommand(command="download",  description="Скачать видео"),
    BotCommand(command="audio",     description="Скачать только аудио"),
    BotCommand(command="quality",   description="Варианты качества"),
    BotCommand(command="cancel",    description="Отменить загрузку"),
    BotCommand(command="language",  description="Изменить язык"),
]

_COMMANDS_ADMIN = _COMMANDS_EN + [
    BotCommand(command="stats",      description="Bot statistics"),
    BotCommand(command="users",      description="Registered user count"),
    BotCommand(command="broadcast",  description="Send message to all users"),
]


async def _register_commands(bot: Bot) -> None:
    await bot.set_my_commands(_COMMANDS_EN, scope=BotCommandScopeDefault())
    await bot.set_my_commands(_COMMANDS_AR, scope=BotCommandScopeDefault(), language_code="ar")
    await bot.set_my_commands(_COMMANDS_RU, scope=BotCommandScopeDefault(), language_code="ru")
    if settings.admin_id:
        await bot.set_my_commands(
            _COMMANDS_ADMIN,
            scope=BotCommandScopeChat(chat_id=settings.admin_id),
        )
    logger.info("Bot commands registered.")


async def main() -> None:
    logger.info("Sandy Squirrel is waking up...")

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(main_router)

    # Graceful startup log
    me = await bot.get_me()
    logger.info("Bot started as @%s (id=%d)", me.username, me.id)
    logger.info(
        "Config: max_file=%dMB | concurrency=%d | rate=%d/%ds",
        settings.max_file_size_mb,
        settings.max_concurrent_downloads,
        settings.rate_limit_max,
        settings.rate_limit_window,
    )

    await _register_commands(bot)
    
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()
        logger.info("Sandy Squirrel is going to sleep. Goodbye!")

if __name__ == "__main__":
    asyncio.run(main())
