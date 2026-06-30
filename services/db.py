"""
services/db.py – PostgreSQL connection and all database operations.

Replaces the JSON file store (user_languages.json, all_users.json)
and the in-memory stats tracker with persistent PostgreSQL storage.

Requires:  DATABASE_URL env var (Railway provides this automatically
           when you add a PostgreSQL plugin to your project).

Tables created automatically on first run:
  - users        (user_id, lang, created_at)
  - stats        (id, total_downloads, failed_downloads, updated_at)
"""
from __future__ import annotations

import logging
import os
import time

import asyncpg

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def init_db() -> None:
    """Create connection pool and ensure tables exist. Call once at startup."""
    global _pool
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        logger.warning("DATABASE_URL not set — falling back to in-memory store.")
        return

    _pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)

    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id   BIGINT PRIMARY KEY,
                lang      TEXT    NOT NULL DEFAULT 'en',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                id                INTEGER PRIMARY KEY DEFAULT 1,
                total_downloads   BIGINT  NOT NULL DEFAULT 0,
                failed_downloads  BIGINT  NOT NULL DEFAULT 0,
                updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CHECK (id = 1)
            )
        """)
        await conn.execute("""
            INSERT INTO stats (id, total_downloads, failed_downloads)
            VALUES (1, 0, 0)
            ON CONFLICT (id) DO NOTHING
        """)

    logger.info("PostgreSQL connected and tables ready.")


def _ready() -> bool:
    return _pool is not None


# ── User store ────────────────────────────────────────────────────────────────

async def register_user(user_id: int) -> None:
    if not _ready():
        return
    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id) VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
        """, user_id)


async def set_user_lang(user_id: int, lang: str) -> None:
    if not _ready():
        return
    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, lang) VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE SET lang = EXCLUDED.lang
        """, user_id, lang)


async def get_user_lang(user_id: int) -> str | None:
    if not _ready():
        return None
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT lang FROM users WHERE user_id = $1", user_id
        )
        return row["lang"] if row else None


async def get_user_lang_or_default(user_id: int, default: str = "en") -> str:
    lang = await get_user_lang(user_id)
    return lang if lang else default


async def has_chosen_language(user_id: int) -> bool:
    lang = await get_user_lang(user_id)
    return lang is not None


async def get_all_user_ids() -> list[int]:
    if not _ready():
        return []
    async with _pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM users")
        return [r["user_id"] for r in rows]


async def user_count() -> int:
    if not _ready():
        return 0
    async with _pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM users")


# ── Stats ─────────────────────────────────────────────────────────────────────

_start_time = time.time()


async def record_success(user_id: int) -> None:
    await register_user(user_id)
    if not _ready():
        return
    async with _pool.acquire() as conn:
        await conn.execute("""
            UPDATE stats SET
                total_downloads = total_downloads + 1,
                updated_at = NOW()
            WHERE id = 1
        """)


async def record_failure(user_id: int) -> None:
    await register_user(user_id)
    if not _ready():
        return
    async with _pool.acquire() as conn:
        await conn.execute("""
            UPDATE stats SET
                failed_downloads = failed_downloads + 1,
                updated_at = NOW()
            WHERE id = 1
        """)


async def get_stats_snapshot() -> dict:
    uptime_s = int(time.time() - _start_time)
    h, rem = divmod(uptime_s, 3600)
    m, s = divmod(rem, 60)
    base = {
        "uptime": f"{h}h {m}m {s}s",
        "total_downloads": 0,
        "failed_downloads": 0,
        "unique_users": 0,
    }
    if not _ready():
        return base
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT total_downloads, failed_downloads FROM stats WHERE id = 1")
        count = await conn.fetchval("SELECT COUNT(*) FROM users")
    if row:
        base["total_downloads"] = row["total_downloads"]
        base["failed_downloads"] = row["failed_downloads"]
    base["unique_users"] = count
    return base


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL pool closed.")