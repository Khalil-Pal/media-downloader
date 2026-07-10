"""
main.py – Sandy Squirrel Bot entry point

Runs in webhook mode on Railway (PORT env var is set automatically).
Falls back to polling if WEBHOOK_URL is not set (useful for local dev).

Start with:  python main.py
"""
from __future__ import annotations

from bootstrap import bootstrap_cookie_files, log_cookie_bootstrap_status

bootstrap_cookie_files()

import asyncio
import logging
import os

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from config.settings import settings
from config.logging_config import setup_logging
from handlers import main_router
from services import db
from services.telethon_uploader import close_client as close_telethon_client

setup_logging()
logger = logging.getLogger(__name__)
log_cookie_bootstrap_status(logger)

_COMMANDS_EN = [
    BotCommand(command="start",     description="Start the bot"),
    BotCommand(command="help",      description="Help guide"),
    BotCommand(command="download",  description="Download a video"),
    BotCommand(command="audio",     description="Download audio only"),
    BotCommand(command="quality",   description="Quality options"),
    BotCommand(command="cancel",    description="Cancel current download"),
    BotCommand(command="language",  description="Change language"),
    BotCommand(command="mode",      description="Switch bot mode"),
]
_COMMANDS_AR = [
    BotCommand(command="start",     description="بدء البوت"),
    BotCommand(command="help",      description="دليل المساعدة"),
    BotCommand(command="download",  description="تحميل فيديو"),
    BotCommand(command="audio",     description="تحميل الصوت فقط"),
    BotCommand(command="quality",   description="خيارات الجودة"),
    BotCommand(command="cancel",    description="إلغاء التحميل الحالي"),
    BotCommand(command="language",  description="تغيير اللغة"),
    BotCommand(command="mode",      description="تغيير وضع البوت"),
]
_COMMANDS_RU = [
    BotCommand(command="start",     description="Запустить бота"),
    BotCommand(command="help",      description="Руководство помощи"),
    BotCommand(command="download",  description="Скачать видео"),
    BotCommand(command="audio",     description="Скачать только аудио"),
    BotCommand(command="quality",   description="Варианты качества"),
    BotCommand(command="cancel",    description="Отменить загрузку"),
    BotCommand(command="language",  description="Изменить язык"),
    BotCommand(command="mode",      description="Сменить режим бота"),
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


async def on_startup(bot: Bot) -> None:
    await db.init_db()
    await _register_commands(bot)
    webhook_url = os.getenv("WEBHOOK_URL", "")
    if webhook_url:
        await bot.set_webhook(f"{webhook_url}/webhook")
        logger.info("Webhook set to %s/webhook", webhook_url)


async def on_shutdown(bot: Bot) -> None:
    await close_telethon_client()
    await db.close_db()
    webhook_url = os.getenv("WEBHOOK_URL", "")
    if webhook_url:
        await bot.delete_webhook()
    logger.info("Sandy Squirrel is going to sleep. Goodbye!")


async def main() -> None:
    logger.info("Sandy Squirrel is waking up...")

    if settings.local_bot_api_url:
        local_api = TelegramAPIServer.from_base(
            settings.local_bot_api_url,
            is_local=True,
        )
        bot_session = AiohttpSession(api=local_api)
        logger.info("Using Local Bot API server at %s", settings.local_bot_api_url)
    else:
        bot_session = None
        logger.warning("Using cloud Bot API: bot uploads are limited to 50 MB.")

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=bot_session,
    )

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(main_router)
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    logger.info(
        "Config: max_file=%dMB | concurrency=%d | rate=%d/%ds",
        settings.max_file_size_mb,
        settings.max_concurrent_downloads,
        settings.rate_limit_max,
        settings.rate_limit_window,
    )

    webhook_url = os.getenv("WEBHOOK_URL", "")
    port = int(os.getenv("PORT", 8080))

    if webhook_url:
        # ── Webhook mode (Railway production) ────────────────────────────
        logger.info("Starting in webhook mode on port %d", port)
        app = web.Application()
        handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
        handler.register(app, path="/webhook")
        setup_application(app, dp, bot=bot)
        await web._run_app(app, host="0.0.0.0", port=port)
    else:
        # ── Polling mode (local development) ─────────────────────────────
        logger.info("WEBHOOK_URL not set — starting in polling mode (local dev)")
        try:
            await dp.start_polling(
                bot,
                allowed_updates=dp.resolve_used_update_types(),
            )
        finally:
            await close_telethon_client()
            await bot.session.close()
            await db.close_db()
            logger.info("Sandy Squirrel is going to sleep. Goodbye!")


if __name__ == "__main__":
    asyncio.run(main())
