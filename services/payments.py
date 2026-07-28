"""Manual upgrade/payment helpers for Sandy Squirrel."""
from __future__ import annotations

import logging
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from config.settings import settings
from services.plans import PLANS as _PLAN_CATALOG
from services.plans import Plan as UpgradePlan

logger = logging.getLogger(__name__)

REFERENCE_ALPHABET = string.ascii_uppercase + string.digits
SUPPORTED_CURRENCIES = ("RUB", "USD", "ILS")
PAYMENT_PROOF_WINDOW_MINUTES = 30
PAYMENT_PROOF_WINDOW = timedelta(minutes=PAYMENT_PROOF_WINDOW_MINUTES)
FREE_DAILY_LIMITS = {"download": 10, "conversion": 3}
FREE_MAX_FILE_SIZE_MB = 500

# Keep one source of truth for prices, durations, and plan capabilities.
PLANS: dict[str, UpgradePlan] = dict(_PLAN_CATALOG)


@dataclass(frozen=True)
class PlanAccess:
    allowed: bool
    operation: str
    plan_key: str
    max_file_size_mb: int
    message_key: str | None = None
    daily_limit: int | None = None


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
    """Return True for active paid users and lazily downgrade expired users."""
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


def _validate_operation(operation: str) -> None:
    if operation not in FREE_DAILY_LIMITS:
        raise ValueError(f"Unsupported plan operation: {operation}")


def _free_day_bounds() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    starts_at = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return starts_at, starts_at + timedelta(days=1)


async def check_plan_access(user_id: int, operation: str) -> PlanAccess:
    """Return the user's current entitlement without consuming usage.

    The free tier follows the limits already advertised in the Plans menu:
    10 downloads and 3 conversions per UTC day, with a 500 MB file limit.
    """
    from services import db

    _validate_operation(operation)
    if settings.admin_id and user_id == settings.admin_id:
        return PlanAccess(
            allowed=True,
            operation=operation,
            plan_key="admin",
            max_file_size_mb=max(
                settings.max_file_size_mb,
                settings.max_convert_file_size_mb,
            ),
        )

    user = await db.get_user_plan(user_id)
    previous_plan = str((user or {}).get("plan") or "free")
    was_paid = previous_plan != "free"
    has_active_paid_plan = await user_has_active_plan(user_id)

    if was_paid and not has_active_paid_plan:
        return PlanAccess(
            allowed=False,
            operation=operation,
            plan_key=previous_plan,
            max_file_size_mb=FREE_MAX_FILE_SIZE_MB,
            message_key="plan_expired",
        )

    if not has_active_paid_plan:
        daily_limit = FREE_DAILY_LIMITS[operation]
        starts_at, expires_at = _free_day_bounds()
        usage = await db.get_or_create_free_daily_usage(
            user_id=user_id,
            starts_at=starts_at,
            expires_at=expires_at,
            download_limit=FREE_DAILY_LIMITS["download"],
            conversion_limit=FREE_DAILY_LIMITS["conversion"],
            max_file_size_mb=FREE_MAX_FILE_SIZE_MB,
        )
        remaining_key = (
            "downloads_remaining"
            if operation == "download"
            else "conversions_remaining"
        )
        remaining = int(usage.get(remaining_key) or 0)
        return PlanAccess(
            allowed=remaining > 0,
            operation=operation,
            plan_key="free",
            max_file_size_mb=FREE_MAX_FILE_SIZE_MB,
            message_key=(
                None
                if remaining > 0
                else f"plan_free_{operation}_limit_reached"
            ),
            daily_limit=daily_limit,
        )

    details = await db.get_user_plan_details(user_id)
    catalog_plan = get_plan(previous_plan)
    if details is None and catalog_plan is None:
        return PlanAccess(
            allowed=False,
            operation=operation,
            plan_key=previous_plan,
            max_file_size_mb=FREE_MAX_FILE_SIZE_MB,
            message_key="plan_upgrade_required",
        )

    max_file_size_mb = int(
        (details or {}).get("max_file_size_mb")
        or (catalog_plan.max_file_size_mb if catalog_plan else FREE_MAX_FILE_SIZE_MB)
    )
    unlimited_key = (
        "unlimited_downloads"
        if operation == "download"
        else "unlimited_conversions"
    )
    remaining_key = (
        "downloads_remaining"
        if operation == "download"
        else "conversions_remaining"
    )
    unlimited = bool(
        (details or {}).get(unlimited_key)
        if details is not None
        else getattr(catalog_plan, unlimited_key)
    )
    if unlimited:
        return PlanAccess(
            allowed=True,
            operation=operation,
            plan_key=previous_plan,
            max_file_size_mb=max_file_size_mb,
        )

    plan_type = str(
        (details or {}).get("plan_type")
        or (catalog_plan.plan_type if catalog_plan else "")
    )
    if plan_type == "package":
        if details is not None:
            balances = [
                int(details.get(key) or 0)
                for key in ("downloads_remaining", "conversions_remaining")
            ]
        else:
            balances = [
                int(getattr(catalog_plan, key) or 0)
                for key in ("downloads_remaining", "conversions_remaining")
            ]
        remaining = min(balances)
    else:
        value = (
            (details or {}).get(remaining_key)
            if details is not None
            else getattr(catalog_plan, remaining_key)
        )
        remaining = int(value or 0)

    return PlanAccess(
        allowed=remaining > 0,
        operation=operation,
        plan_key=previous_plan,
        max_file_size_mb=max_file_size_mb,
        message_key=(
            None
            if remaining > 0
            else (
                "plan_usage_exhausted"
                if plan_type == "package"
                else f"plan_{operation}_not_included"
            )
        ),
    )


async def record_plan_usage(user_id: int, operation: str) -> None:
    """Record one successful operation for free users or usage packages."""
    from services import db

    _validate_operation(operation)
    if settings.admin_id and user_id == settings.admin_id:
        return

    user = await db.get_user_plan(user_id)
    plan_key = str((user or {}).get("plan") or "free")
    if plan_key == "free":
        starts_at, expires_at = _free_day_bounds()
        await db.get_or_create_free_daily_usage(
            user_id=user_id,
            starts_at=starts_at,
            expires_at=expires_at,
            download_limit=FREE_DAILY_LIMITS["download"],
            conversion_limit=FREE_DAILY_LIMITS["conversion"],
            max_file_size_mb=FREE_MAX_FILE_SIZE_MB,
        )
        consumed = await db.consume_free_daily_usage(
            user_id,
            operation,
            starts_at,
        )
        if consumed is None:
            logger.warning(
                "Successful %s for user %s could not consume free usage.",
                operation,
                user_id,
            )
        return

    details = await db.get_user_plan_details(user_id)
    catalog_plan = get_plan(plan_key)
    plan_type = str(
        (details or {}).get("plan_type")
        or (catalog_plan.plan_type if catalog_plan else "")
    )
    if plan_type == "package":
        consumed = await db.consume_package_usage(user_id)
        if consumed is None:
            logger.warning(
                "Successful %s for user %s could not consume package usage.",
                operation,
                user_id,
            )
