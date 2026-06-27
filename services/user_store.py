from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Single file for everything — easier to persist on Railway
_STORE_FILE = Path("./data/user_store.json")
_languages: dict[int, str] = {}
_all_users: set[int] = set()


def _load() -> None:
    try:
        _STORE_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Try new unified format first
        if _STORE_FILE.exists():
            raw = json.loads(_STORE_FILE.read_text(encoding="utf-8"))
            _languages.update({int(k): v for k, v in raw.get("languages", {}).items()})
            _all_users.update(int(u) for u in raw.get("users", []))
            logger.info("Loaded %d users, %d languages from store", len(_all_users), len(_languages))
            return

        # Fall back to old separate files if they exist
        old_lang = Path("./data/user_languages.json")
        old_users = Path("./data/all_users.json")

        if old_lang.exists():
            raw = json.loads(old_lang.read_text(encoding="utf-8"))
            _languages.update({int(k): v for k, v in raw.items()})

        if old_users.exists():
            raw2 = json.loads(old_users.read_text(encoding="utf-8"))
            _all_users.update(int(u) for u in raw2)

        # Always include language users in all_users
        _all_users.update(_languages.keys())

        # Migrate to new format
        if _languages or _all_users:
            _save()
            logger.info("Migrated old store files → %s", _STORE_FILE)

    except Exception as exc:
        logger.warning("Could not load user store: %s", exc)


def _save() -> None:
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
        logger.warning("Could not save user store: %s", exc)


def register_user(user_id: int) -> None:
    """Track every user who interacts with the bot."""
    if user_id not in _all_users:
        _all_users.add(user_id)
        _save()


def get_all_user_ids() -> list[int]:
    return list(_all_users)


def user_count() -> int:
    return len(_all_users)


def get_user_lang(user_id: int):
    return _languages.get(user_id)


def get_user_lang_or_default(user_id: int, default: str = "en") -> str:
    return _languages.get(user_id) or default


def set_user_lang(user_id: int, lang: str) -> None:
    _languages[user_id] = lang
    _all_users.add(user_id)  # also register them
    _save()


def has_chosen_language(user_id: int) -> bool:
    return user_id in _languages


_load()