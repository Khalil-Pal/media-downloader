from aiogram import Router

from handlers.commands import router as commands_router
from handlers.language import router as language_router
from handlers.downloader_handler import router as downloader_router
from handlers.callbacks import router as callbacks_router

# Master router – include into the Dispatcher in main.py
main_router = Router(name="main")
main_router.include_router(commands_router)
main_router.include_router(language_router)
main_router.include_router(callbacks_router)
main_router.include_router(downloader_router)

__all__ = ["main_router"]
