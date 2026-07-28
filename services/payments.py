"""Plan catalog, entitlement checks, and manual-payment helpers."""
from __future__ import annotations

import logging
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from config.settings import settings

logger = logging.getLogger(__name__)

Action = Literal["download", "conversion"]
REFERENCE_ALPHABET = string.ascii_uppercase + string.digits
SUPPORTED_CURRENCIES = ("USD", "ILS", "RUB")
PAYMENT_PROOF_WINDOW_MINUTES = 30
PAYMENT_PROOF_WINDOW = timedelta(minutes=PAYMENT_PROOF_WINDOW_MINUTES)


@dataclass(frozen=True)
class Plan:
    """One source of truth for every plan capability and limit."""

    key: str
    name: str
    plan_type: Literal["free", "subscription", "package"]
    duration_days: int
    prices: dict[str, str]
    max_file_size_mb: int
    allows_downloads: bool
    allows_conversions: bool
    plan_priority: int
    daily_download_limit: int | None = None
    daily_conversion_limit: int | None = None
    package_uses: int | None = None

    # Reserved metadata only. Actual 1080p/playlist enforcement belongs in
    # quality selection and services/downloader.py once that feature is built.
    max_video_height: int | None = None
    playlist_support: bool = False

    @property
    def unlimited_downloads(self) -> bool:
        return self.plan_type == "subscription" and self.allows_downloads

    @property
    def unlimited_conversions(self) -> bool:
        return self.plan_type == "subscription" and self.allows_conversions

    @property
    def priority_level(self) -> int:
        """Compatibility name for existing plan display/storage code."""
        return self.plan_priority


PLANS: dict[str, Plan] = {
    "free": Plan(
        key="free",
        name="Free Plan - Starter",
        plan_type="free",
        duration_days=2,
        prices={},
        max_file_size_mb=500,
        allows_downloads=True,
        allows_conversions=True,
        plan_priority=0,
        daily_download_limit=10,
        daily_conversion_limit=3,
    ),
    "downloader_pro": Plan(
        key="downloader_pro",
        name="Downloader Pro",
        plan_type="subscription",
        duration_days=30,
        prices={"USD": "$1.99", "ILS": "₪7", "RUB": "₽200"},
        max_file_size_mb=2000,
        allows_downloads=True,
        allows_conversions=False,
        plan_priority=1,
        max_video_height=1080,
        playlist_support=True,
    ),
    "converter_pro": Plan(
        key="converter_pro",
        name="Converter Pro",
        plan_type="subscription",
        duration_days=30,
        prices={"USD": "$1.99", "ILS": "₪7", "RUB": "₽200"},
        max_file_size_mb=2000,
        allows_downloads=False,
        allows_conversions=True,
        plan_priority=1,
    ),
    "all_in_one": Plan(
        key="all_in_one",
        name="All-in-One Pro",
        plan_type="subscription",
        duration_days=30,
        prices={"USD": "$2.99", "ILS": "₪11", "RUB": "₽300"},
        max_file_size_mb=2000,
        allows_downloads=True,
        allows_conversions=True,
        plan_priority=1,
        max_video_height=1080,
        playlist_support=True,
    ),
    "annual": Plan(
        key="annual",
        name="Annual All-in-One",
        plan_type="subscription",
        duration_days=365,
        prices={"USD": "$29.99", "ILS": "₪110", "RUB": "₽3000"},
        max_file_size_mb=2000,
        allows_downloads=True,
        allows_conversions=True,
        plan_priority=2,
        max_video_height=1080,
        playlist_support=True,
    ),
    "starter_pack": Plan(
        key="starter_pack",
        name="Starter Pack",
        plan_type="package",
        duration_days=30,
        prices={"USD": "$1.50", "ILS": "₪5", "RUB": "₽150"},
        max_file_size_mb=500,
        allows_downloads=True,
        allows_conversions=True,
        plan_priority=0,
        package_uses=15,
    ),
    "pro_pack": Plan(
        key="pro_pack",
        name="Pro Pack",
        plan_type="package",
        duration_days=60,
        prices={"USD": "$4.99", "ILS": "₪18", "RUB": "₽500"},
        max_file_size_mb=2000,
        allows_downloads=True,
        allows_conversions=True,
        plan_priority=1,
        package_uses=60,
    ),
    "ultra_pack": Plan(
        key="ultra_pack",
        name="Ultra Pack",
        plan_type="package",
        duration_days=90,
        prices={"USD": "$9.99", "ILS": "₪36", "RUB": "₽1000"},
        max_file_size_mb=2000,
        allows_downloads=True,
        allows_conversions=True,
        plan_priority=1,
        package_uses=150,
    ),
}

PURCHASABLE_PLANS: dict[str, Plan] = {
    key: plan for key, plan in PLANS.items() if plan.plan_type != "free"
}


def get_plan(plan_key: str) -> Plan | None:
    return PLANS.get(plan_key)


def get_plan_amount(plan_key: str, currency: str) -> str | None:
    plan = get_plan(plan_key)
    if plan is None:
        return None
    return plan.prices.get(currency)


def plan_price(plan_key: str, currency: str) -> str | None:
    return get_plan_amount(plan_key, currency)


def generate_reference_code(user_id: int) -> str:
    suffix = "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(4))
    return f"SSB-{user_id}-{suffix}"


def payment_proof_request_is_active(requested_at: object) -> bool:
    if not isinstance(requested_at, datetime):
        return False
    if requested_at.tzinfo is None:
        requested_at = requested_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - requested_at.astimezone(timezone.utc)
    return timedelta(0) <= age <= PAYMENT_PROOF_WINDOW


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _plan_allows(plan: Plan, action: Action) -> bool:
    return plan.allows_downloads if action == "download" else plan.allows_conversions


def _file_exceeds_plan(plan: Plan, file_size_bytes: int | None) -> bool:
    if file_size_bytes is None:
        return False
    return file_size_bytes > plan.max_file_size_mb * 1024 * 1024


async def user_has_active_plan(user_id: int) -> bool:
    """Return True for active paid plans and lazily downgrade expired plans."""
    from services import db

    row = await db.get_user_plan(user_id)
    if not row or row.get("plan") == "free":
        return False

    plan = get_plan(str(row.get("plan") or ""))
    plan_type = str(row.get("plan_type") or (plan.plan_type if plan else ""))
    expires_at = (
        row.get("package_expires_at")
        if plan_type == "package"
        else row.get("plan_expires_at")
    )
    if expires_at is None:
        return True
    if isinstance(expires_at, datetime) and _as_utc(expires_at) > datetime.now(timezone.utc):
        return True

    await db.set_user_plan(user_id, "free", None)
    return False


async def check_usage_allowed(
    user_id: int,
    action: Action,
    file_size_bytes: int | None = None,
) -> tuple[bool, str | None]:
    """Apply every plan rule and return a translated denial reason key."""
    from services import db

    if action not in ("download", "conversion"):
        raise ValueError(f"Unsupported usage action: {action}")
    if settings.admin_id and user_id == settings.admin_id:
        return True, None

    row = await db.get_user_plan(user_id)
    if row is None:
        await db.register_user(user_id)
        row = await db.get_user_plan(user_id)

    plan_key = str((row or {}).get("plan") or "free")
    plan = get_plan(plan_key)
    if plan is None:
        return False, "plan_upgrade_required"
    plan_type = str((row or {}).get("plan_type") or plan.plan_type)
    now = datetime.now(timezone.utc)

    if plan_type == "free":
        row = await db.prepare_free_usage_window(
            user_id,
            now=now,
            next_reset_at=now + timedelta(days=1),
        )
        free_started_at = row.get("free_started_at")
        if (
            isinstance(free_started_at, datetime)
            and now >= _as_utc(free_started_at) + timedelta(days=plan.duration_days)
        ):
            return False, "free_expired"
        if _file_exceeds_plan(plan, file_size_bytes):
            return False, "file_too_large_for_plan"

        count_key = (
            "downloads_today" if action == "download" else "conversions_today"
        )
        limit = (
            plan.daily_download_limit
            if action == "download"
            else plan.daily_conversion_limit
        )
        if limit is not None and int(row.get(count_key) or 0) >= limit:
            return False, "free_daily_limit_reached"
        return True, None

    if plan_type == "subscription":
        if not await user_has_active_plan(user_id):
            return False, "plan_expired"
        if not _plan_allows(plan, action):
            return False, "plan_feature_not_included"
        if _file_exceeds_plan(plan, file_size_bytes):
            return False, "file_too_large_for_plan"
        return True, None

    if plan_type == "package":
        package_expires_at = (row or {}).get("package_expires_at")
        if (
            not isinstance(package_expires_at, datetime)
            or _as_utc(package_expires_at) <= now
        ):
            await db.set_user_plan(user_id, "free", None)
            return False, "package_expired"
        if int((row or {}).get("package_uses_remaining") or 0) <= 0:
            return False, "package_depleted"
        if _file_exceeds_plan(plan, file_size_bytes):
            return False, "file_too_large_for_plan"
        return True, None

    return False, "plan_upgrade_required"


async def increment_usage(user_id: int, action: Action) -> dict | None:
    """Record one successfully delivered download or conversion."""
    from services import db

    if action not in ("download", "conversion"):
        raise ValueError(f"Unsupported usage action: {action}")
    if settings.admin_id and user_id == settings.admin_id:
        return None

    row = await db.get_user_plan(user_id)
    plan_key = str((row or {}).get("plan") or "free")
    plan = get_plan(plan_key) or PLANS["free"]
    plan_type = str((row or {}).get("plan_type") or plan.plan_type)

    if plan_type == "free":
        now = datetime.now(timezone.utc)
        await db.prepare_free_usage_window(
            user_id,
            now=now,
            next_reset_at=now + timedelta(days=1),
        )
        return await db.increment_free_usage(user_id, action)

    if plan_type == "package":
        updated = await db.decrement_package_usage(user_id)
        if updated is None:
            logger.warning(
                "Successful %s for user %s could not consume package usage.",
                action,
                user_id,
            )
        return updated

    return row
