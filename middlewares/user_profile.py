"""Keep Telegram profile fields current for every user interaction."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from services.user_store import register_user


class UserProfileMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None) or data.get("event_from_user")
        if user is not None:
            await register_user(
                user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
            )
        return await handler(event, data)
