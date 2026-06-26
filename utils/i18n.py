"""
Translation loader and text retrieval.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ("en", "ar", "ru")
DEFAULT_LANGUAGE = "en"
_translations: dict[str, dict[str, str]] = {}


def _find_locale_file(lang: str) -> Path | None:
    here = Path(__file__).resolve().parent.parent
    cwd = Path.cwd()

    candidates = [
        here / "locales" / (lang + ".json"),
        cwd  / "locales" / (lang + ".json"),
        here / (lang + ".json"),
        cwd  / (lang + ".json"),
    ]

    for path in candidates:
        if path.is_file():
            return path

    return None


def load_translations() -> None:
    """Load all locale JSON files into memory. Called once at module import."""
    for lang in SUPPORTED_LANGUAGES:
        path = _find_locale_file(lang)
        if path is None:
            logger.warning("Locale file not found for: %s", lang)
            _translations[lang] = {}
            continue
        try:
           with path.open(encoding="utf-8") as fh:
                _translations[lang] = json.load(fh)
        except Exception as exc:
            logger.error("Failed to load locale %s: %s", lang, exc)
            _translations[lang] = {}


def t(lang: str | None, key: str, **kwargs: object) -> str:

   effective = lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE

    text = (
        _translations.get(effective, {}).get(key)
        or _translations.get(DEFAULT_LANGUAGE, {}).get(key)
        or key
    )

    if kwargs:
        try:
            text = text.format_map(kwargs)
        except (KeyError, ValueError):
            pass

    return text


load_translations()