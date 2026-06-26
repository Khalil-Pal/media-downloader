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

_LOCALES_DIR = Path(__file__).parent.parent / "locales"
_translations: dict[str, dict[str, str]] = {}


def load_translations() -> None:
    """Load all locale JSON files into memory. Call once at startup."""
    for lang in SUPPORTED_LANGUAGES:
        path = _LOCALES_DIR / f"{lang}.json"
        try:
            with path.open(encoding="utf-8") as f:
                _translations[lang] = json.load(f)
            logger.debug("Loaded locale: %s (%d keys)", lang, len(_translations[lang]))
        except FileNotFoundError:
            logger.warning("Locale file not found: %s", path)
            _translations[lang] = {}
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse locale file %s: %s", path, exc)
            _translations[lang] = {}


def t(lang: str | None, key: str, **kwargs: object) -> str:
    """
    Return the translated string for *key* in *lang*.

    Falls back to English if the key is missing in the requested language.
    Supports optional str.format_map() substitution via **kwargs.
    """
    effective_lang = lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE

    text = (
        _translations.get(effective_lang, {}).get(key)
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