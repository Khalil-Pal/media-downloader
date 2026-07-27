from __future__ import annotations

import json
import os
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123456:test-token")

from config.settings import settings
from handlers.payment_handler import (
    PendingUpgradeProofFilter,
    cancel_pending_keyboard,
    cb_cancel_pending_upgrade,
    cb_upgrade_currency,
    cmd_approve_payment,
    cmd_reject_payment,
    cmd_upgrade,
    msg_upgrade_payment_proof,
    upgrade_currency_keyboard,
    upgrade_plans_keyboard,
)
from middlewares import UserProfileMiddleware
from services import db
from services.payments import (
    PLANS,
    generate_reference_code,
    plan_price,
    user_has_active_plan,
)
from services.plans import PLANS as PLAN_CATALOG

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _user(user_id: int, username: str = "test_user") -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        username=username,
        first_name="Test",
        last_name="User",
    )


def _message(user_id: int, text: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        from_user=_user(user_id),
        text=text,
        caption=None,
        answer=AsyncMock(),
        chat=SimpleNamespace(id=user_id),
        message_id=100,
        photo=None,
        document=None,
    )


def _callback(user_id: int, data: str) -> SimpleNamespace:
    return SimpleNamespace(
        from_user=_user(user_id),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(answer=AsyncMock()),
    )


class UpgradeFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        db._pool = None
        db._memory_users.clear()
        db._memory_user_plans.clear()
        db._memory_payments.clear()
        db._memory_cancelled_payment_refs.clear()
        db._memory_next_payment_id = 1

        self._settings = {
            "admin_id": settings.admin_id,
            "payment_info_rub": settings.payment_info_rub,
            "payment_info_usd": settings.payment_info_usd,
            "payment_info_ils": settings.payment_info_ils,
            "payment_review_eta": settings.payment_review_eta,
        }
        object.__setattr__(settings, "admin_id", 999)
        object.__setattr__(settings, "payment_info_rub", "RUB receiving details")
        object.__setattr__(settings, "payment_info_usd", "USD receiving details")
        object.__setattr__(settings, "payment_info_ils", "ILS receiving details")
        object.__setattr__(settings, "payment_review_eta", "within 24 hours")

    def tearDown(self) -> None:
        for key, value in self._settings.items():
            object.__setattr__(settings, key, value)

    async def test_upgrade_selection_creates_pending_reference(self) -> None:
        user_id = 101
        await db.set_user_lang(user_id, "en")
        message = _message(user_id, "/upgrade")

        await cmd_upgrade(message)

        text, kwargs = message.answer.await_args.args[0], message.answer.await_args.kwargs
        self.assertIn("Choose a plan", text)
        self.assertEqual(len(kwargs["reply_markup"].inline_keyboard), len(PLANS))

        callback = _callback(user_id, "upgrade:currency:all_in_one:USD")
        await cb_upgrade_currency(callback)

        pending = await db.get_pending_upgrade_payment_for_user(user_id)
        self.assertIsNotNone(pending)
        self.assertEqual(pending["payment_plan"], "all_in_one")
        self.assertEqual(pending["payment_currency"], "USD")
        self.assertRegex(pending["payment_ref"], rf"SSB-{user_id}-[A-Z0-9]{{4}}")

        payment_text = callback.message.answer.await_args.args[0]
        self.assertIn(plan_price("all_in_one", "USD"), payment_text)
        self.assertIn("USD receiving details", payment_text)
        self.assertNotIn("ILS receiving details", payment_text)
        self.assertIn(pending["payment_ref"], payment_text)
        self.assertIn("NEVER", payment_text)

    async def test_cancel_and_choose_again_end_to_end(self) -> None:
        user_id = 111
        old_ref = f"SSB-{user_id}-OLD1"
        new_ref = f"SSB-{user_id}-NEW2"
        await db.register_user(user_id, "switcher", "Plan", "Switcher")
        await db.set_user_lang(user_id, "en")
        await db.set_payment_pending(
            user_id,
            "downloader_pro",
            "RUB",
            old_ref,
        )

        upgrade_message = _message(user_id, "/upgrade")
        await cmd_upgrade(upgrade_message)

        pending_text = upgrade_message.answer.await_args.args[0]
        pending_markup = upgrade_message.answer.await_args.kwargs["reply_markup"]
        self.assertIn("pending upgrade request", pending_text)
        self.assertEqual(
            pending_markup.inline_keyboard[0][0].callback_data,
            f"upgrade:cancel_pending:{old_ref}",
        )
        self.assertEqual(
            pending_markup.inline_keyboard[0][0].text,
            "🔄 Cancel & choose again",
        )

        cancel_callback = _callback(
            user_id,
            f"upgrade:cancel_pending:{old_ref}",
        )
        await cb_cancel_pending_upgrade(cancel_callback)

        cancelled_user = await db.get_user_plan(user_id)
        self.assertEqual(cancelled_user["payment_status"], "none")
        self.assertIsNone(cancelled_user["payment_ref"])
        self.assertIsNone(cancelled_user["payment_plan"])
        self.assertIsNone(cancelled_user["payment_currency"])
        self.assertIsNone(await db.get_pending_payment_by_ref(old_ref))
        cancelled_ref = await db.get_cancelled_payment_by_ref(old_ref)
        self.assertEqual(cancelled_ref["payment_plan"], "downloader_pro")
        self.assertEqual(cancelled_ref["payment_currency"], "RUB")

        reopened_text = cancel_callback.message.answer.await_args.args[0]
        reopened_markup = cancel_callback.message.answer.await_args.kwargs["reply_markup"]
        self.assertIn("Choose a plan", reopened_text)
        self.assertEqual(len(reopened_markup.inline_keyboard), len(PLANS))

        choose_new = _callback(
            user_id,
            "upgrade:currency:converter_pro:USD",
        )
        with patch(
            "handlers.payment_handler.generate_reference_code",
            side_effect=[old_ref, new_ref],
        ):
            await cb_upgrade_currency(choose_new)

        new_pending = await db.get_pending_upgrade_payment_for_user(user_id)
        self.assertEqual(new_pending["payment_ref"], new_ref)
        self.assertNotEqual(new_pending["payment_ref"], old_ref)
        self.assertEqual(new_pending["payment_plan"], "converter_pro")
        self.assertEqual(new_pending["payment_currency"], "USD")

        stale_cancel = _callback(
            user_id,
            f"upgrade:cancel_pending:{old_ref}",
        )
        await cb_cancel_pending_upgrade(stale_cancel)
        self.assertEqual(
            (await db.get_pending_upgrade_payment_for_user(user_id))["payment_ref"],
            new_ref,
        )

        stale_proof = _message(user_id)
        stale_proof.caption = f"Payment reference: {old_ref}"
        stale_proof.photo = [SimpleNamespace(file_id="old-proof")]
        self.assertTrue(await PendingUpgradeProofFilter()(stale_proof))
        stale_bot = SimpleNamespace(
            forward_message=AsyncMock(),
            send_message=AsyncMock(),
        )

        await msg_upgrade_payment_proof(stale_proof, stale_bot)

        stale_bot.forward_message.assert_awaited_once()
        stale_admin_text = stale_bot.send_message.await_args.args[1]
        self.assertIn("CANCELLED upgrade reference", stale_admin_text)
        self.assertIn(old_ref, stale_admin_text)
        self.assertIn("was cancelled", stale_proof.answer.await_args.args[0])

        old_approve = _message(999, f"/approve {old_ref}")
        approval_bot = SimpleNamespace(send_message=AsyncMock())
        await cmd_approve_payment(old_approve, approval_bot)

        self.assertIn(
            "cancelled by the user and is no longer valid",
            old_approve.answer.await_args.args[0],
        )
        approval_bot.send_message.assert_not_awaited()
        self.assertEqual(
            (await db.get_pending_upgrade_payment_for_user(user_id))["payment_ref"],
            new_ref,
        )

        new_proof = _message(user_id)
        new_proof.photo = [SimpleNamespace(file_id="new-proof")]
        new_proof_bot = SimpleNamespace(
            forward_message=AsyncMock(),
            send_message=AsyncMock(),
        )
        await msg_upgrade_payment_proof(new_proof, new_proof_bot)

        new_admin_text = new_proof_bot.send_message.await_args.args[1]
        self.assertIn(new_ref, new_admin_text)
        self.assertIn(f"/approve {new_ref}", new_admin_text)
        self.assertIn("Payment proof received", new_proof.answer.await_args.args[0])

        new_approve = _message(999, f"/approve {new_ref}")
        await cmd_approve_payment(new_approve, approval_bot)

        approved = await db.get_user_plan(user_id)
        self.assertEqual(approved["payment_status"], "confirmed")
        self.assertEqual(approved["plan"], "converter_pro")
        self.assertIsNone(await db.get_pending_payment_by_ref(new_ref))
        self.assertIsNotNone(await db.get_cancelled_payment_by_ref(old_ref))
        approval_bot.send_message.assert_awaited_once()
        self.assertIn("Approved upgrade", new_approve.answer.await_args.args[0])

    async def test_cancel_only_changes_pending_rows(self) -> None:
        user_id = 112
        ref_code = f"SSB-{user_id}-KEEP"
        expires_at = datetime.now(timezone.utc) + timedelta(days=30)
        await db.set_payment_pending(
            user_id,
            "downloader_pro",
            "USD",
            ref_code,
        )
        await db.set_payment_confirmed(
            ref_code,
            "downloader_pro",
            expires_at,
        )

        self.assertIsNone(await db.cancel_pending_payment(user_id))
        confirmed = await db.get_user_plan(user_id)
        self.assertEqual(confirmed["payment_status"], "confirmed")
        self.assertEqual(confirmed["payment_ref"], ref_code)

        self.assertIsNone(await db.cancel_pending_payment(999999))

    async def test_recent_cancelled_proof_is_forwarded_without_caption(self) -> None:
        user_id = 113
        ref_code = f"SSB-{user_id}-RACE"
        await db.register_user(user_id, "race_user", "Race", "User")
        await db.set_user_lang(user_id, "en")
        await db.set_payment_pending(
            user_id,
            "starter_pack",
            "ILS",
            ref_code,
        )
        await db.cancel_pending_payment(user_id)

        proof = _message(user_id)
        proof.photo = [SimpleNamespace(file_id="race-proof")]
        proof_filter = PendingUpgradeProofFilter()
        self.assertTrue(await proof_filter(proof))

        bot = SimpleNamespace(
            forward_message=AsyncMock(),
            send_message=AsyncMock(),
        )
        await msg_upgrade_payment_proof(proof, bot)

        bot.forward_message.assert_awaited_once()
        self.assertIn("CANCELLED", bot.send_message.await_args.args[1])
        self.assertIn(ref_code, bot.send_message.await_args.args[1])

        db._memory_cancelled_payment_refs[ref_code]["cancelled_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=16)
        )
        expired_proof = _message(user_id)
        expired_proof.photo = [SimpleNamespace(file_id="late-proof")]
        self.assertFalse(await proof_filter(expired_proof))

    async def test_ils_uses_its_own_payment_details(self) -> None:
        callback = _callback(110, "upgrade:currency:converter_pro:ILS")

        await cb_upgrade_currency(callback)

        payment_text = callback.message.answer.await_args.args[0]
        self.assertIn("ILS receiving details", payment_text)
        self.assertNotIn("USD receiving details", payment_text)
        pending = await db.get_pending_upgrade_payment_for_user(110)
        self.assertEqual(pending["payment_currency"], "ILS")

    async def test_missing_admin_does_not_create_pending_payment(self) -> None:
        object.__setattr__(settings, "admin_id", 0)
        callback = _callback(102, "upgrade:currency:downloader_pro:RUB")

        await cb_upgrade_currency(callback)

        self.assertIsNone(await db.get_pending_upgrade_payment_for_user(102))
        self.assertIn("temporarily unavailable", callback.message.answer.await_args.args[0])

    async def test_legacy_pending_payment_blocks_duplicate_upgrade(self) -> None:
        user_id = 109
        await db.set_user_lang(user_id, "en")
        await db.create_payment_request(
            user_id=user_id,
            username="@legacy_user",
            plan=PLAN_CATALOG["downloader_pro"],
            currency="USD",
            amount="$1.99",
        )
        message = _message(user_id, "/upgrade")

        await cmd_upgrade(message)

        text = message.answer.await_args.args[0]
        self.assertIn("already have a pending payment request", text)
        self.assertNotIn("reply_markup", message.answer.await_args.kwargs)

    async def test_proof_is_forwarded_with_admin_commands(self) -> None:
        user_id = 103
        await db.register_user(user_id, "payer", "Paying", "User")
        await db.set_user_lang(user_id, "en")
        await db.set_payment_pending(
            user_id,
            "converter_pro",
            "ILS",
            f"SSB-{user_id}-AB12",
        )
        message = _message(user_id)
        message.photo = [SimpleNamespace(file_id="photo-file")]
        bot = SimpleNamespace(forward_message=AsyncMock(), send_message=AsyncMock())

        await msg_upgrade_payment_proof(message, bot)

        bot.forward_message.assert_awaited_once_with(
            chat_id=999,
            from_chat_id=user_id,
            message_id=100,
        )
        caption = bot.send_message.await_args.args[1]
        self.assertIn(f"SSB-{user_id}-AB12", caption)
        self.assertIn(f"/approve SSB-{user_id}-AB12", caption)
        self.assertIn(f"/reject SSB-{user_id}-AB12", caption)
        self.assertIn("Payment proof received", message.answer.await_args.args[0])

    async def test_approve_activates_plan_and_notifies_user(self) -> None:
        user_id = 104
        ref_code = f"SSB-{user_id}-CD34"
        await db.register_user(user_id, "approved_user", "Approved", "User")
        await db.set_user_lang(user_id, "en")
        await db.set_payment_pending(user_id, "annual", "USD", ref_code)
        admin_message = _message(999, f"/approve {ref_code}")
        bot = SimpleNamespace(send_message=AsyncMock())

        await cmd_approve_payment(admin_message, bot)

        row = await db.get_user_plan(user_id)
        self.assertEqual(row["payment_status"], "confirmed")
        self.assertEqual(row["plan"], "annual")
        self.assertGreater(row["plan_expires_at"], datetime.now(timezone.utc))
        self.assertIsNone(await db.get_pending_payment_by_ref(ref_code))

        details = await db.get_user_plan_details(user_id)
        self.assertEqual(details["plan_key"], "annual")
        self.assertTrue(details["is_active"])
        self.assertTrue(await user_has_active_plan(user_id))

        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.await_args.args[0], user_id)
        self.assertIn("approved", bot.send_message.await_args.args[1])
        self.assertIn("Approved upgrade", admin_message.answer.await_args.args[0])

    async def test_reject_resets_pending_payment_and_notifies_user(self) -> None:
        user_id = 105
        ref_code = f"SSB-{user_id}-EF56"
        await db.register_user(user_id, "rejected_user", "Rejected", "User")
        await db.set_user_lang(user_id, "en")
        await db.set_payment_pending(user_id, "starter_pack", "RUB", ref_code)
        admin_message = _message(999, f"/reject {ref_code} amount mismatch")
        bot = SimpleNamespace(send_message=AsyncMock())

        await cmd_reject_payment(admin_message, bot)

        row = await db.get_user_plan(user_id)
        self.assertEqual(row["payment_status"], "none")
        self.assertIsNone(row["payment_ref"])
        self.assertIsNone(row["payment_plan"])
        self.assertIsNone(row["payment_currency"])
        self.assertIn("amount mismatch", bot.send_message.await_args.args[1])
        self.assertIn("Rejected payment", admin_message.answer.await_args.args[0])

    async def test_expired_plan_is_lazily_downgraded(self) -> None:
        user_id = 106
        await db.set_user_plan(
            user_id,
            "downloader_pro",
            datetime.now(timezone.utc) - timedelta(seconds=1),
        )

        self.assertFalse(await user_has_active_plan(user_id))
        row = await db.get_user_plan(user_id)
        self.assertEqual(row["plan"], "free")
        self.assertIsNone(row["plan_expires_at"])
        self.assertEqual(row["payment_status"], "none")

    async def test_profile_is_updated_on_registration(self) -> None:
        user_id = 107
        await db.register_user(user_id, "old_name", "Old", None)
        await db.register_user(user_id, "new_name", "New", "Name")

        row = await db.get_user_plan(user_id)
        self.assertEqual(row["username"], "new_name")
        self.assertEqual(row["first_name"], "New")
        self.assertEqual(row["last_name"], "Name")

    async def test_profile_middleware_updates_message_and_callback_users(self) -> None:
        middleware = UserProfileMiddleware()
        handler = AsyncMock(return_value="handled")
        message = _message(108)

        result = await middleware(handler, message, {})

        self.assertEqual(result, "handled")
        handler.assert_awaited_once_with(message, {})
        row = await db.get_user_plan(108)
        self.assertEqual(row["username"], "test_user")
        self.assertEqual(row["first_name"], "Test")


class UpgradeStaticTests(unittest.TestCase):
    def test_reference_code_format(self) -> None:
        for _ in range(100):
            self.assertRegex(
                generate_reference_code(5551234567),
                r"^SSB-5551234567-[A-Z0-9]{4}$",
            )

    def test_upgrade_catalog_reuses_main_plan_catalog(self) -> None:
        self.assertEqual(set(PLANS), set(PLAN_CATALOG))
        for key in PLANS:
            self.assertEqual(PLANS[key], PLAN_CATALOG[key])

    def test_callback_data_fits_telegram_limit(self) -> None:
        for lang in ("en", "ar", "ru"):
            markups = [
                upgrade_plans_keyboard(lang),
                cancel_pending_keyboard(
                    lang,
                    "SSB-9223372036854775807-ABCD",
                ),
            ]
            markups.extend(upgrade_currency_keyboard(lang, key) for key in PLANS)
            for markup in markups:
                for row in markup.inline_keyboard:
                    for button in row:
                        self.assertLessEqual(len(button.callback_data.encode("utf-8")), 64)

    def test_cancel_button_is_translated(self) -> None:
        expected = {
            "en": "🔄 Cancel & choose again",
            "ar": "🔄 إلغاء واختيار خطة أخرى",
            "ru": "🔄 Отменить и выбрать заново",
        }
        for lang, text in expected.items():
            button = cancel_pending_keyboard(
                lang,
                "SSB-1-ABCD",
            ).inline_keyboard[0][0]
            self.assertEqual(button.text, text)

    def test_upgrade_translations_are_complete_and_formattable(self) -> None:
        locales = {
            lang: json.loads((PROJECT_ROOT / f"{lang}.json").read_text(encoding="utf-8"))
            for lang in ("en", "ar", "ru")
        }
        keys = {key for key in locales["en"] if key.startswith("upgrade_")}
        self.assertTrue(keys)
        for lang, translations in locales.items():
            self.assertFalse(keys - translations.keys(), f"Missing {lang} keys")
            for key in keys:
                fields = re.findall(r"{([a-z_]+)}", translations[key])
                translations[key].format(**{field: "test" for field in fields})

    def test_env_example_contains_only_placeholders(self) -> None:
        example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("PAYMENT_INFO_RUB", example)
        self.assertIn("PAYMENT_INFO_USD", example)
        self.assertIn("PAYMENT_INFO_ILS", example)
        self.assertNotIn("PAYMENT_INFO_INTL", example)
        self.assertIn("Example", example)
        self.assertNotIn("PAYMENT_INFO_RUB=\n", example)


if __name__ == "__main__":
    unittest.main()
