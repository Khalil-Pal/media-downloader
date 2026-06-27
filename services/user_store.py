from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# ── In-memory cache ───────────────────────────────────────────────────────────
_languages: dict[int, str] = {}
_all_users: set[int] = set()

# ── Storage backend ───────────────────────────────────────────────────────────
# Uses Postgres if DATABASE_URL is set, otherwise falls back to local JSON file.

_DATABASE_URL = os.getenv("DATABASE_URL", "")
_STORE_FILE   = Path("./data/user_store.json")
_USE_PG       = bool(_DATABASE_URL)

# ── Postgres helpers ──────────────────────────────────────────────────────────

def _pg_connect():
    import psycopg2
    return psycopg2.connect(_DATABASE_URL, sslmode="require")


def _pg_setup() -> None:
    """Create tables if they don't exist yet."""
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_users (
                    user_id BIGINT PRIMARY KEY,
                    lang    VARCHAR(10)
                );
            """)
        conn.commit()
    logger.info("Postgres tables ready")


def _pg_load() -> None:
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, lang FROM bot_users;")
            for user_id, lang in cur.fetchall():
                _all_users.add(user_id)
                if lang:
                    _languages[user_id] = lang
    logger.info("Loaded %d users from Postgres", len(_all_users))


def _pg_upsert(user_id: int, lang: str | None = None) -> None:
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO bot_users (user_id, lang)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE
                    SET lang = COALESCE(EXCLUDED.lang, bot_users.lang);
            """, (user_id, lang))
        conn.commit()


# ── JSON file helpers ─────────────────────────────────────────────────────────

def _file_load() -> None:
    try:
        _STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _STORE_FILE.exists():
            raw = json.loads(_STORE_FILE.read_text(encoding="utf-8"))
            _languages.update({int(k): v for k, v in raw.get("languages", {}).items()})
            _all_users.update(int(u) for u in raw.get("users", []))
            _all_users.update(_languages.keys())
            logger.info("Loaded %d users from file", len(_all_users))
        # migrate old separate files
        for old in [Path("./data/user_languages.json"), Path("./data/all_users.json")]:
            if old.exists():
                try:
                    raw2 = json.loads(old.read_text(encoding="utf-8"))
                    if isinstance(raw2, dict):
                        _languages.update({int(k): v for k, v in raw2.items()})
                    elif isinstance(raw2, list):
                        _all_users.update(int(u) for u in raw2)
                    _all_users.update(_languages.keys())
                except Exception:
                    pass
    except Exception as exc:
        logger.warning("Could not load user store file: %s", exc)


def _file_save() -> None:
    try:
        _STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STORE_FILE.write_text(
            json.dumps({
                "languages": {str(k): v for k, v in _languages.items()},
                "users": list(_all_users),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Could not save user store file: %s", exc)


# ── Public API ────────────────────────────────────────────────────────────────

def register_user(user_id: int) -> None:
    if user_id not in _all_users:
        _all_users.add(user_id)
        if _USE_PG:
            try:
                _pg_upsert(user_id)
            except Exception as exc:
                logger.warning("Postgres register_user failed: %s", exc)
        else:
            _file_save()


def set_user_lang(user_id: int, lang: str) -> None:
    _languages[user_id] = lang
    _all_users.add(user_id)
    if _USE_PG:
        try:
            _pg_upsert(user_id, lang)
        except Exception as exc:
            logger.warning("Postgres set_user_lang failed: %s", exc)
    else:
        _file_save()


def get_user_lang(user_id: int):
    return _languages.get(user_id)


def get_user_lang_or_default(user_id: int, default: str = "en") -> str:
    return _languages.get(user_id) or default


def has_chosen_language(user_id: int) -> bool:
    return user_id in _languages


def get_all_user_ids() -> list[int]:
    return list(_all_users)


def user_count() -> int:
    return len(_all_users)


# ── Startup ───────────────────────────────────────────────────────────────────

def _init() -> None:
    if _USE_PG:
        try:
            _pg_setup()
            _pg_load()
            logger.info("Using Postgres for user storage")
        except Exception as exc:
            logger.error("Postgres init failed, falling back to file: %s", exc)
            _file_load()
    else:
        logger.info("Using file storage for users (no DATABASE_URL set)")
        _file_load()


_init()