"""
handlers/common.py – Shared keyboards, URL token store, and static message text.

Why the URL token store?
    Telegram caps callback_data at 64 bytes. Pasting a full URL directly into
    callback_data silently truncates anything past byte 64, corrupting the URL.
    Instead we store the real URL in a small in-process dict and put only a
    12-character token in the callback. The token round-trips safely and the
    actual URL is looked up in cb_quality() via resolve_url().
"""
from __future__ import annotations

import uuid
from collections import OrderedDict

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ── URL Token Store ───────────────────────────────────────────────────────────

_URL_STORE: OrderedDict[str, str] = OrderedDict()
_URL_STORE_MAX = 500  # max simultaneous pending quality-picker sessions


def _store_url(url: str) -> str:
    """Persist *url* and return a 12-character hex token for callback_data."""
    token = uuid.uuid4().hex[:12]
    _URL_STORE[token] = url
    # Evict oldest entry if we've grown too large
    if len(_URL_STORE) > _URL_STORE_MAX:
        _URL_STORE.popitem(last=False)
    return token


def resolve_url(token: str) -> str | None:
    """Return the URL stored under *token*, or None if it has expired / is unknown."""
    return _URL_STORE.get(token)


# ── Keyboards ─────────────────────────────────────────────────────────────────

def quality_keyboard(url: str) -> InlineKeyboardMarkup:
    """
    Inline keyboard for quality selection.

    Uses a short URL token instead of embedding the raw URL so that
    callback_data never exceeds Telegram's 64-byte hard limit.
    Max callback_data length here: len("quality:audio:") + 12 = 26 bytes.
    """
    token = _store_url(url)
    builder = InlineKeyboardBuilder()
    options = [
        ("🥇 Best",       f"quality:best:{token}"),
        ("📺 720p",       f"quality:720:{token}"),
        ("📱 480p",       f"quality:480:{token}"),
        ("🔻 360p",       f"quality:360:{token}"),
        ("🔍 144p",       f"quality:144:{token}"),
        ("🎵 Audio only", f"quality:audio:{token}"),
    ]
    for label, data in options:
        builder.button(text=label, callback_data=data)
    builder.adjust(2)
    return builder.as_markup()


def cancel_keyboard(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Cancel", callback_data=f"cancel:{user_id}")
    return builder.as_markup()


# ── Static message text ───────────────────────────────────────────────────────

WELCOME_TEXT = """
🐿️ *Welcome to Sandy Squirrel!*

I can download videos and audio from YouTube, Instagram, *Threads*, Facebook, TikTok, Twitter/X, Vimeo, Reddit, and more.

*How to use me:*
• Just paste a video URL and I'll download it for you
• Use /download or /audio followed by a URL
• Choose your preferred quality when prompted

*Commands:*
/start — Show this message
/help — Detailed instructions
/download `<url>` — Download video
/audio `<url>` — Download audio only
/quality — Supported quality options
/cancel — Cancel current download
/stats — Bot statistics _(admin only)_

*Supported platforms:*
▶️ YouTube  📸 Instagram  🧵 Threads
👤 Facebook  🎵 TikTok  🐦 Twitter/X
🎬 Vimeo  🔗 Reddit  📅 Dailymotion

Ready to download? Send me a link! 🚀
""".strip()

HELP_TEXT = """
📖 *Sandy Squirrel – Help Guide*

*Downloading a video:*
Simply paste a URL, or use:
`/download https://youtube.com/watch?v=...`

*Threads example:*
`/download https://www.threads.net/@user/post/ABC123`

*Downloading audio only:*
`/audio https://youtube.com/watch?v=...`

*Quality selection:*
After sending a URL you'll get quality options:
• 🥇 Best – Highest available quality
• 📺 720p – HD video
• 📱 480p – Standard definition
• 🔻 360p – Low bandwidth
• 🔍 144p – Minimum quality
• 🎵 Audio – MP3 audio only

*Limitations:*
• Max file size: 2 GB (files over 50 MB upload via Telethon)
• Private/age-restricted content is not supported
• Geo-blocked content may not be available

*Tips:*
• For large YouTube videos, try 360p or audio-only
• Instagram Reels, Stories, and Threads posts are supported
• Use /cancel if a download is taking too long

Need more help? Just send a URL and I'll handle the rest! 🐿️
""".strip()