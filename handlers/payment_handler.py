"""Manual /upgrade payment flow with admin approval."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config.settings import settings
from services import db
from services.payments import (
    PAYMENT_PROOF_WINDOW_MINUTES,
    PURCHASABLE_PLANS,
    SUPPORTED_CURRENCIES,
    generate_reference_code,
    get_plan,
    payment_proof_request_is_active,
    plan_price,
)
from services.user_store import get_user_lang_or_default
from utils.i18n import t

logger = logging.getLogger(__name__)
router = Router(name="payments")

_REFERENCE_PATTERN = re.compile(r"\bSSB-\d+-[A-Z0-9]{4}\b", re.IGNORECASE)


def _reference_from_message(message: Message) -> str | None:
    text = (
        getattr(message, "caption", None)
        or getattr(message, "text", None)
        or ""
    )
    match = _REFERENCE_PATTERN.search(text)
    return match.group(0).upper() if match else None


async def _proof_payment_context(message: Message) -> tuple[dict, bool] | None:
    if message.from_user is None:
        return None

    user_id = message.from_user.id
    explicit_ref = _reference_from_message(message)
    if explicit_ref:
        pending = await db.get_pending_payment_by_ref(explicit_ref)
        if pending and int(pending["user_id"]) == user_id:
            return pending, False

        cancelled = await db.get_cancelled_payment_by_ref(explicit_ref)
        if cancelled and int(cancelled["user_id"]) == user_id:
            return cancelled, True

    pending = await db.get_pending_upgrade_payment_for_user(user_id)
    if pending and payment_proof_request_is_active(
        pending.get("payment_proof_requested_at")
    ):
        return pending, False

    if pending and pending.get("payment_proof_requested_at"):
        await db.clear_upgrade_payment_proof_request(
            user_id,
            str(pending.get("payment_ref") or ""),
        )
    return None


class PendingUpgradeProofFilter(BaseFilter):
    async def __call__(
        self,
        message: Message,
    ) -> bool | dict[str, tuple[dict, bool]]:
        if message.from_user is None or not (message.photo or message.document):
            return False
        context = await _proof_payment_context(message)
        if context is None:
            return False
        return {"upgrade_payment_context": context}


def _is_admin(user_id: int | None) -> bool:
    return bool(settings.admin_id) and user_id == settings.admin_id


def _format_date(value) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d")
    return str(value)


def upgrade_plans_keyboard(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in PURCHASABLE_PLANS.values():
        builder.button(
            text=t(lang, "upgrade_btn_plan", plan_name=plan.name),
            callback_data=f"upgrade:plan:{plan.key}",
        )
    builder.adjust(1)
    return builder.as_markup()


def upgrade_currency_keyboard(lang: str, plan_key: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    plan = PURCHASABLE_PLANS.get(plan_key)
    if plan is None:
        return builder.as_markup()

    for currency in SUPPORTED_CURRENCIES:
        if currency in plan.prices:
            builder.button(
                text=t(lang, f"upgrade_currency_{currency.lower()}"),
                callback_data=f"upgrade:currency:{plan_key}:{currency}",
            )
    builder.button(text=t(lang, "btn_back_plans"), callback_data="upgrade:plans")
    builder.adjust(1)
    return builder.as_markup()


def cancel_pending_keyboard(lang: str, ref_code: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "upgrade_btn_cancel_pending"),
        callback_data=f"upgrade:cancel_pending:{ref_code}",
    )
    return builder.as_markup()


def pending_upgrade_keyboard(lang: str, ref_code: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "upgrade_btn_send_proof"),
        callback_data=f"upgrade:send_proof:{ref_code}",
    )
    builder.button(
        text=t(lang, "upgrade_btn_cancel_pending"),
        callback_data=f"upgrade:cancel_pending:{ref_code}",
    )
    builder.adjust(1)
    return builder.as_markup()


def legacy_payment_proof_keyboard(
    lang: str,
    payment_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "payment_btn_send_receipt"),
        callback_data=f"pay:send_proof:{payment_id}",
    )
    return builder.as_markup()


def _plans_overview(lang: str) -> str:
    lines = [t(lang, "upgrade_choose_plan")]
    for plan in PURCHASABLE_PLANS.values():
        prices = " / ".join(plan.prices[currency] for currency in SUPPORTED_CURRENCIES)
        lines.append(
            t(
                lang,
                "upgrade_plan_line",
                plan_name=plan.name,
                prices=prices,
                duration_days=plan.duration_days,
            )
        )
    return "\n".join(lines)


async def _new_reference_code(user_id: int) -> str:
    for _ in range(32):
        ref_code = generate_reference_code(user_id)
        if (
            not await db.get_pending_payment_by_ref(ref_code)
            and not await db.get_cancelled_payment_by_ref(ref_code)
        ):
            return ref_code
    raise RuntimeError("Could not generate a unique payment reference.")


def _pending_text(lang: str, row: dict) -> str:
    plan = get_plan(str(row.get("payment_plan") or ""))
    plan_name = plan.name if plan else str(row.get("payment_plan") or "-")
    return t(
        lang,
        "upgrade_existing_pending",
        plan_name=plan_name,
        currency=row.get("payment_currency") or "-",
        ref_code=row.get("payment_ref") or "-",
    )


def _legacy_pending_text(lang: str, row: dict) -> str:
    receipt_status = (
        t(lang, "payment_receipt_received")
        if row.get("receipt_file_id")
        else t(lang, "payment_awaiting_receipt")
    )
    return t(
        lang,
        "payment_pending",
        payment_id=row["id"],
        plan_name=row["plan_name"],
        currency=row["currency"],
        amount=row["amount"],
        receipt_status=receipt_status,
    )


async def _existing_pending_prompt(
    user_id: int,
    lang: str,
) -> tuple[str, InlineKeyboardMarkup | None] | None:
    pending = await db.get_pending_upgrade_payment_for_user(user_id)
    if pending:
        return (
            _pending_text(lang, pending),
            pending_upgrade_keyboard(lang, str(pending.get("payment_ref") or "")),
        )

    legacy_pending = await db.get_pending_payment_for_user(user_id)
    if legacy_pending:
        return (
            _legacy_pending_text(lang, legacy_pending),
            legacy_payment_proof_keyboard(lang, legacy_pending["id"]),
        )
    return None


async def _answer_existing_pending(message: Message, user_id: int, lang: str) -> bool:
    prompt = await _existing_pending_prompt(user_id, lang)
    if prompt is None:
        return False

    text, reply_markup = prompt
    if reply_markup is None:
        await message.answer(text, parse_mode=None)
    else:
        await message.answer(
            text,
            reply_markup=reply_markup,
            parse_mode=None,
        )
    return True


async def _send_upgrade_start(message: Message, user_id: int, lang: str) -> None:
    if await _answer_existing_pending(message, user_id, lang):
        return

    await message.answer(
        _plans_overview(lang),
        reply_markup=upgrade_plans_keyboard(lang),
        parse_mode=None,
    )


@router.message(Command("upgrade"))
async def cmd_upgrade(message: Message) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]
    lang = await get_user_lang_or_default(user_id)
    await _send_upgrade_start(message, user_id, lang)


@router.callback_query(F.data.startswith("upgrade:cancel_pending:"))
async def cb_cancel_pending_upgrade(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is None:
        return

    user_id = callback.from_user.id
    lang = await get_user_lang_or_default(user_id)
    expected_ref = (callback.data or "").split(":", 2)[2]
    pending = await db.get_pending_upgrade_payment_for_user(user_id)
    if pending and str(pending.get("payment_ref") or "") == expected_ref:
        cancelled = await db.cancel_pending_payment(user_id)
        if cancelled:
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
    await _send_upgrade_start(callback.message, user_id, lang)


@router.callback_query(F.data.startswith("upgrade:send_proof:"))
async def cb_request_upgrade_proof(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is None:
        return

    user_id = callback.from_user.id
    lang = await get_user_lang_or_default(user_id)
    ref_code = (callback.data or "").split(":", 2)[2]
    pending = await db.request_upgrade_payment_proof(user_id, ref_code)
    if pending is None:
        await _answer_reference_unavailable(callback.message, ref_code)
        return

    await callback.message.answer(
        t(
            lang,
            "upgrade_send_proof_prompt",
            minutes=PAYMENT_PROOF_WINDOW_MINUTES,
        ),
        parse_mode=None,
    )


@router.callback_query(F.data == "upgrade:plans")
async def cb_upgrade_back_to_plans(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is None:
        return

    lang = await get_user_lang_or_default(callback.from_user.id)
    await _send_upgrade_start(callback.message, callback.from_user.id, lang)


@router.callback_query(F.data.startswith("upgrade:plan:"))
async def cb_upgrade_plan(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is None:
        return

    lang = await get_user_lang_or_default(callback.from_user.id)
    plan_key = (callback.data or "").split(":", 2)[2]
    plan = PURCHASABLE_PLANS.get(plan_key)
    if plan is None:
        await callback.message.answer(t(lang, "unknown_plan"), parse_mode=None)
        return

    if await _answer_existing_pending(
        callback.message,
        callback.from_user.id,
        lang,
    ):
        return

    await callback.message.answer(
        t(lang, "upgrade_choose_currency", plan_name=plan.name),
        reply_markup=upgrade_currency_keyboard(lang, plan.key),
        parse_mode=None,
    )


@router.callback_query(F.data.startswith("upgrade:currency:"))
async def cb_upgrade_currency(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is None:
        return

    user_id = callback.from_user.id
    lang = await get_user_lang_or_default(user_id)
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.message.answer(t(lang, "unknown_plan"), parse_mode=None)
        return

    _, _, plan_key, currency = parts
    plan = PURCHASABLE_PLANS.get(plan_key)
    amount = plan_price(plan_key, currency)
    if plan is None or amount is None:
        await callback.message.answer(t(lang, "unknown_plan"), parse_mode=None)
        return

    if await _answer_existing_pending(callback.message, user_id, lang):
        return

    payment_info = settings.upgrade_payment_info_for(currency)
    if not payment_info:
        await callback.message.answer(t(lang, "upgrade_payment_info_missing"), parse_mode=None)
        return
    if not settings.admin_id:
        await callback.message.answer(t(lang, "upgrade_admin_unavailable"), parse_mode=None)
        return

    ref_code = await _new_reference_code(user_id)
    await db.set_payment_pending(user_id, plan.key, currency, ref_code)

    await callback.message.answer(
        t(
            lang,
            "upgrade_payment_instructions",
            plan_name=plan.name,
            amount=amount,
            currency=currency,
            payment_info=payment_info,
            ref_code=ref_code,
        ),
        reply_markup=pending_upgrade_keyboard(lang, ref_code),
        parse_mode=None,
    )


@router.message(PendingUpgradeProofFilter())
async def msg_upgrade_payment_proof(
    message: Message,
    bot: Bot,
    upgrade_payment_context: tuple[dict, bool] | None = None,
) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]
    lang = await get_user_lang_or_default(user_id)
    context = upgrade_payment_context or await _proof_payment_context(message)
    if context is None:
        return
    payment, was_cancelled = context
    ref_code = str(payment.get("payment_ref") or "")

    if not was_cancelled:
        cancelled = await db.get_cancelled_payment_by_ref(ref_code)
        if cancelled and int(cancelled["user_id"]) == user_id:
            payment = cancelled
            was_cancelled = True

    if not was_cancelled:
        await db.clear_upgrade_payment_proof_request(user_id, ref_code)

    if not settings.admin_id:
        await message.answer(t(lang, "upgrade_admin_missing"), parse_mode=None)
        return

    plan = get_plan(str(payment.get("payment_plan") or ""))
    plan_name = plan.name if plan else str(payment.get("payment_plan") or "-")
    username = payment.get("username") or "-"

    try:
        await bot.forward_message(
            chat_id=settings.admin_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        await bot.send_message(
            settings.admin_id,
            (
                t(
                    "en",
                    "upgrade_admin_cancelled_proof_caption",
                    user_id=user_id,
                    username=username,
                    plan_name=plan_name,
                    currency=payment.get("payment_currency") or "-",
                    ref_code=ref_code,
                )
                if was_cancelled
                else t(
                    "en",
                    "upgrade_admin_proof_caption",
                    user_id=user_id,
                    username=username,
                    plan_name=plan_name,
                    currency=payment.get("payment_currency") or "-",
                    ref_code=ref_code,
                    approve_command=f"/approve {ref_code}",
                )
            ),
            parse_mode=None,
        )
    except Exception:
        logger.exception("Could not forward upgrade proof %s to admin.", ref_code)
        await message.answer(t(lang, "upgrade_admin_unavailable"), parse_mode=None)
        return

    if was_cancelled:
        response = t(lang, "upgrade_cancelled_proof_received", ref_code=ref_code)
    else:
        response = t(lang, "upgrade_proof_received", eta=settings.payment_review_eta)
    await message.answer(response, parse_mode=None)


async def _answer_reference_unavailable(message: Message, ref_code: str) -> None:
    cancelled = await db.get_cancelled_payment_by_ref(ref_code)
    key = "upgrade_ref_cancelled" if cancelled else "upgrade_ref_not_found"
    await message.answer(t("en", key, ref_code=ref_code), parse_mode=None)


@router.message(Command("approve"))
async def cmd_approve_payment(message: Message, bot: Bot) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer(t("en", "upgrade_approve_usage"), parse_mode=None)
        return

    ref_code = parts[1].strip()
    pending = await db.get_pending_payment_by_ref(ref_code)
    if pending is None:
        await _answer_reference_unavailable(message, ref_code)
        return

    plan_key = str(pending.get("payment_plan") or "")
    plan = PURCHASABLE_PLANS.get(plan_key)
    if plan is None:
        await message.answer(t("en", "unknown_plan"), parse_mode=None)
        return

    approved = await db.approve_upgrade_payment(ref_code, plan)
    if approved is None:
        await _answer_reference_unavailable(message, ref_code)
        return

    updated, active_plan = approved
    user_id = int(updated["user_id"])
    expires_at = active_plan["expires_at"]

    user_lang = await get_user_lang_or_default(user_id)
    user_notified = True
    try:
        await bot.send_message(
            user_id,
            t(
                user_lang,
                "upgrade_approved_user",
                plan_name=plan.name,
                expires_at=_format_date(expires_at),
            ),
            parse_mode=None,
        )
    except Exception:
        user_notified = False
        logger.exception("Upgrade approved, but user %s could not be notified.", user_id)

    await message.answer(
        t(
            "en",
            "upgrade_approved_admin",
            user_id=user_id,
            plan_name=plan.name,
            expires_at=_format_date(expires_at),
            notification_status=(
                t("en", "upgrade_user_notified")
                if user_notified
                else t("en", "upgrade_user_notification_failed")
            ),
        ),
        parse_mode=None,
    )


@router.message(Command("reject"))
async def cmd_reject_payment(message: Message, bot: Bot) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3:
        await message.answer(t("en", "upgrade_reject_usage"), parse_mode=None)
        return

    _, ref_code, reason = parts
    pending = await db.get_pending_payment_by_ref(ref_code)
    if pending is None:
        await _answer_reference_unavailable(message, ref_code)
        return

    updated = await db.set_payment_rejected(ref_code)
    if updated is None:
        await _answer_reference_unavailable(message, ref_code)
        return

    user_id = int(updated["user_id"])
    user_lang = await get_user_lang_or_default(user_id)
    user_notified = True
    try:
        await bot.send_message(
            user_id,
            t(user_lang, "upgrade_rejected_user", reason=reason),
            parse_mode=None,
        )
    except Exception:
        user_notified = False
        logger.exception("Upgrade rejected, but user %s could not be notified.", user_id)

    await message.answer(
        t(
            "en",
            "upgrade_rejected_admin",
            user_id=user_id,
            ref_code=ref_code,
            notification_status=(
                t("en", "upgrade_user_notified")
                if user_notified
                else t("en", "upgrade_user_notification_failed")
            ),
        ),
        parse_mode=None,
    )
