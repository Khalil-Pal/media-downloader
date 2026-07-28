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
from handlers.payment_handler import _plans_overview
from services import db
from services.downloader import DownloadResult, MediaInfo
from services.payments import (
    PLANS,
    PURCHASABLE_PLANS,
    check_usage_allowed,
    increment_usage,
)
from services.plans import PLANS as COMPATIBILITY_PLANS
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

    def test_catalog_is_the_single_source_of_truth(self) -> None:
        self.assertIs(PLANS, COMPATIBILITY_PLANS)
        self.assertNotIn("free", PURCHASABLE_PLANS)
        self.assertEqual(PLANS["free"].duration_days, 2)
        self.assertEqual(PLANS["free"].daily_download_limit, 10)
        self.assertEqual(PLANS["free"].daily_conversion_limit, 3)
        self.assertEqual(PLANS["starter_pack"].package_uses, 15)
        self.assertEqual(PLANS["pro_pack"].package_uses, 60)
        self.assertEqual(PLANS["ultra_pack"].package_uses, 150)
        self.assertEqual(PLANS["downloader_pro"].max_video_height, 1080)
        self.assertTrue(PLANS["downloader_pro"].playlist_support)
        for plan in PLANS.values():
            self.assertFalse(hasattr(plan, "plan_priority"))
            self.assertFalse(hasattr(plan, "priority_level"))

    async def test_plan_displays_have_no_priority_artifacts(self) -> None:
        free_user = 2011
        paid_user = 2012
        await db.set_user_lang(free_user, "en")
        await db.activate_user_plan(paid_user, PLANS["annual"])

        forbidden_terms = {
            "en": ("priority",),
            "ar": ("أولوية", "الأولوية"),
            "ru": ("приоритет",),
        }
        annual_endings = {
            "en": "• All All-in-One features for one year\n\nUsage packages:",
            "ar": "• كل ميزات All-in-One لمدة سنة كاملة\n\nباقات الاستخدام:",
            "ru": "• Все функции All-in-One на один год\n\nПакеты использования:",
        }

        for language in ("en", "ar", "ru"):
            translations = json.loads(
                (PROJECT_ROOT / f"{language}.json").read_text(encoding="utf-8")
            )
            rendered = (
                _plans_overview(language),
                translations["menu_plans"],
                await _my_plan_text(free_user, language),
                await _my_plan_text(paid_user, language),
            )
            for text in rendered:
                lowered = text.casefold()
                for term in forbidden_terms[language]:
                    self.assertNotIn(term.casefold(), lowered)
                self.assertNotIn("{priority}", text)
                self.assertNotIn("\n• \n", text)
            self.assertIn(annual_endings[language], translations["menu_plans"])

        details = await db.get_user_plan_details(paid_user)
        self.assertNotIn("priority_level", details)

    async def test_free_daily_limit_and_lazy_reset(self) -> None:
        user_id = 2001
        await db.set_user_lang(user_id, "en")
        await db.set_user_mode(user_id, "converter")

        self.assertEqual(
            await check_usage_allowed(user_id, "download", 1),
            (True, None),
        )
        for _ in range(10):
            await increment_usage(user_id, "download")

        self.assertEqual(
            await check_usage_allowed(user_id, "download", 1),
            (False, "free_daily_limit_reached"),
        )
        row = await db.get_user_plan(user_id)
        self.assertEqual(row["downloads_today"], 10)
        self.assertEqual(row["conversions_today"], 0)
        self.assertIsNotNone(row["free_started_at"])
        self.assertIsNotNone(row["usage_reset_at"])

        message, _ = _message(user_id)
        with patch.object(
            downloader_handler,
            "fetch_info",
            new=AsyncMock(),
        ) as fetch_info:
            await downloader_handler._run_download(
                message,
                SimpleNamespace(),
                "https://example.com/media",
            )
        fetch_info.assert_not_awaited()
        self.assertEqual(
            message.answer.await_args.args[0],
            "You've reached today's Free Plan usage limit. "
            "Use /upgrade to continue.",
        )

        db._memory_users[user_id]["conversions_today"] = 2
        db._memory_users[user_id]["usage_reset_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        self.assertEqual(
            await check_usage_allowed(user_id, "download", 1),
            (True, None),
        )
        reset = await db.get_user_plan(user_id)
        self.assertEqual(reset["downloads_today"], 0)
        self.assertEqual(reset["conversions_today"], 0)
        self.assertGreater(reset["usage_reset_at"], datetime.now(timezone.utc))

    async def test_free_two_day_window_expires_with_distinct_reason(self) -> None:
        user_id = 2002
        await db.set_user_lang(user_id, "en")
        await db.set_user_mode(user_id, "converter")
        self.assertTrue((await check_usage_allowed(user_id, "conversion", 1))[0])
        db._memory_users[user_id]["free_started_at"] = (
            datetime.now(timezone.utc) - timedelta(days=2, seconds=1)
        )

        self.assertEqual(
            await check_usage_allowed(user_id, "conversion", 1),
            (False, "free_expired"),
        )
        message, _ = _document(user_id)
        await convert_handler.handle_convertible_file(message)
        self.assertEqual(
            message.answer.await_args.args[0],
            "Your 2-day Free Starter period has ended. "
            "Use /upgrade to continue.",
        )

    async def test_downloader_pro_allows_downloads_but_never_conversions(
        self,
    ) -> None:
        user_id = 2003
        await db.set_user_lang(user_id, "en")
        await db.set_user_mode(user_id, "converter")
        await db.activate_user_plan(user_id, PLANS["downloader_pro"])

        for _ in range(25):
            self.assertEqual(
                await check_usage_allowed(user_id, "download", 1),
                (True, None),
            )
            await increment_usage(user_id, "download")
        self.assertEqual(
            await check_usage_allowed(user_id, "conversion", 1),
            (False, "plan_feature_not_included"),
        )

        message, _ = _document(user_id)
        await convert_handler.handle_convertible_file(message)
        self.assertIn(
            "current plan does not include this feature",
            message.answer.await_args.args[0],
        )

    async def test_converter_pro_allows_conversions_but_never_downloads(
        self,
    ) -> None:
        user_id = 2004
        await db.set_user_lang(user_id, "en")
        await db.set_user_mode(user_id, "converter")
        await db.activate_user_plan(user_id, PLANS["converter_pro"])

        for _ in range(25):
            self.assertEqual(
                await check_usage_allowed(user_id, "conversion", 1),
                (True, None),
            )
            await increment_usage(user_id, "conversion")
        self.assertEqual(
            await check_usage_allowed(user_id, "download", 1),
            (False, "plan_feature_not_included"),
        )

        message, _ = _message(user_id)
        with patch.object(
            downloader_handler,
            "fetch_info",
            new=AsyncMock(),
        ) as fetch_info:
            await downloader_handler._run_download(
                message,
                SimpleNamespace(),
                "https://example.com/media",
            )
        fetch_info.assert_not_awaited()
        self.assertIn(
            "current plan does not include this feature",
            message.answer.await_args.args[0],
        )

    async def test_all_in_one_completes_download_and_conversion(self) -> None:
        user_id = 2005
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
            file_size_bytes=5,
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
            ),
            patch.object(
                downloader_handler.stats,
                "record_failure",
                new=AsyncMock(),
            ),
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

        conversion_message, _ = _document(user_id)
        await convert_handler.handle_convertible_file(conversion_message)
        picker_markup = conversion_message.answer.await_args.kwargs["reply_markup"]
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
        self.assertEqual(
            await check_usage_allowed(user_id, "download", 1),
            (True, None),
        )
        self.assertEqual(
            await check_usage_allowed(user_id, "conversion", 1),
            (True, None),
        )

    async def test_package_uses_one_shared_depleting_counter(self) -> None:
        user_id = 2006
        await db.set_user_lang(user_id, "en")
        await db.activate_user_plan(user_id, PLANS["starter_pack"])

        before = await db.get_user_plan(user_id)
        self.assertEqual(before["plan_type"], "package")
        self.assertEqual(before["package_uses_remaining"], 15)
        self.assertIsNone(before["plan_expires_at"])
        self.assertGreater(
            before["package_expires_at"],
            datetime.now(timezone.utc),
        )

        await increment_usage(user_id, "download")
        after_download = await db.get_user_plan(user_id)
        self.assertEqual(after_download["package_uses_remaining"], 14)

        await increment_usage(user_id, "conversion")
        after_conversion = await db.get_user_plan(user_id)
        self.assertEqual(after_conversion["package_uses_remaining"], 13)

        for index in range(13):
            action = "download" if index % 2 == 0 else "conversion"
            await increment_usage(user_id, action)
        depleted = await db.get_user_plan(user_id)
        self.assertEqual(depleted["package_uses_remaining"], 0)
        self.assertEqual(
            await check_usage_allowed(user_id, "download", 1),
            (False, "package_depleted"),
        )
        details = await db.get_user_plan_details(user_id)
        self.assertIsNone(details["downloads_remaining"])
        self.assertIsNone(details["conversions_remaining"])
        self.assertIn("0 downloads or conversions", await _my_plan_text(user_id, "en"))

    async def test_package_expiry_is_distinct_from_depletion(self) -> None:
        user_id = 2007
        await db.activate_user_plan(user_id, PLANS["starter_pack"])
        db._memory_users[user_id]["package_uses_remaining"] = 10
        db._memory_users[user_id]["package_expires_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        )

        before = await db.get_user_plan(user_id)
        self.assertEqual(before["package_uses_remaining"], 10)
        self.assertEqual(
            await check_usage_allowed(user_id, "conversion", 1),
            (False, "package_expired"),
        )
        after = await db.get_user_plan(user_id)
        self.assertEqual(after["plan"], "free")
        self.assertEqual(after["plan_type"], "free")
        self.assertIsNone(after["package_uses_remaining"])
        self.assertIsNone(after["package_expires_at"])

    async def test_file_size_caps_block_before_expensive_work(self) -> None:
        free_user = 2008
        await db.set_user_lang(free_user, "en")
        await db.set_user_mode(free_user, "converter")
        self.assertEqual(
            await check_usage_allowed(
                free_user,
                "conversion",
                501 * MEBIBYTE,
            ),
            (False, "file_too_large_for_plan"),
        )
        oversized_document, _ = _document(
            free_user,
            file_size=501 * MEBIBYTE,
        )
        await convert_handler.handle_convertible_file(oversized_document)
        self.assertIn(
            "exceeds your plan's size limit",
            oversized_document.answer.await_args.args[0],
        )

        paid_user = 2009
        await db.set_user_lang(paid_user, "en")
        await db.activate_user_plan(paid_user, PLANS["all_in_one"])
        self.assertEqual(
            await check_usage_allowed(
                paid_user,
                "download",
                2001 * MEBIBYTE,
            ),
            (False, "file_too_large_for_plan"),
        )

        estimated = MediaInfo(
            title="Oversized",
            uploader="Test",
            duration="1:00",
            platform="Test",
            file_size_str="2001 MB",
            file_size_bytes=2001 * MEBIBYTE,
        )
        message, status = _message(paid_user)
        with (
            patch.object(
                downloader_handler,
                "fetch_info",
                new=AsyncMock(return_value=estimated),
            ),
            patch.object(
                downloader_handler,
                "download_media",
                new=AsyncMock(),
            ) as download_media,
            patch.object(
                downloader_handler.rate_limiter,
                "check",
                new=AsyncMock(return_value=(True, "")),
            ),
        ):
            await downloader_handler._run_download(
                message,
                SimpleNamespace(),
                "https://example.com/large",
            )
        download_media.assert_not_awaited()
        self.assertIn(
            "exceeds your plan's size limit",
            status.edit_text.await_args.args[0],
        )

    async def test_stale_conversion_callback_rechecks_usage(self) -> None:
        user_id = 2010
        await db.set_user_lang(user_id, "en")
        await db.set_user_mode(user_id, "converter")
        message, _ = _document(user_id)
        await convert_handler.handle_convertible_file(message)
        markup = message.answer.await_args.kwargs["reply_markup"]
        callback_data = markup.inline_keyboard[0][0].callback_data

        for _ in range(3):
            await increment_usage(user_id, "conversion")

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=user_id),
            data=callback_data,
            message=SimpleNamespace(),
            answer=AsyncMock(),
        )
        bot = SimpleNamespace(download=AsyncMock())
        await convert_handler.cb_convert(callback, bot)

        self.assertIn(
            "Free Plan usage limit",
            callback.answer.await_args.args[0],
        )
        bot.download.assert_not_awaited()

    async def test_admin_bypasses_expiry_limits_features_and_size(self) -> None:
        admin_id = settings.admin_id
        await db.set_user_plan(
            admin_id,
            "converter_pro",
            datetime.now(timezone.utc) - timedelta(days=1),
        )
        self.assertEqual(
            await check_usage_allowed(
                admin_id,
                "download",
                10_000 * MEBIBYTE,
            ),
            (True, None),
        )
        self.assertEqual(
            await check_usage_allowed(
                admin_id,
                "conversion",
                10_000 * MEBIBYTE,
            ),
            (True, None),
        )

    def test_schema_policy_calls_and_translations_are_complete(self) -> None:
        db_source = (PROJECT_ROOT / "services" / "db.py").read_text(
            encoding="utf-8"
        )
        for column in (
            "plan_type",
            "downloads_today",
            "conversions_today",
            "usage_reset_at",
            "free_started_at",
            "package_uses_remaining",
            "package_expires_at",
        ):
            self.assertIn(f"ADD COLUMN IF NOT EXISTS {column}", db_source)
        self.assertIn(
            "ALTER TABLE user_plans DROP COLUMN IF EXISTS priority_level",
            db_source,
        )

        payments_source = (PROJECT_ROOT / "services" / "payments.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("downloads_remaining", payments_source)
        self.assertNotIn("conversions_remaining", payments_source)
        self.assertIn("GREATEST(", db_source)
        self.assertNotIn("LEAST(", db_source)

        for handler_name in ("downloader_handler.py", "convert_handler.py"):
            handler_source = (
                PROJECT_ROOT / "handlers" / handler_name
            ).read_text(encoding="utf-8")
            self.assertIn("check_usage_allowed", handler_source)
            self.assertIn("increment_usage", handler_source)
            self.assertNotIn("check_plan_access", handler_source)

        required_keys = {
            "free_daily_limit_reached",
            "free_expired",
            "plan_feature_not_included",
            "file_too_large_for_plan",
            "package_depleted",
            "package_expired",
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
