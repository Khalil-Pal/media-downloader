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
    """
    Search several candidate locations for <lang>.json.
    Supports both layouts:
      - locales/<lang>.json  (preferred)
      - <lang>.json          (root-level fallback)
    """
    here = Path(__file__).resolve().parent.parent  # project root when installed normally
    cwd = Path.cwd()

    candidates = [
        here / "locales" / f"{lang}.json",
        cwd / "locales" / f"{lang}.json",
        here / f"{lang}.json",
        cwd / f"{lang}.json",
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
            logger.warning("Locale file not found for language: %s (tried locales/%s.json and %s.json)", lang, lang, lang)
            _translations[lang] = {}
            continue
        try:
            with path.open(encoding="utf-8") as f:
                _translations[lang] = json.load(f)
            logger.debug("Loaded locale %s from %s (%d keys)", lang, path, len(_translations[lang]))
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