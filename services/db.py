"""
services/db.py – PostgreSQL connection and all database operations.

Replaces the JSON file store (user_languages.json, all_users.json)
and the in-memory stats tracker with persistent PostgreSQL storage.

Requires:  DATABASE_URL env var (Railway provides this automatically
           when you add a PostgreSQL plugin to your project).

Tables created automatically on first run:
  - users        (user_id, lang, mode, created_at)
  - stats        (id, total_downloads, failed_downloads, updated_at)
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import asyncpg

from services.plans import Plan

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None
_memory_users: dict[int, dict[str, object | None]] = {}
_memory_total_downloads = 0
_memory_failed_downloads = 0
_memory_payments: dict[int, dict] = {}
_memory_user_plans: dict[int, dict] = {}
_memory_cancelled_payment_refs: dict[str, dict] = {}
_memory_next_payment_id = 1


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
                lang      TEXT,
                mode      TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS mode TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS plan TEXT NOT NULL DEFAULT 'free'")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_expires_at TIMESTAMPTZ")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS payment_ref TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS payment_status TEXT NOT NULL DEFAULT 'none'")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS payment_currency TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS payment_plan TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS payment_requested_at TIMESTAMPTZ")
        await conn.execute("ALTER TABLE users ALTER COLUMN lang DROP DEFAULT")
        await conn.execute("ALTER TABLE users ALTER COLUMN lang DROP NOT NULL")
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
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id               BIGSERIAL PRIMARY KEY,
                user_id          BIGINT NOT NULL,
                username         TEXT,
                plan_key         TEXT NOT NULL,
                plan_name        TEXT NOT NULL,
                currency         TEXT NOT NULL,
                amount           TEXT NOT NULL,
                status           TEXT NOT NULL DEFAULT 'pending',
                receipt_file_id  TEXT,
                receipt_file_type TEXT,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                reviewed_at      TIMESTAMPTZ,
                reviewed_by      BIGINT
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS payments_user_status_idx
            ON payments (user_id, status)
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS cancelled_payment_refs (
                payment_ref      TEXT PRIMARY KEY,
                user_id          BIGINT NOT NULL,
                payment_plan     TEXT,
                payment_currency TEXT,
                cancelled_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS cancelled_payment_refs_user_idx
            ON cancelled_payment_refs (user_id, cancelled_at DESC)
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_plans (
                user_id                 BIGINT PRIMARY KEY,
                plan_key                TEXT NOT NULL,
                plan_name               TEXT NOT NULL,
                plan_type               TEXT NOT NULL,
                starts_at               TIMESTAMPTZ NOT NULL,
                expires_at              TIMESTAMPTZ NOT NULL,
                max_file_size_mb        INTEGER NOT NULL,
                unlimited_downloads     BOOLEAN NOT NULL,
                unlimited_conversions   BOOLEAN NOT NULL,
                downloads_remaining     INTEGER,
                conversions_remaining   INTEGER,
                priority_level          INTEGER NOT NULL,
                is_active               BOOLEAN NOT NULL DEFAULT TRUE
            )
        """)

    logger.info("PostgreSQL connected and tables ready.")


def _ready() -> bool:
    return _pool is not None


def _memory_register_user(user_id: int) -> dict[str, object | None]:
    return _memory_users.setdefault(
        user_id,
        {
            "lang": None,
            "mode": None,
            "username": None,
            "first_name": None,
            "last_name": None,
            "plan": "free",
            "plan_expires_at": None,
            "payment_ref": None,
            "payment_status": "none",
            "payment_currency": None,
            "payment_plan": None,
            "payment_requested_at": None,
        },
    )


# ── User store ────────────────────────────────────────────────────────────────

async def register_user(
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> None:
    has_profile = username is not None or first_name is not None or last_name is not None
    if not _ready():
        user = _memory_register_user(user_id)
        if has_profile:
            user["username"] = username
            user["first_name"] = first_name
            user["last_name"] = last_name
        return
    async with _pool.acquire() as conn:
        if has_profile:
            await conn.execute("""
                INSERT INTO users (user_id, username, first_name, last_name)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name,
                    last_name = EXCLUDED.last_name
            """, user_id, username, first_name, last_name)
        else:
            await conn.execute("""
                INSERT INTO users (user_id) VALUES ($1)
                ON CONFLICT (user_id) DO NOTHING
            """, user_id)


async def set_user_lang(user_id: int, lang: str) -> None:
    if not _ready():
        _memory_register_user(user_id)["lang"] = lang
        return
    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, lang) VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE SET lang = EXCLUDED.lang
        """, user_id, lang)


async def set_user_mode(user_id: int, mode: str) -> None:
    if mode not in {"downloader", "converter"}:
        raise ValueError(f"Invalid user mode: {mode}")
    if not _ready():
        _memory_register_user(user_id)["mode"] = mode
        return
    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, mode) VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE SET mode = EXCLUDED.mode
        """, user_id, mode)


async def get_user_lang(user_id: int) -> str | None:
    if not _ready():
        user = _memory_users.get(user_id)
        return user["lang"] if user else None
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT lang FROM users WHERE user_id = $1", user_id
        )
        return row["lang"] if row else None


async def get_user_lang_or_default(user_id: int, default: str = "en") -> str:
    lang = await get_user_lang(user_id)
    return lang if lang else default


async def get_user_mode(user_id: int) -> str | None:
    if not _ready():
        user = _memory_users.get(user_id)
        return user["mode"] if user else None
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT mode FROM users WHERE user_id = $1", user_id
        )
        return row["mode"] if row else None


async def get_user_mode_or_default(user_id: int, default: str = "downloader") -> str:
    mode = await get_user_mode(user_id)
    return mode if mode else default


async def has_chosen_language(user_id: int) -> bool:
    lang = await get_user_lang(user_id)
    return lang is not None


async def get_all_user_ids() -> list[int]:
    if not _ready():
        return list(_memory_users)
    async with _pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM users")
        return [r["user_id"] for r in rows]


async def user_count() -> int:
    if not _ready():
        return len(_memory_users)
    async with _pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM users")


# ── Stats ─────────────────────────────────────────────────────────────────────

_start_time = time.time()


async def record_success(user_id: int) -> None:
    global _memory_total_downloads
    await register_user(user_id)
    if not _ready():
        _memory_total_downloads += 1
        return
    async with _pool.acquire() as conn:
        await conn.execute("""
            UPDATE stats SET
                total_downloads = total_downloads + 1,
                updated_at = NOW()
            WHERE id = 1
        """)


async def record_failure(user_id: int) -> None:
    global _memory_failed_downloads
    await register_user(user_id)
    if not _ready():
        _memory_failed_downloads += 1
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
        base["total_downloads"] = _memory_total_downloads
        base["failed_downloads"] = _memory_failed_downloads
        base["unique_users"] = len(_memory_users)
        return base
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT total_downloads, failed_downloads FROM stats WHERE id = 1")
        count = await conn.fetchval("SELECT COUNT(*) FROM users")
    if row:
        base["total_downloads"] = row["total_downloads"]
        base["failed_downloads"] = row["failed_downloads"]
    base["unique_users"] = count
    return base


async def get_user_plan(user_id: int) -> dict | None:
    if not _ready():
        user = _memory_users.get(user_id)
        return dict(user, user_id=user_id) if user else None

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
                user_id,
                username,
                first_name,
                last_name,
                plan,
                plan_expires_at,
                payment_ref,
                payment_status,
                payment_currency,
                payment_plan,
                payment_requested_at
            FROM users
            WHERE user_id = $1
        """, user_id)
        return dict(row) if row else None


async def set_user_plan(
    user_id: int,
    plan: str,
    plan_expires_at: datetime | None,
) -> dict:
    if not _ready():
        user = _memory_register_user(user_id)
        user["plan"] = plan
        user["plan_expires_at"] = plan_expires_at
        if plan == "free":
            user["payment_status"] = "none"
            user["payment_ref"] = None
            user["payment_currency"] = None
            user["payment_plan"] = None
            user["payment_requested_at"] = None
        else:
            user["payment_status"] = "confirmed"
        return dict(user, user_id=user_id)

    if plan == "free":
        async with _pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO users (
                    user_id,
                    plan,
                    plan_expires_at,
                    payment_ref,
                    payment_status,
                    payment_currency,
                    payment_plan,
                    payment_requested_at
                )
                VALUES ($1, 'free', NULL, NULL, 'none', NULL, NULL, NULL)
                ON CONFLICT (user_id) DO UPDATE SET
                    plan = 'free',
                    plan_expires_at = NULL,
                    payment_ref = NULL,
                    payment_status = 'none',
                    payment_currency = NULL,
                    payment_plan = NULL,
                    payment_requested_at = NULL
                RETURNING *
            """, user_id)
            return dict(row)

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO users (
                user_id,
                plan,
                plan_expires_at,
                payment_status
            )
            VALUES ($1, $2, $3, 'confirmed')
            ON CONFLICT (user_id) DO UPDATE SET
                plan = EXCLUDED.plan,
                plan_expires_at = EXCLUDED.plan_expires_at,
                payment_status = 'confirmed'
            RETURNING *
        """, user_id, plan, plan_expires_at)
        return dict(row)


async def get_pending_upgrade_payment_for_user(user_id: int) -> dict | None:
    if not _ready():
        user = _memory_users.get(user_id)
        if user and user.get("payment_status") == "pending":
            return dict(user, user_id=user_id)
        return None

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT *
            FROM users
            WHERE user_id = $1
              AND payment_status = 'pending'
        """, user_id)
        return dict(row) if row else None


async def get_pending_payment_by_ref(ref_code: str) -> dict | None:
    if not _ready():
        for user_id, user in _memory_users.items():
            if (
                user.get("payment_ref") == ref_code
                and user.get("payment_status") == "pending"
            ):
                return dict(user, user_id=user_id)
        return None

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT *
            FROM users
            WHERE payment_ref = $1
              AND payment_status = 'pending'
            LIMIT 1
        """, ref_code)
        return dict(row) if row else None


async def set_payment_pending(
    user_id: int,
    plan: str,
    currency: str,
    ref_code: str,
) -> dict:
    if not _ready():
        user = _memory_register_user(user_id)
        user["payment_ref"] = ref_code
        user["payment_status"] = "pending"
        user["payment_currency"] = currency
        user["payment_plan"] = plan
        user["payment_requested_at"] = _now()
        return dict(user, user_id=user_id)

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO users (
                user_id,
                payment_ref,
                payment_status,
                payment_currency,
                payment_plan,
                payment_requested_at
            )
            VALUES ($1, $2, 'pending', $3, $4, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                payment_ref = EXCLUDED.payment_ref,
                payment_status = 'pending',
                payment_currency = EXCLUDED.payment_currency,
                payment_plan = EXCLUDED.payment_plan,
                payment_requested_at = NOW()
            RETURNING *
        """, user_id, ref_code, currency, plan)
        return dict(row)


async def cancel_pending_payment(user_id: int) -> dict | None:
    """Cancel only the user's active pending upgrade and retain its reference."""
    if not _ready():
        user = _memory_users.get(user_id)
        if not user or user.get("payment_status") != "pending":
            return None

        ref_code = str(user.get("payment_ref") or "")
        if ref_code:
            _memory_cancelled_payment_refs[ref_code] = {
                "user_id": user_id,
                "username": user.get("username"),
                "payment_ref": ref_code,
                "payment_plan": user.get("payment_plan"),
                "payment_currency": user.get("payment_currency"),
                "payment_status": "cancelled",
                "cancelled_at": _now(),
            }

        user["payment_ref"] = None
        user["payment_status"] = "none"
        user["payment_currency"] = None
        user["payment_plan"] = None
        user["payment_requested_at"] = None
        return dict(user, user_id=user_id, cancelled_ref=ref_code or None)

    async with _pool.acquire() as conn:
        async with conn.transaction():
            pending = await conn.fetchrow("""
                SELECT
                    user_id,
                    payment_ref,
                    payment_plan,
                    payment_currency
                FROM users
                WHERE user_id = $1
                  AND payment_status = 'pending'
                FOR UPDATE
            """, user_id)
            if pending is None:
                return None

            ref_code = pending["payment_ref"]
            if ref_code:
                await conn.execute("""
                    INSERT INTO cancelled_payment_refs (
                        payment_ref,
                        user_id,
                        payment_plan,
                        payment_currency,
                        cancelled_at
                    )
                    VALUES ($1, $2, $3, $4, NOW())
                    ON CONFLICT (payment_ref) DO UPDATE SET
                        user_id = EXCLUDED.user_id,
                        payment_plan = EXCLUDED.payment_plan,
                        payment_currency = EXCLUDED.payment_currency,
                        cancelled_at = NOW()
                """,
                    ref_code,
                    pending["user_id"],
                    pending["payment_plan"],
                    pending["payment_currency"],
                )

            row = await conn.fetchrow("""
                UPDATE users
                SET payment_ref = NULL,
                    payment_status = 'none',
                    payment_currency = NULL,
                    payment_plan = NULL,
                    payment_requested_at = NULL
                WHERE user_id = $1
                  AND payment_status = 'pending'
                RETURNING *
            """, user_id)
            if row is None:
                return None
            return dict(row, cancelled_ref=ref_code)


async def get_cancelled_payment_by_ref(ref_code: str) -> dict | None:
    if not _ready():
        row = _memory_cancelled_payment_refs.get(ref_code)
        return dict(row) if row else None

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
                cancelled.user_id,
                users.username,
                cancelled.payment_ref,
                cancelled.payment_plan,
                cancelled.payment_currency,
                'cancelled' AS payment_status,
                cancelled.cancelled_at
            FROM cancelled_payment_refs AS cancelled
            LEFT JOIN users ON users.user_id = cancelled.user_id
            WHERE cancelled.payment_ref = $1
        """, ref_code)
        return dict(row) if row else None


async def get_latest_cancelled_payment_for_user(user_id: int) -> dict | None:
    if not _ready():
        matches = [
            row
            for row in _memory_cancelled_payment_refs.values()
            if row.get("user_id") == user_id
        ]
        if not matches:
            return None
        latest = max(matches, key=lambda row: row["cancelled_at"])
        return dict(latest)

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
                cancelled.user_id,
                users.username,
                cancelled.payment_ref,
                cancelled.payment_plan,
                cancelled.payment_currency,
                'cancelled' AS payment_status,
                cancelled.cancelled_at
            FROM cancelled_payment_refs AS cancelled
            LEFT JOIN users ON users.user_id = cancelled.user_id
            WHERE cancelled.user_id = $1
            ORDER BY cancelled.cancelled_at DESC
            LIMIT 1
        """, user_id)
        return dict(row) if row else None


async def set_payment_confirmed(
    ref_code: str,
    plan: str,
    plan_expires_at: datetime | None,
) -> dict | None:
    if not _ready():
        for user_id, user in _memory_users.items():
            if (
                user.get("payment_ref") == ref_code
                and user.get("payment_status") == "pending"
            ):
                user["plan"] = plan
                user["plan_expires_at"] = plan_expires_at
                user["payment_status"] = "confirmed"
                return dict(user, user_id=user_id)
        return None

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE users
            SET plan = $2,
                plan_expires_at = $3,
                payment_status = 'confirmed'
            WHERE payment_ref = $1
              AND payment_status = 'pending'
            RETURNING *
        """, ref_code, plan, plan_expires_at)
        return dict(row) if row else None


async def set_payment_rejected(ref_code: str) -> dict | None:
    if not _ready():
        for user_id, user in _memory_users.items():
            if (
                user.get("payment_ref") == ref_code
                and user.get("payment_status") == "pending"
            ):
                user["payment_ref"] = None
                user["payment_status"] = "none"
                user["payment_currency"] = None
                user["payment_plan"] = None
                user["payment_requested_at"] = None
                return dict(user, user_id=user_id)
        return None

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE users
            SET payment_ref = NULL,
                payment_status = 'none',
                payment_currency = NULL,
                payment_plan = NULL,
                payment_requested_at = NULL
            WHERE payment_ref = $1
              AND payment_status = 'pending'
            RETURNING *
        """, ref_code)
        return dict(row) if row else None


# ── Payments and plans ────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _memory_payment_row(payment_id: int) -> dict | None:
    payment = _memory_payments.get(payment_id)
    return dict(payment) if payment else None


async def get_pending_payment_for_user(user_id: int) -> dict | None:
    if not _ready():
        pending = [
            payment for payment in _memory_payments.values()
            if payment["user_id"] == user_id and payment["status"] == "pending"
        ]
        if not pending:
            return None
        return dict(sorted(pending, key=lambda p: p["created_at"], reverse=True)[0])

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT * FROM payments
            WHERE user_id = $1 AND status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
        """, user_id)
        return dict(row) if row else None


async def get_pending_payment_waiting_for_receipt(user_id: int) -> dict | None:
    if not _ready():
        pending = [
            payment for payment in _memory_payments.values()
            if (
                payment["user_id"] == user_id
                and payment["status"] == "pending"
                and not payment.get("receipt_file_id")
            )
        ]
        if not pending:
            return None
        return dict(sorted(pending, key=lambda p: p["created_at"], reverse=True)[0])

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT * FROM payments
            WHERE user_id = $1
              AND status = 'pending'
              AND receipt_file_id IS NULL
            ORDER BY created_at DESC
            LIMIT 1
        """, user_id)
        return dict(row) if row else None


async def create_payment_request(
    *,
    user_id: int,
    username: str,
    plan: Plan,
    currency: str,
    amount: str,
) -> dict:
    global _memory_next_payment_id

    if not _ready():
        payment_id = _memory_next_payment_id
        _memory_next_payment_id += 1
        payment = {
            "id": payment_id,
            "user_id": user_id,
            "username": username,
            "plan_key": plan.key,
            "plan_name": plan.name,
            "currency": currency,
            "amount": amount,
            "status": "pending",
            "receipt_file_id": None,
            "receipt_file_type": None,
            "created_at": _now(),
            "reviewed_at": None,
            "reviewed_by": None,
        }
        _memory_payments[payment_id] = payment
        return dict(payment)

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO payments (
                user_id,
                username,
                plan_key,
                plan_name,
                currency,
                amount
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *
        """, user_id, username, plan.key, plan.name, currency, amount)
        return dict(row)


async def attach_payment_receipt(
    payment_id: int,
    receipt_file_id: str,
    receipt_file_type: str,
) -> dict | None:
    if not _ready():
        payment = _memory_payments.get(payment_id)
        if payment is None:
            return None
        payment["receipt_file_id"] = receipt_file_id
        payment["receipt_file_type"] = receipt_file_type
        return dict(payment)

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE payments
            SET receipt_file_id = $2,
                receipt_file_type = $3
            WHERE id = $1
            RETURNING *
        """, payment_id, receipt_file_id, receipt_file_type)
        return dict(row) if row else None


async def get_payment(payment_id: int) -> dict | None:
    if not _ready():
        return _memory_payment_row(payment_id)

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM payments WHERE id = $1", payment_id)
        return dict(row) if row else None


async def set_payment_status(
    payment_id: int,
    status: str,
    reviewed_by: int,
) -> dict | None:
    if status not in {"approved", "rejected"}:
        raise ValueError(f"Invalid payment status: {status}")

    if not _ready():
        payment = _memory_payments.get(payment_id)
        if payment is None or payment["status"] != "pending":
            return None
        payment["status"] = status
        payment["reviewed_at"] = _now()
        payment["reviewed_by"] = reviewed_by
        return dict(payment)

    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE payments
            SET status = $2,
                reviewed_at = NOW(),
                reviewed_by = $3
            WHERE id = $1
              AND status = 'pending'
            RETURNING *
        """, payment_id, status, reviewed_by)
        return dict(row) if row else None


def _store_memory_user_plan(
    user_id: int,
    plan: Plan,
    starts_at: datetime,
    expires_at: datetime,
) -> dict:
    row = {
        "user_id": user_id,
        "plan_key": plan.key,
        "plan_name": plan.name,
        "plan_type": plan.plan_type,
        "starts_at": starts_at,
        "expires_at": expires_at,
        "max_file_size_mb": plan.max_file_size_mb,
        "unlimited_downloads": plan.unlimited_downloads,
        "unlimited_conversions": plan.unlimited_conversions,
        "downloads_remaining": plan.downloads_remaining,
        "conversions_remaining": plan.conversions_remaining,
        "priority_level": plan.priority_level,
        "is_active": True,
    }
    _memory_user_plans[user_id] = row
    return row


async def _upsert_user_plan(
    conn: asyncpg.Connection,
    user_id: int,
    plan: Plan,
    starts_at: datetime,
    expires_at: datetime,
):
    return await conn.fetchrow("""
        INSERT INTO user_plans (
            user_id,
            plan_key,
            plan_name,
            plan_type,
            starts_at,
            expires_at,
            max_file_size_mb,
            unlimited_downloads,
            unlimited_conversions,
            downloads_remaining,
            conversions_remaining,
            priority_level,
            is_active
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, TRUE)
        ON CONFLICT (user_id) DO UPDATE SET
            plan_key = EXCLUDED.plan_key,
            plan_name = EXCLUDED.plan_name,
            plan_type = EXCLUDED.plan_type,
            starts_at = EXCLUDED.starts_at,
            expires_at = EXCLUDED.expires_at,
            max_file_size_mb = EXCLUDED.max_file_size_mb,
            unlimited_downloads = EXCLUDED.unlimited_downloads,
            unlimited_conversions = EXCLUDED.unlimited_conversions,
            downloads_remaining = EXCLUDED.downloads_remaining,
            conversions_remaining = EXCLUDED.conversions_remaining,
            priority_level = EXCLUDED.priority_level,
            is_active = TRUE
        RETURNING *
    """,
        user_id,
        plan.key,
        plan.name,
        plan.plan_type,
        starts_at,
        expires_at,
        plan.max_file_size_mb,
        plan.unlimited_downloads,
        plan.unlimited_conversions,
        plan.downloads_remaining,
        plan.conversions_remaining,
        plan.priority_level,
    )


async def approve_upgrade_payment(
    ref_code: str,
    plan: Plan,
) -> tuple[dict, dict] | None:
    """Confirm a pending upgrade and activate its plan as one operation."""
    starts_at = _now()
    expires_at = starts_at + timedelta(days=plan.duration_days)

    if not _ready():
        for user_id, user in _memory_users.items():
            if (
                user.get("payment_ref") == ref_code
                and user.get("payment_status") == "pending"
            ):
                plan_row = _store_memory_user_plan(user_id, plan, starts_at, expires_at)
                user["plan"] = plan.key
                user["plan_expires_at"] = expires_at
                user["payment_status"] = "confirmed"
                return dict(user, user_id=user_id), dict(plan_row)
        return None

    async with _pool.acquire() as conn:
        async with conn.transaction():
            user_row = await conn.fetchrow("""
                UPDATE users
                SET plan = $2,
                    plan_expires_at = $3,
                    payment_status = 'confirmed'
                WHERE payment_ref = $1
                  AND payment_status = 'pending'
                RETURNING *
            """, ref_code, plan.key, expires_at)
            if user_row is None:
                return None

            plan_row = await _upsert_user_plan(
                conn,
                user_row["user_id"],
                plan,
                starts_at,
                expires_at,
            )
            return dict(user_row), dict(plan_row)


async def activate_user_plan(user_id: int, plan: Plan) -> dict:
    starts_at = _now()
    expires_at = starts_at + timedelta(days=plan.duration_days)

    if not _ready():
        row = _store_memory_user_plan(user_id, plan, starts_at, expires_at)
        user = _memory_register_user(user_id)
        user["plan"] = plan.key
        user["plan_expires_at"] = expires_at
        user["payment_status"] = "confirmed"
        return dict(row)

    async with _pool.acquire() as conn:
        async with conn.transaction():
            row = await _upsert_user_plan(conn, user_id, plan, starts_at, expires_at)
            await conn.execute("""
                INSERT INTO users (
                    user_id,
                    plan,
                    plan_expires_at,
                    payment_status
                )
                VALUES ($1, $2, $3, 'confirmed')
                ON CONFLICT (user_id) DO UPDATE SET
                    plan = EXCLUDED.plan,
                    plan_expires_at = EXCLUDED.plan_expires_at,
                    payment_status = 'confirmed'
            """, user_id, plan.key, expires_at)
            return dict(row)


async def get_user_plan_details(user_id: int) -> dict | None:
    if not _ready():
        plan = _memory_user_plans.get(user_id)
        return dict(plan) if plan else None

    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM user_plans WHERE user_id = $1",
            user_id,
        )
        return dict(row) if row else None


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL pool closed.")
