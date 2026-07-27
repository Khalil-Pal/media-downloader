"""Manual upgrade/payment helpers for Sandy Squirrel."""
from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta, timezone

from services.plans import PLANS as _PLAN_CATALOG
from services.plans import Plan as UpgradePlan

REFERENCE_ALPHABET = string.ascii_uppercase + string.digits
SUPPORTED_CURRENCIES = ("RUB", "USD", "ILS")
PAYMENT_PROOF_WINDOW_MINUTES = 30
PAYMENT_PROOF_WINDOW = timedelta(minutes=PAYMENT_PROOF_WINDOW_MINUTES)

# Keep one source of truth for prices, durations, and plan capabilities.
PLANS: dict[str, UpgradePlan] = dict(_PLAN_CATALOG)


def get_plan(plan_key: str) -> UpgradePlan | None:
    return PLANS.get(plan_key)


def generate_reference_code(user_id: int) -> str:
    suffix = "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(4))
    return f"SSB-{user_id}-{suffix}"


def plan_price(plan_key: str, currency: str) -> str | None:
    plan = get_plan(plan_key)
    if plan is None:
        return None
    return plan.prices.get(currency)


def payment_proof_request_is_active(requested_at: object) -> bool:
    if not isinstance(requested_at, datetime):
        return False
    if requested_at.tzinfo is None:
        requested_at = requested_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - requested_at.astimezone(timezone.utc)
    return timedelta(0) <= age <= PAYMENT_PROOF_WINDOW


async def user_has_active_plan(user_id: int) -> bool:
    """Return True for active paid users and lazily downgrade expired users.

    Future feature gate example:
        if not await user_has_active_plan(user_id):
            await message.answer("Please upgrade with /upgrade.")
            return
    """
    from services import db

    row = await db.get_user_plan(user_id)
    if not row or row.get("plan") == "free":
        return False

    expires_at = row.get("plan_expires_at")
    if expires_at is None:
        return True

    if isinstance(expires_at, datetime):
        now = datetime.now(expires_at.tzinfo or timezone.utc)
        if expires_at > now:
            return True

    await db.set_user_plan(user_id, "free", None)
    return False
