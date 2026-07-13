"""Main menu flow for Sandy Squirrel."""
from __future__ import annotations

import logging
from html import escape
from pathlib import Path
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup, Message, User
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config.settings import settings
from services import db
from services.plans import SUPPORTED_CURRENCIES, get_plan, get_plan_amount
from services.user_store import get_user_lang_or_default, set_user_mode
from utils.i18n import t

logger = logging.getLogger(__name__)
router = Router(name="menu")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MENU_PHOTO_CANDIDATES = (
    PROJECT_ROOT / "main_menu.jpg",
)
CURRENCY_BUTTONS = {
    "USD": "💵 USD",
    "ILS": "₪ ILS",
    "RUB": "₽ RUB",
}


class PendingPaymentReceiptFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        if message.from_user is None:
            return False
        pending = await db.get_pending_payment_waiting_for_receipt(message.from_user.id)
        return pending is not None


def main_menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=t(lang, "menu_btn_download"), callback_data="menu:download")
    builder.button(text=t(lang, "menu_btn_convert"), callback_data="menu:convert")
    builder.button(text=t(lang, "menu_btn_plans"), callback_data="menu:plans")
    builder.button(text=t(lang, "menu_btn_my_plan"), callback_data="menu:my_plan")
    builder.button(text=t(lang, "menu_btn_about"), callback_data="menu:about")
    builder.button(text=t(lang, "menu_btn_language"), callback_data="menu:language")
    builder.adjust(2, 2, 2)
    return builder.as_markup()


def plans_keyboard(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t(lang, "plan_btn_downloader_pro"),
        callback_data="plan:choose:downloader_pro",
    )
    builder.button(
        text=t(lang, "plan_btn_converter_pro"),
        callback_data="plan:choose:converter_pro",
    )
    builder.button(
        text=t(lang, "plan_btn_all_in_one"),
        callback_data="plan:choose:all_in_one",
    )
    builder.button(
        text=t(lang, "plan_btn_annual"),
        callback_data="plan:choose:annual",
    )
    builder.button(
        text=t(lang, "plan_btn_starter_pack"),
        callback_data="plan:choose:starter_pack",
    )
    builder.button(
        text=t(lang, "plan_btn_pro_pack"),
        callback_data="plan:choose:pro_pack",
    )
    builder.button(
        text=t(lang, "plan_btn_ultra_pack"),
        callback_data="plan:choose:ultra_pack",
    )
    builder.button(text=t(lang, "btn_back_main_menu"), callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def currency_keyboard(lang: str, plan_key: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for currency in SUPPORTED_CURRENCIES:
        builder.button(
            text=CURRENCY_BUTTONS[currency],
            callback_data=f"pay:currency:{plan_key}:{currency}",
        )
    builder.button(text=t(lang, "btn_back_plans"), callback_data="menu:plans")
    builder.adjust(1)
    return builder.as_markup()


def admin_payment_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=t("en", "admin_btn_approve"), callback_data=f"pay:approve:{payment_id}")
    builder.button(text=t("en", "admin_btn_reject"), callback_data=f"pay:reject:{payment_id}")
    builder.adjust(2)
    return builder.as_markup()


def _menu_photo_path() -> Path | None:
    return next((path for path in MENU_PHOTO_CANDIDATES if path.is_file()), None)


def _display_name(user: User | None, lang: str) -> str:
    if user and user.username:
        return "@" + user.username
    if user and user.first_name:
        return user.first_name
    return t(lang, "friend_name")


def _username_for_storage(user: User | None) -> str:
    if user is None:
        return ""
    if user.username:
        return "@" + user.username
    if user.full_name:
        return user.full_name
    return str(user.id)


def _format_date(value) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d")
    return str(value)


def _is_expired(plan_row: dict) -> bool:
    expires_at = plan_row.get("expires_at")
    if not isinstance(expires_at, datetime):
        return False
    return expires_at <= datetime.now(expires_at.tzinfo or timezone.utc)


def _priority_label(lang: str, priority_level: int) -> str:
    if priority_level >= 2:
        return t(lang, "priority_highest")
    if priority_level == 1:
        return t(lang, "priority_priority")
    return t(lang, "priority_low")


def _remaining_text(lang: str, plan_row: dict) -> str:
    if plan_row.get("plan_type") == "package":
        count = plan_row.get("downloads_remaining") or 0
        return t(lang, "my_plan_remaining_package", count=count)
    if plan_row.get("unlimited_downloads") and plan_row.get("unlimited_conversions"):
        return t(lang, "my_plan_remaining_unlimited_all")
    if plan_row.get("unlimited_downloads"):
        return t(lang, "my_plan_remaining_unlimited_downloads")
    if plan_row.get("unlimited_conversions"):
        return t(lang, "my_plan_remaining_unlimited_conversions")
    return t(lang, "my_plan_remaining_none")


def _pending_payment_text(lang: str, payment: dict) -> str:
    receipt_status = (
        t(lang, "payment_receipt_received")
        if payment.get("receipt_file_id")
        else t(lang, "payment_awaiting_receipt")
    )
    return t(
        lang,
        "payment_pending",
        payment_id=payment["id"],
        plan_name=payment["plan_name"],
        currency=payment["currency"],
        amount=payment["amount"],
        receipt_status=receipt_status,
    )


async def _my_plan_text(user_id: int, lang: str) -> str:
    plan_row = await db.get_user_plan(user_id)
    if not plan_row:
        return t(
            lang,
            "my_plan_free_details",
            priority=_priority_label(lang, 0),
        )

    status = t(lang, "plan_status_expired") if _is_expired(plan_row) else t(lang, "plan_status_active")
    return t(
        lang,
        "my_plan_paid_details",
        plan_name=plan_row["plan_name"],
        status=status,
        expires_at=_format_date(plan_row["expires_at"]),
        remaining=_remaining_text(lang, plan_row),
        max_file_size_mb=plan_row["max_file_size_mb"],
        priority=_priority_label(lang, plan_row["priority_level"]),
    )


def _payment_instruction_text(lang: str, payment: dict, details: str) -> str:
    instruction = t(
        lang,
        f"payment_instructions_{payment['currency'].lower()}",
        plan_name=payment["plan_name"],
        amount=payment["amount"],
        details=details,
    )
    return (
        t(
            lang,
            "payment_request_created",
            payment_id=payment["id"],
            plan_name=payment["plan_name"],
            currency=payment["currency"],
            amount=payment["amount"],
        )
        + "\n\n"
        + instruction
        + "\n\n"
        + t(lang, "payment_send_receipt")
    )


async def _notify_admin_payment(bot, payment: dict) -> bool:
    if not settings.admin_id:
        logger.warning("ADMIN_ID is not configured; payment %s cannot be reviewed.", payment["id"])
        return False

    caption = t(
        "en",
        "admin_payment_request",
        payment_id=payment["id"],
        username=payment.get("username") or "-",
        user_id=payment["user_id"],
        plan_name=payment["plan_name"],
        currency=payment["currency"],
        amount=payment["amount"],
        status=payment["status"],
    )
    markup = admin_payment_keyboard(payment["id"])

    if payment.get("receipt_file_type") == "photo":
        await bot.send_photo(
            chat_id=settings.admin_id,
            photo=payment["receipt_file_id"],
            caption=caption,
            reply_markup=markup,
            parse_mode=None,
        )
    else:
        await bot.send_document(
            chat_id=settings.admin_id,
            document=payment["receipt_file_id"],
            caption=caption,
            reply_markup=markup,
            parse_mode=None,
        )
    return True


async def show_main_menu(
    message: Message,
    lang: str | None = None,
    user: User | None = None,
) -> None:
    actor = user or message.from_user
    user_id = actor.id if actor else 0
    effective_lang = lang or await get_user_lang_or_default(user_id)
    photo_path = _menu_photo_path()

    if photo_path:
        try:
            await message.answer_photo(photo=FSInputFile(photo_path))
        except Exception as exc:
            logger.warning("Could not send main menu photo %s: %s", photo_path, exc)

    username = escape(_display_name(actor, effective_lang))
    await message.answer(
        t(effective_lang, "main_menu_welcome", username=username),
        reply_markup=main_menu_keyboard(effective_lang),
    )


@router.callback_query(F.data.startswith("menu:"))
async def cb_main_menu(callback: CallbackQuery) -> None:
    await callback.answer()

    if callback.message is None:
        return

    action = (callback.data or "").split(":", 1)[1]
    user_id = callback.from_user.id
    lang = await get_user_lang_or_default(user_id)

    if action in {"back", "main"}:
        await show_main_menu(callback.message, lang, user=callback.from_user)
        return

    if action == "download":
        await set_user_mode(user_id, "downloader")
        await callback.message.answer(t(lang, "menu_download_instruction"))
        return

    if action == "convert":
        await set_user_mode(user_id, "converter")
        await callback.message.answer(t(lang, "menu_convert_instruction"))
        return

    if action == "plans":
        await callback.message.answer(
            t(lang, "menu_plans"),
            reply_markup=plans_keyboard(lang),
        )
        return

    if action == "my_plan":
        await callback.message.answer(
            await _my_plan_text(user_id, lang),
            parse_mode=None,
        )
        return

    if action == "about":
        await callback.message.answer(t(lang, "menu_about"), parse_mode=None)
        return

    if action == "language":
        from handlers.language import language_keyboard

        await callback.message.answer(
            t(None, "choose_language"),
            reply_markup=language_keyboard(),
        )


@router.callback_query(F.data.startswith("plan:"))
async def cb_plan_choice(callback: CallbackQuery) -> None:
    await callback.answer()

    if callback.message is None:
        return

    user_id = callback.from_user.id
    lang = await get_user_lang_or_default(user_id)
    data = callback.data or ""
    parts = data.split(":")

    if len(parts) == 3 and parts[1] == "choose":
        plan_key = parts[2]
    elif len(parts) == 2:
        plan_key = parts[1]
    else:
        await callback.message.answer(t(lang, "unknown_plan"))
        return

    pending = await db.get_pending_payment_for_user(user_id)
    if pending:
        await callback.message.answer(_pending_payment_text(lang, pending), parse_mode=None)
        return

    plan = get_plan(plan_key)
    if plan is None:
        await callback.message.answer(t(lang, "unknown_plan"))
        return

    await callback.message.answer(
        t(lang, "choose_currency", plan_name=plan.name),
        reply_markup=currency_keyboard(lang, plan.key),
        parse_mode=None,
    )


@router.callback_query(F.data.startswith("pay:currency:"))
async def cb_payment_currency(callback: CallbackQuery) -> None:
    await callback.answer()

    if callback.message is None:
        return

    user_id = callback.from_user.id
    lang = await get_user_lang_or_default(user_id)
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.message.answer(t(lang, "unknown_plan"))
        return

    _, _, plan_key, currency = parts
    pending = await db.get_pending_payment_for_user(user_id)
    if pending:
        await callback.message.answer(_pending_payment_text(lang, pending), parse_mode=None)
        return

    plan = get_plan(plan_key)
    amount = get_plan_amount(plan_key, currency)
    if plan is None or amount is None:
        await callback.message.answer(t(lang, "unknown_plan"))
        return

    details = settings.payment_details_for(currency)
    if not details:
        await callback.message.answer(t(lang, "payment_details_missing"), parse_mode=None)
        return

    payment = await db.create_payment_request(
        user_id=user_id,
        username=_username_for_storage(callback.from_user),
        plan=plan,
        currency=currency,
        amount=amount,
    )
    await callback.message.answer(
        _payment_instruction_text(lang, payment, details),
        parse_mode=None,
    )


@router.message(PendingPaymentReceiptFilter())
async def msg_payment_receipt(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return

    user_id = message.from_user.id
    lang = await get_user_lang_or_default(user_id)
    pending = await db.get_pending_payment_waiting_for_receipt(user_id)
    if pending is None:
        return

    receipt_file_id = None
    receipt_file_type = None
    if message.photo:
        receipt_file_id = message.photo[-1].file_id
        receipt_file_type = "photo"
    elif message.document:
        receipt_file_id = message.document.file_id
        receipt_file_type = "document"

    if not receipt_file_id or not receipt_file_type:
        await message.answer(t(lang, "payment_receipt_required"), parse_mode=None)
        return

    payment = await db.attach_payment_receipt(
        pending["id"],
        receipt_file_id,
        receipt_file_type,
    )
    if payment is None:
        await message.answer(t(lang, "payment_not_found"), parse_mode=None)
        return

    sent_to_admin = await _notify_admin_payment(bot, payment)
    if sent_to_admin:
        await message.answer(t(lang, "payment_sent_to_admin"), parse_mode=None)
    else:
        await message.answer(t(lang, "payment_admin_missing"), parse_mode=None)


@router.callback_query(F.data.regexp(r"^pay:(approve|reject):\d+$"))
async def cb_admin_payment_review(callback: CallbackQuery, bot: Bot) -> None:
    admin_lang = await get_user_lang_or_default(callback.from_user.id)
    if callback.from_user.id != settings.admin_id:
        await callback.answer(t(admin_lang, "not_authorized"), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    action = parts[1]
    payment_id = int(parts[2])
    payment = await db.get_payment(payment_id)
    if payment is None:
        await callback.answer(t("en", "payment_not_found"), show_alert=True)
        return
    if payment["status"] != "pending":
        await callback.answer(t("en", "payment_already_reviewed"), show_alert=True)
        return

    if action == "approve":
        plan = get_plan(payment["plan_key"])
        if plan is None:
            await callback.answer(t("en", "unknown_plan"), show_alert=True)
            return
        reviewed_payment = await db.set_payment_status(
            payment_id,
            "approved",
            callback.from_user.id,
        )
        if reviewed_payment is None:
            await callback.answer(t("en", "payment_already_reviewed"), show_alert=True)
            return
        plan_row = await db.activate_user_plan(payment["user_id"], plan)
        user_lang = await get_user_lang_or_default(payment["user_id"])
        await bot.send_message(
            payment["user_id"],
            t(
                user_lang,
                "payment_approved_user",
                plan_name=plan.name,
                expires_at=_format_date(plan_row["expires_at"]),
            ),
            parse_mode=None,
        )
        await callback.answer(t("en", "payment_approved_admin"), show_alert=True)
    else:
        reviewed_payment = await db.set_payment_status(
            payment_id,
            "rejected",
            callback.from_user.id,
        )
        if reviewed_payment is None:
            await callback.answer(t("en", "payment_already_reviewed"), show_alert=True)
            return
        user_lang = await get_user_lang_or_default(payment["user_id"])
        await bot.send_message(
            payment["user_id"],
            t(user_lang, "payment_rejected_user", plan_name=payment["plan_name"]),
            parse_mode=None,
        )
        await callback.answer(t("en", "payment_rejected_admin"), show_alert=True)

    if callback.message and reviewed_payment:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception as exc:
            logger.warning("Could not remove payment review buttons: %s", exc)
