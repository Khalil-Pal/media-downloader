"""
handlers/common.py – Shared keyboards, message builders, and typing helpers
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def quality_keyboard(url: str) -> InlineKeyboardMarkup:
    """Inline keyboard for quality selection."""
    builder = InlineKeyboardBuilder()
    options = [
        ("🥇 Best", f"quality:best:{url}"),
        ("📺 720p", f"quality:720:{url}"),
        ("📱 480p", f"quality:480:{url}"),
        ("🔻 360p", f"quality:360:{url}"),
        ("🔍 144p", f"quality:144:{url}"),
        ("🎵 Audio only", f"quality:audio:{url}"),
    ]
    for label, data in options:
        # Callback data max 64 bytes — truncate URL if needed
        payload = data[:64]
        builder.button(text=label, callback_data=payload)
    builder.adjust(2)
    return builder.as_markup()


def cancel_keyboard(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Cancel", callback_data=f"cancel:{user_id}")
    return builder.as_markup()


WELCOME_TEXT = """
🐿️ *Welcome to Sandy Squirrel!*

I can download videos and audio from YouTube, Instagram, Facebook, TikTok, Twitter/X, Vimeo, and more.

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
▶️ YouTube  📸 Instagram  👤 Facebook
🎵 TikTok  🐦 Twitter/X  🎬 Vimeo  🔗 Reddit

Ready to download? Send me a link! 🚀
""".strip()

HELP_TEXT = """
📖 *Sandy Squirrel – Help Guide*

*Downloading a video:*
Simply paste a URL, or use:
`/download https://youtube.com/watch?v=...`

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
• Max file size: 50 MB
• Private/age-restricted content is not supported
• Geo-blocked content may not be available

*Tips:*
• For large YouTube videos, try 360p or audio-only
• Instagram Reels and Stories are supported
• Use /cancel if a download is taking too long

Need more help? Just send a URL and I'll handle the rest! 🐿️
""".strip()
