from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123456:test-token")

from config.settings import settings
from handlers import convert_handler, downloader_handler
from handlers.menu import _my_plan_text
from services import db
from services.downloader import DownloadResult, MediaInfo
from services.payments import (
    FREE_DAILY_LIMITS,
    FREE_MAX_FILE_SIZE_MB,
    check_plan_access,
    record_plan_usage,
)
from services.plans import PLANS
from utils import rate_limiter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MEBIBYTE = 1024 * 1024


def _message(
    user_id: int,
    *,
    document: SimpleNamespace | None = None,
) -> tuple[SimpleNamespace, SimpleNamespace]:
    status = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
    message = SimpleNamespace(
        from_user=SimpleNamespace(
            id=user_id,
            username=f"user_{user_id}",
            first_name="Plan",
            last_name="Tester",
        ),
        chat=SimpleNamespace(id=user_id),
        message_id=100,
        text=None,
        caption=None,
        photo=None,
        document=document,
        video=None,
        audio=None,
        answer=AsyncMock(return_value=status),
    )
    return message, status


def _document(
    user_id: int,
    *,
    file_name: str = "sample.png",
    mime_type: str = "image/png",
    file_size: int = 1024,
) -> tuple[SimpleNamespace, SimpleNamespace]:
    return _message(
        user_id,
        document=SimpleNamespace(
            file_id=f"file-{user_id}",
            file_name=file_name,
            mime_type=mime_type,
            file_size=file_size,
        ),
    )


class PlanEnforcementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._original_download_path = settings.download_path
        self._original_admin_id = settings.admin_id
        object.__setattr__(settings, "download_path", Path(self._temp_dir.name))
        object.__setattr__(settings, "admin_id", 999_999)

        db._pool = None
        db._memory_users.clear()
        db._memory_user_plans.clear()
        db._memory_payments.clear()
        db._memory_cancelled_payment_refs.clear()
        convert_handler._conversion_store.clear()
        convert_handler._active_conversions.clear()
        downloader_handler._active_downloads.clear()
        rate_limiter._windows.clear()
        rate_limiter._last_request.clear()

    def tearDown(self) -> None:
        object.__setattr__(settings, "download_path", self._original_download_path)
        object.__setattr__(settings, "admin_id", self._original_admin_id)
        self._temp_dir.cleanup()

    async def test_free_user_is_allowed_then_both_daily_limits_block(self) -> None:
        user_id = 2001
        await db.set_user_lang(user_id, "en")
        await db.set_user_mode(user_id, "converter")

        self.assertTrue((await check_plan_access(user_id, "download")).allowed)
        self.assertTrue((await check_plan_access(user_id, "conversion")).allowed)

        for _ in range(FREE_DAILY_LIMITS["download"]):
            await record_plan_usage(user_id, "download")
        for _ in range(FREE_DAILY_LIMITS["conversion"]):
            await record_plan_usage(user_id, "conversion")

        download_message, _ = _message(user_id)
        await downloader_handler._run_download(
            download_message,
            SimpleNamespace(),
            "https://example.com/media",
        )
        download_text = download_message.answer.await_args.args[0]
        self.assertIn("free limit of 10 downloads", download_text)
        self.assertIn("/upgrade", download_text)

        conversion_message, _ = _document(user_id)
        await convert_handler.handle_convertible_file(conversion_message)
        conversion_text = conversion_message.answer.await_args.args[0]
        self.assertIn("free limit of 3 conversions", conversion_text)
        self.assertIn("/upgrade", conversion_text)

        row = await db.get_user_plan(user_id)
        self.assertEqual(row["plan"], "free")
        self.assertIsNone(row["plan_expires_at"])
        usage = await db.get_user_plan_details(user_id)
        self.assertEqual(usage["plan_key"], "free_daily")
        self.assertEqual(usage["downloads_remaining"], 0)
        self.assertEqual(usage["conversions_remaining"], 0)
        my_plan_text = await _my_plan_text(user_id, "en")
        self.assertIn("Status: Free", my_plan_text)
        self.assertNotIn("Expiry date:", my_plan_text)

    async def test_expired_plan_reports_expiry_and_lazily_downgrades(self) -> None:
        for user_id, operation in ((2002, "download"), (2003, "conversion")):
            with self.subTest(operation=operation):
                await db.set_user_lang(user_id, "en")
                await db.set_user_mode(user_id, "converter")
                await db.set_user_plan(
                    user_id,
                    "all_in_one",
                    datetime.now(timezone.utc) - timedelta(minutes=1),
                )

                before = await db.get_user_plan(user_id)
                self.assertEqual(before["plan"], "all_in_one")

                if operation == "download":
                    message, _ = _message(user_id)
                    await downloader_handler._run_download(
                        message,
                        SimpleNamespace(),
                        "https://example.com/media",
                    )
                else:
                    message, _ = _document(user_id)
                    await convert_handler.handle_convertible_file(message)

                self.assertIn(
                    "paid plan has expired",
                    message.answer.await_args.args[0],
                )
                self.assertIn("/upgrade", message.answer.await_args.args[0])

                after = await db.get_user_plan(user_id)
                self.assertEqual(after["plan"], "free")
                self.assertIsNone(after["plan_expires_at"])
                self.assertEqual(after["payment_status"], "none")

    async def test_active_all_in_one_completes_download_and_conversion(self) -> None:
        user_id = 2004
        await db.set_user_lang(user_id, "en")
        await db.set_user_mode(user_id, "converter")
        await db.activate_user_plan(user_id, PLANS["all_in_one"])

        session_dir = settings.download_path / "download-session"
        session_dir.mkdir()
        media_path = session_dir / "sample.mp4"
        media_path.write_bytes(b"video")
        media_info = MediaInfo(
            title="Paid media",
            uploader="Sandy",
            duration="0:01",
            platform="Test",
            file_size_str="5 B",
            duration_seconds=1,
        )
        result = DownloadResult(
            success=True,
            file_path=media_path,
            info=media_info,
        )
        download_message, download_status = _message(user_id)
        download_bot = SimpleNamespace(
            send_audio=AsyncMock(),
            send_video=AsyncMock(),
        )

        with (
            patch.object(
                downloader_handler,
                "fetch_info",
                new=AsyncMock(return_value=media_info),
            ),
            patch.object(
                downloader_handler,
                "download_media",
                new=AsyncMock(return_value=result),
            ),
            patch.object(
                downloader_handler.rate_limiter,
                "check",
                new=AsyncMock(return_value=(True, "")),
            ),
            patch.object(
                downloader_handler.stats,
                "record_success",
                new=AsyncMock(),
            ) as record_success,
            patch.object(
                downloader_handler.stats,
                "record_failure",
                new=AsyncMock(),
            ) as record_failure,
            patch.object(
                downloader_handler,
                "get_video_dimensions",
                return_value=(640, 360),
            ),
        ):
            await downloader_handler._run_download(
                download_message,
                download_bot,
                "https://example.com/media",
            )

        download_bot.send_video.assert_awaited_once()
        download_status.delete.assert_awaited_once()
        record_success.assert_awaited_once_with(user_id)
        record_failure.assert_not_awaited()

        conversion_message, _ = _document(user_id)
        await convert_handler.handle_convertible_file(conversion_message)
        picker_text = conversion_message.answer.await_args.args[0]
        picker_markup = conversion_message.answer.await_args.kwargs["reply_markup"]
        self.assertIn("Choose a target format", picker_text)
        callback_data = picker_markup.inline_keyboard[0][0].callback_data

        conversion_status = SimpleNamespace(
            chat=SimpleNamespace(id=user_id),
            edit_text=AsyncMock(),
            delete=AsyncMock(),
        )
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=user_id),
            data=callback_data,
            message=conversion_status,
            answer=AsyncMock(),
        )

        async def fake_download(_file_id, destination) -> None:
            destination.write(b"source image")

        async def fake_convert(input_path, target_format, _mime_type):
            output_path = input_path.with_name(f"converted.{target_format}")
            output_path.write_bytes(b"converted image")
            return output_path

        conversion_bot = SimpleNamespace(
            download=AsyncMock(side_effect=fake_download),
            send_audio=AsyncMock(),
            send_document=AsyncMock(),
        )
        with (
            patch.object(
                convert_handler.rate_limiter,
                "check",
                new=AsyncMock(return_value=(True, "")),
            ),
            patch.object(
                convert_handler,
                "convert_file",
                new=AsyncMock(side_effect=fake_convert),
            ),
        ):
            await convert_handler.cb_convert(callback, conversion_bot)

        conversion_bot.send_document.assert_awaited_once()
        conversion_status.delete.assert_awaited_once()
        row = await db.get_user_plan(user_id)
        self.assertEqual(row["plan"], "all_in_one")
        self.assertGreater(row["plan_expires_at"], datetime.now(timezone.utc))

    async def test_plan_capabilities_and_shared_package_balance(self) -> None:
        downloader_user = 2005
        converter_user = 2006
        package_user = 2007
        await db.activate_user_plan(
            downloader_user,
            PLANS["downloader_pro"],
        )
        await db.activate_user_plan(
            converter_user,
            PLANS["converter_pro"],
        )
        await db.activate_user_plan(package_user, PLANS["starter_pack"])

        self.assertTrue(
            (await check_plan_access(downloader_user, "download")).allowed
        )
        downloader_conversion = await check_plan_access(
            downloader_user,
            "conversion",
        )
        self.assertFalse(downloader_conversion.allowed)
        self.assertEqual(
            downloader_conversion.message_key,
            "plan_conversion_not_included",
        )

        self.assertTrue(
            (await check_plan_access(converter_user, "conversion")).allowed
        )
        converter_download = await check_plan_access(
            converter_user,
            "download",
        )
        self.assertFalse(converter_download.allowed)
        self.assertEqual(
            converter_download.message_key,
            "plan_download_not_included",
        )

        await record_plan_usage(package_user, "download")
        details = await db.get_user_plan_details(package_user)
        self.assertEqual(details["downloads_remaining"], 14)
        self.assertEqual(details["conversions_remaining"], 14)

        for _ in range(14):
            await record_plan_usage(package_user, "conversion")
        exhausted = await check_plan_access(package_user, "download")
        self.assertFalse(exhausted.allowed)
        self.assertEqual(exhausted.message_key, "plan_usage_exhausted")

    async def test_conversion_callback_rechecks_a_newly_reached_limit(self) -> None:
        user_id = 2008
        await db.set_user_lang(user_id, "en")
        await db.set_user_mode(user_id, "converter")
        message, _ = _document(user_id)
        await convert_handler.handle_convertible_file(message)
        markup = message.answer.await_args.kwargs["reply_markup"]
        callback_data = markup.inline_keyboard[0][0].callback_data

        for _ in range(FREE_DAILY_LIMITS["conversion"]):
            await record_plan_usage(user_id, "conversion")

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=user_id),
            data=callback_data,
            message=SimpleNamespace(),
            answer=AsyncMock(),
        )
        bot = SimpleNamespace(download=AsyncMock())
        await convert_handler.cb_convert(callback, bot)

        alert_text = callback.answer.await_args.args[0]
        self.assertIn("free limit of 3 conversions", alert_text)
        callback.answer.assert_awaited_once_with(alert_text, show_alert=True)
        bot.download.assert_not_awaited()

    async def test_free_allowance_resets_on_a_new_utc_day(self) -> None:
        user_id = 2010
        today = datetime.now(timezone.utc).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        yesterday = today - timedelta(days=1)
        await db.get_or_create_free_daily_usage(
            user_id=user_id,
            starts_at=yesterday,
            expires_at=today,
            download_limit=FREE_DAILY_LIMITS["download"],
            conversion_limit=FREE_DAILY_LIMITS["conversion"],
            max_file_size_mb=FREE_MAX_FILE_SIZE_MB,
        )
        db._memory_user_plans[user_id]["downloads_remaining"] = 0
        db._memory_user_plans[user_id]["conversions_remaining"] = 0

        self.assertTrue((await check_plan_access(user_id, "download")).allowed)
        self.assertTrue((await check_plan_access(user_id, "conversion")).allowed)
        usage = await db.get_user_plan_details(user_id)
        self.assertEqual(usage["starts_at"], today)
        self.assertEqual(
            usage["downloads_remaining"],
            FREE_DAILY_LIMITS["download"],
        )
        self.assertEqual(
            usage["conversions_remaining"],
            FREE_DAILY_LIMITS["conversion"],
        )

    async def test_free_file_size_and_admin_bypass(self) -> None:
        free_user = 2009
        await db.set_user_lang(free_user, "en")
        await db.set_user_mode(free_user, "converter")
        oversized, _ = _document(
            free_user,
            file_size=(FREE_MAX_FILE_SIZE_MB + 1) * MEBIBYTE,
        )
        await convert_handler.handle_convertible_file(oversized)
        self.assertIn(
            "plan allows files up to 500 MB",
            oversized.answer.await_args.args[0],
        )

        admin_id = settings.admin_id
        object.__setattr__(settings, "admin_id", 0)
        try:
            for _ in range(FREE_DAILY_LIMITS["download"]):
                await record_plan_usage(admin_id, "download")
            for _ in range(FREE_DAILY_LIMITS["conversion"]):
                await record_plan_usage(admin_id, "conversion")
        finally:
            object.__setattr__(settings, "admin_id", admin_id)

        usage = await db.get_user_plan_details(admin_id)
        self.assertEqual(usage["downloads_remaining"], 0)
        self.assertEqual(usage["conversions_remaining"], 0)
        self.assertTrue((await check_plan_access(admin_id, "download")).allowed)
        self.assertTrue((await check_plan_access(admin_id, "conversion")).allowed)

    def test_plan_rejection_translations_are_complete(self) -> None:
        required_keys = {
            "plan_free_download_limit_reached",
            "plan_free_conversion_limit_reached",
            "plan_expired",
            "plan_upgrade_required",
            "plan_download_not_included",
            "plan_conversion_not_included",
            "plan_usage_exhausted",
            "plan_file_too_large",
        }
        for language in ("en", "ar", "ru"):
            with self.subTest(language=language):
                translations = json.loads(
                    (PROJECT_ROOT / f"{language}.json").read_text(encoding="utf-8")
                )
                self.assertFalse(required_keys - translations.keys())
                for key in required_keys:
                    self.assertIn("/upgrade", translations[key])


if __name__ == "__main__":
    unittest.main()
