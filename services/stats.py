"""
services/stats.py – Stats tracker that delegates to PostgreSQL via db.py.

Keeps the same public API as the old in-memory tracker so no handler
code needs to change.
"""
from __future__ import annotations

from services import db


async def record_success(user_id: int) -> None:
    await db.record_success(user_id)


async def record_failure(user_id: int) -> None:
    await db.record_failure(user_id)


async def get_snapshot() -> dict:
    return await db.get_stats_snapshot()


# Singleton-compatible alias used by handlers
class _StatsProxy:
    async def record_success(self, user_id: int) -> None:
        await db.record_success(user_id)

    async def record_failure(self, user_id: int) -> None:
        await db.record_failure(user_id)

    async def get_snapshot(self) -> dict:
        return await db.get_stats_snapshot()


stats = _StatsProxy()