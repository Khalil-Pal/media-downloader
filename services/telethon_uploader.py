"""
services/telethon_uploader.py – Large file uploader using Telethon (up to 2GB)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession

from config.settings import settings

logger = logging.getLogger(__name__)

# Session string stored in env to avoid file system dependency on Railway
_SESSION_STRING = os.getenv("TELETHON_SESSION", "")

_client: TelegramClient | None = None


async def get_client() -> TelegramClient:
    """Return an authenticated Telethon client (singleton)."""
    global _client

    if _client and _client.is_connected():
        return _client

    _client = TelegramClient(
        StringSession(_SESSION_STRING),
        settings.api_id,
        settings.api_hash,
    )

    await _client.connect()

    if not await _client.is_user_authorized():
        logger.error("Telethon client is not authorized. Run generate_session.py first.")
        raise RuntimeError("Telethon session not authorized.")

    return _client


async def upload_large_file(
    chat_id: int,
    file_path: Path,
    caption: str,
    is_audio: bool = False,
) -> None:
    """Upload a file up to 2GB via Telethon user account."""
    client = await get_client()

    # Resolve numeric chat_id for Telethon
    entity = await client.get_entity(chat_id)

    if is_audio:
        await client.send_file(
            entity,
            str(file_path),
            caption=caption,
            voice=False,
            attributes=[],
        )
    else:
        await client.send_file(
            entity,
            str(file_path),
            caption=caption,
            supports_streaming=True,
        )

    logger.info("Large file uploaded via Telethon: %s", file_path.name)