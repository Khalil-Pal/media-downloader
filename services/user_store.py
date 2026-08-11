"""
services/user_store.py – User store that delegates to PostgreSQL via db.py.

Keeps the same public API as the old JSON-based store so no handler
code needs to change. All functions are now async.
"""
from __future__ import annotations

from services import db


async def register_user(
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> None:
    await db.register_user(user_id, username, first_name, last_name)


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


async def get_user_mode(user_id: int) -> str | None:
    return await db.get_user_mode(user_id)


async def get_user_mode_or_default(user_id: int, default: str = "downloader") -> str:
    return await db.get_user_mode_or_default(user_id, default)


async def set_user_mode(user_id: int, mode: str) -> None:
    await db.set_user_mode(user_id, mode)


async def has_chosen_language(user_id: int) -> bool:
    return await db.has_chosen_language(user_id)


async def get_user_plan(user_id: int) -> dict | None:
    return await db.get_user_plan(user_id)


async def set_user_plan(
    user_id: int,
    plan: str,
    plan_expires_at,
) -> dict:
    return await db.set_user_plan(user_id, plan, plan_expires_at)


async def get_pending_payment_by_ref(ref_code: str) -> dict | None:
    return await db.get_pending_payment_by_ref(ref_code)


async def get_pending_upgrade_payment_for_user(user_id: int) -> dict | None:
    return await db.get_pending_upgrade_payment_for_user(user_id)


async def set_payment_pending(
    user_id: int,
    plan: str,
    currency: str,
    ref_code: str,
) -> dict:
    return await db.set_payment_pending(user_id, plan, currency, ref_code)


async def cancel_pending_payment(user_id: int) -> dict | None:
    return await db.cancel_pending_payment(user_id)


async def set_payment_confirmed(ref_code: str, plan: str, plan_expires_at) -> dict | None:
    return await db.set_payment_confirmed(ref_code, plan, plan_expires_at)


async def set_payment_rejected(ref_code: str) -> dict | None:
    return await db.set_payment_rejected(ref_code)
