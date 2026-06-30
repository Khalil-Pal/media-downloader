"""
services/user_store.py – User store that delegates to PostgreSQL via db.py.

Keeps the same public API as the old JSON-based store so no handler
code needs to change. All functions are now async.
"""
from __future__ import annotations

from services import db


async def register_user(user_id: int) -> None:
    await db.register_user(user_id)


async def get_all_user_ids() -> list[int]:
    return await db.get_all_user_ids()


async def user_count() -> int:
    return await db.user_count()


async def get_user_lang(user_id: int) -> str | None:
    return await db.get_user_lang(user_id)


async def get_user_lang_or_default(user_id: int, default: str = "en") -> str:
    return await db.get_user_lang_or_default(user_id, default)


async def set_user_lang(user_id: int, lang: str) -> None:
    await db.set_user_lang(user_id, lang)


async def has_chosen_language(user_id: int) -> bool:
    return await db.has_chosen_language(user_id)