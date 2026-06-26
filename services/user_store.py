"""
Stores language choices in memory with JSON file-backed persistence so
preferences survive bot restarts without requiring a database.

Stored values: "en", "ar", "ru"
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_STORE_FILE = Path("./data/user_languages.json")
_languages: dict[int, str] = {}


def _load() -> None:
    """Load persisted language map from disk."""
    try:
        _STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _STORE_FILE.exists():
            raw = json.loads(_STORE_FILE.read_text(encoding="utf-8"))
            _languages.update({int(k): v for k, v in raw.items()})
            logger.debug("Loaded %d user language records", len(_languages))
    except Exception as exc:
        logger.warning("Could not load user language store: %s", exc)


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


def get_user_lang(user_id: int) -> str | None:
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
