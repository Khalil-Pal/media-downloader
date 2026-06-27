from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_STORE_FILE = Path("./data/user_languages.json")
_USERS_FILE = Path("./data/all_users.json")
_languages: dict[int, str] = {}
_all_users: set[int] = set()

def _load() -> None:
    """Load persisted language map from disk."""
    try:
        _STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _STORE_FILE.exists():
            raw = json.loads(_STORE_FILE.read_text(encoding="utf-8"))
            _languages.update({int(k): v for k, v in raw.items()})
    except Exception as exc:
        logger.warning("Could not load user language store: %s", exc)
    try:
        if _USERS_FILE.exists():
            raw2 = json.loads(_USERS_FILE.read_text(encoding="utf-8"))
            _all_users.update(int(u) for u in raw2)
    except Exception as exc:
        logger.warning("Could not load all-users store: %s", exc)


def _save() -> None:
    """Persist language map to disk."""
    try:
        _STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STORE_FILE.write_text(
            json.dumps({str(k): v for k, v in _languages.items()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Could not save user language store: %s", exc)


def _save_users() -> None:
    try:
        _USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _USERS_FILE.write_text(
            json.dumps(list(_all_users), ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Could not save all-users store: %s", exc)


def register_user(user_id: int) -> None:
    if user_id not in _all_users:
        _all_users.add(user_id)
        _save_users()


def get_all_user_ids() -> list[int]:
    return list(_all_users)


def user_count() -> int:
    return len(_all_users)


def get_user_lang(user_id: int):
    """Return the user's chosen language code, or None if not yet selected."""
    return _languages.get(user_id)


def get_user_lang_or_default(user_id: int, default: str = "en") -> str:
    """Return the user's language code, defaulting to *default* if not set."""
    return _languages.get(user_id) or default


def set_user_lang(user_id: int, lang: str) -> None:
    """Persist a language choice for a user."""
    _languages[user_id] = lang
    _save()


def has_chosen_language(user_id: int) -> bool:
    """True if the user has already picked a language."""
    return user_id in _languages


_load()