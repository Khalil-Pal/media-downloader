"""
handlers/common.py – Shared keyboards and URL token store.
"""
from __future__ import annotations

import uuid
from collections import OrderedDict

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from utils.i18n import t

# ── URL Token Store ───────────────────────────────────────────────────────────

_URL_STORE: OrderedDict[str, str] = OrderedDict()
_URL_STORE_MAX = 500  # max simultaneous pending quality-picker sessions


def _store_url(url: str) -> str:
    """Persist *url* and return a 12-character hex token for callback_data."""
    token = uuid.uuid4().hex[:12]
    _URL_STORE[token] = url

    if len(_URL_STORE) > _URL_STORE_MAX:
        _URL_STORE.popitem(last=False)
    return token


def resolve_url(token: str) -> str | None:
    """Return the URL stored under *token*, or None if it has expired / is unknown."""
    return _URL_STORE.get(token)


# ── Keyboards ─────────────────────────────────────────────────────────────────

def quality_keyboard(url: str, lang: str = "en") -> InlineKeyboardMarkup:
    """
    Inline keyboard for quality selection.

    Uses a short URL token instead of embedding the raw URL so that
    callback_data never exceeds Telegram's 64-byte hard limit.
    Max callback_data length here: len("quality:audio:") + 12 = 26 bytes.
    """
    token = _store_url(url)
    builder = InlineKeyboardBuilder()
     (t(lang, "btn_best"),  "quality:best:"  + token),
        (t(lang, "btn_720p"),  "quality:720:"   + token),
        (t(lang, "btn_480p"),  "quality:480:"   + token),
        (t(lang, "btn_360p"),  "quality:360:"   + token),
        (t(lang, "btn_144p"),  "quality:144:"   + token),
        (t(lang, "btn_audio"), "quality:audio:" + token),
    ]
    for label, data in options:
        builder.button(text=label, callback_data=data)
    builder.adjust(2)
    return builder.as_markup()


def cancel_keyboard(user_id: int, lang: str = "en") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
builder.button(text=t(lang, "btn_cancel"), 
callback_data="cancel:" + str(user_id))
    return builder.as_markup()
