"""
services/telethon_uploader.py – Large-file sender through Telethon as the BOT.

Files larger than 50 MB are sent through Telegram's MTProto API using the
same BOT_TOKEN as the aiogram bot. This file deliberately does NOT use a
personal TELETHON_SESSION, phone number, or verification code.

Required Railway variables:
  BOT_TOKEN
  API_ID
  API_HASH
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from config.settings import settings

logger = logging.getLogger(__name__)

# The client is kept for the lifetime of the Railway container. The session is
# intentionally in memory: on a container restart Telethon authenticates again
# with BOT_TOKEN, never with a personal Telegram account.
_client: Any | None = None


def _telethon_configured() -> bool:
    """Return True only when Telethon can authenticate as the bot."""
    return bool(settings.bot_token and settings.api_id and settings.api_hash)


async def get_client():
    """Return an authenticated Telethon client logged in as Sandy Squirrel."""
    global _client

    if not _telethon_configured():
        raise RuntimeError(
            "Telethon bot upload is not configured. Set BOT_TOKEN, API_ID, "
            "and API_HASH in Railway. Do not use TELETHON_SESSION."
        )

    # Import lazily so the normal bot can still start when Telethon is absent.
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    if _client is not None and _client.is_connected():
        return _client

    # Empty StringSession = in-memory session. BOT_TOKEN authenticates the
    # account on every new Railway container; no personal session is stored.
    _client = TelegramClient(
        StringSession(),
        settings.api_id,
        settings.api_hash,
        device_model="Sandy Squirrel",
        system_version="Railway",
        app_version="1.0",
    )

    await _client.start(bot_token=settings.bot_token)

    me = await _client.get_me()
    if me is None or not getattr(me, "bot", False):
        await _client.disconnect()
        _client = None
        raise RuntimeError(
            "Telethon did not authenticate as a bot. Remove TELETHON_SESSION "
            "and verify BOT_TOKEN, API_ID, and API_HASH."
        )

    logger.info("Telethon authenticated as bot @%s", me.username or me.id)
    return _client


async def close_client() -> None:
    """Disconnect the cached Telethon client during graceful shutdown."""
    global _client

    if _client is None:
        return

    try:
        if _client.is_connected():
            await _client.disconnect()
            logger.info("Telethon client disconnected.")
    finally:
        _client = None


async def _resolve_chat(client, chat_id: int):
    """Resolve the MTProto peer for a user, group, or channel chat ID."""
    try:
        return await client.get_input_entity(chat_id)
    except (TypeError, ValueError):
        # This refreshes the bot's dialog/entity cache. It is needed because
        # aiogram receives updates through Bot API while Telethon is used only
        # for the large-file upload.
        await client.get_dialogs(limit=200)
        return await client.get_input_entity(chat_id)


async def upload_large_file(
    chat_id: int,
    file_path: Path,
    caption: str,
    is_audio: bool = False,
) -> None:
    """Send a file through Telethon, authored by the bot account."""
    if not _telethon_configured():
        raise RuntimeError(
            "Large-file upload requires BOT_TOKEN, API_ID, and API_HASH in Railway."
        )

    if not file_path.exists():
        raise RuntimeError(f"File does not exist: {file_path.name}")

    # Keep the product limit explicit. Telegram's standard non-Premium limit is
    # 2 GB for a single file.
    max_bytes = 2 * 1024 * 1024 * 1024
    if file_path.stat().st_size > max_bytes:
        raise RuntimeError("This file is larger than the 2 GB upload limit.")

    client = await get_client()
    entity = await _resolve_chat(client, chat_id)

    logger.info(
        "Starting Telethon BOT upload: %s (%.1f MB)",
        file_path.name,
        file_path.stat().st_size / (1024 * 1024),
    )

    try:
        await client.send_file(
            entity,
            file=str(file_path),
            caption=caption,
            parse_mode="html",
            force_document=False,
            supports_streaming=not is_audio,
        )
    except Exception:
        logger.exception("Telethon bot upload failed for %s", file_path.name)
        raise

    logger.info("Large file sent by bot via Telethon: %s", file_path.name)
