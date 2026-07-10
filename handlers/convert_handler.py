"""File conversion handlers."""
from __future__ import annotations

import logging
import mimetypes
import re
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, FSInputFile, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config.settings import settings
from services.converter import (
    cleanup_conversion_session,
    convert_file,
    create_conversion_session,
    friendly_conversion_error,
    supported_targets,
)
from services.telethon_uploader import upload_large_file
from services.user_store import get_user_lang_or_default, get_user_mode_or_default, register_user
from utils import rate_limiter
from utils.i18n import t
from handlers.language import ensure_mode_selected

logger = logging.getLogger(__name__)
router = Router(name="converter")

SMALL_FILE_LIMIT = 50 * 1024 * 1024
_CONVERSION_STORE_MAX = 500
_conversion_store: OrderedDict[str, "ConversionRequest"] = OrderedDict()
_active_conversions: set[int] = set()


@dataclass(frozen=True)
class ConversionRequest:
    user_id: int
    file_id: str
    file_name: str
    mime_type: str | None
    file_size: int | None
    targets: tuple[str, ...]


def _store_request(request: ConversionRequest) -> str:
    token = uuid.uuid4().hex[:12]
    _conversion_store[token] = request
    if len(_conversion_store) > _CONVERSION_STORE_MAX:
        _conversion_store.popitem(last=False)
    return token


def _resolve_request(token: str) -> ConversionRequest | None:
    return _conversion_store.get(token)


def _safe_filename(file_name: str) -> str:
    name = Path(file_name).name.strip() or "file"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name[:120] or "file"


def _file_payload(message: Message) -> tuple[str, str, str | None, int | None] | None:
    file_obj = message.document or message.video or message.audio
    if not file_obj:
        return None

    file_id = file_obj.file_id
    mime_type = getattr(file_obj, "mime_type", None)
    file_size = getattr(file_obj, "file_size", None)
    file_name = getattr(file_obj, "file_name", None)

    if not file_name:
        if message.video:
            file_name = "video.mp4"
        elif message.audio:
            guessed = mimetypes.guess_extension(mime_type or "") or ".mp3"
            file_name = "audio" + guessed
        else:
            guessed = mimetypes.guess_extension(mime_type or "") or ".bin"
            file_name = "file" + guessed

    return file_id, _safe_filename(file_name), mime_type, file_size


def _conversion_keyboard(token: str, targets: tuple[str, ...], lang: str):
    builder = InlineKeyboardBuilder()
    for target in targets:
        builder.button(
            text=t(lang, "btn_convert_to", format=target.upper()),
            callback_data=f"convert:{target}:{token}",
        )
    builder.adjust(2)
    return builder.as_markup()


@router.message(F.document | F.video | F.audio)
async def handle_convertible_file(message: Message) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]
    await register_user(user_id)
    lang = await get_user_lang_or_default(user_id)
    if not await ensure_mode_selected(message, lang):
        return

    payload = _file_payload(message)
    if not payload:
        return

    file_id, file_name, mime_type, file_size = payload
    options = supported_targets(file_name, mime_type)

    mode = await get_user_mode_or_default(user_id)
    if mode == "downloader" and options:
        await message.answer(t(lang, "mode_need_converter"))
        return

    if file_size and file_size > settings.max_convert_file_size_bytes:
        await message.answer(
            t(lang, "conversion_too_large", limit=settings.max_convert_file_size_mb)
        )
        return

    if not options:
        await message.answer(t(lang, "conversion_unsupported"))
        return

    targets = tuple(option.target_format for option in options)
    token = _store_request(
        ConversionRequest(
            user_id=user_id,
            file_id=file_id,
            file_name=file_name,
            mime_type=mime_type,
            file_size=file_size,
            targets=targets,
        )
    )

    await message.answer(
        t(lang, "conversion_choose", filename=file_name),
        reply_markup=_conversion_keyboard(token, targets, lang),
    )


@router.callback_query(F.data.startswith("convert:"))
async def cb_convert(callback: CallbackQuery, bot: Bot) -> None:
    user_id = callback.from_user.id
    lang = await get_user_lang_or_default(user_id)
    if callback.message is None:
        await callback.answer(t(lang, "conversion_expired"), show_alert=True)
        return

    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3:
        await callback.answer(t(lang, "invalid_selection"), show_alert=True)
        return

    _, target_format, token = parts
    request = _resolve_request(token)
    if not request:
        await callback.answer(t(lang, "conversion_expired"), show_alert=True)
        return

    if request.user_id != user_id:
        await callback.answer(t(lang, "conversion_wrong_user"), show_alert=True)
        return

    if target_format not in request.targets:
        await callback.answer(t(lang, "invalid_selection"), show_alert=True)
        return

    allowed, reason = await rate_limiter.check(user_id)
    if not allowed:
        await callback.answer(reason, show_alert=True)
        return

    if user_id in _active_conversions:
        await callback.answer(t(lang, "active_conversion_warning"), show_alert=True)
        return

    await callback.answer()
    _active_conversions.add(user_id)
    _conversion_store.pop(token, None)
    session_dir: Path | None = None
    status_msg = callback.message

    try:
        if status_msg:
            await status_msg.edit_text(t(lang, "conversion_converting"))

        session_dir = create_conversion_session()
        input_path = session_dir / request.file_name
        with input_path.open("wb") as destination:
            await bot.download(request.file_id, destination=destination)

        output_path = await convert_file(input_path, target_format, request.mime_type)

        if status_msg:
            await status_msg.edit_text(t(lang, "conversion_uploading"))

        caption = t(lang, "conversion_result_caption", filename=output_path.name)
        if output_path.stat().st_size > SMALL_FILE_LIMIT:
            is_audio = output_path.suffix.lower() == ".mp3"
            await upload_large_file(
                chat_id=callback.message.chat.id,
                file_path=output_path,
                caption=caption,
                is_audio=is_audio,
                supports_streaming=False,
                force_document=not is_audio,
            )
        elif output_path.suffix.lower() == ".mp3":
            await bot.send_audio(
                chat_id=callback.message.chat.id,
                audio=FSInputFile(output_path),
                caption=caption,
            )
        else:
            await bot.send_document(
                chat_id=callback.message.chat.id,
                document=FSInputFile(output_path),
                caption=caption,
            )

        if status_msg:
            await status_msg.delete()

    except Exception as exc:
        logger.exception("Conversion failed for %s to %s", request.file_name, target_format)
        if status_msg:
            await status_msg.edit_text(t(lang, friendly_conversion_error(exc)))

    finally:
        _active_conversions.discard(user_id)
        cleanup_conversion_session(session_dir)
