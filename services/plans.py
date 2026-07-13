"""Static Sandy Squirrel plan catalog."""
from __future__ import annotations

from dataclasses import dataclass

SUPPORTED_CURRENCIES = ("USD", "ILS", "RUB")


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    plan_type: str
    duration_days: int
    prices: dict[str, str]
    max_file_size_mb: int
    unlimited_downloads: bool
    unlimited_conversions: bool
    downloads_remaining: int | None
    conversions_remaining: int | None
    priority_level: int


PLANS: dict[str, Plan] = {
    "downloader_pro": Plan(
        key="downloader_pro",
        name="Downloader Pro",
        plan_type="subscription",
        duration_days=30,
        prices={"USD": "$1.99", "ILS": "₪7", "RUB": "₽200"},
        max_file_size_mb=2000,
        unlimited_downloads=True,
        unlimited_conversions=False,
        downloads_remaining=None,
        conversions_remaining=0,
        priority_level=1,
    ),
    "converter_pro": Plan(
        key="converter_pro",
        name="Converter Pro",
        plan_type="subscription",
        duration_days=30,
        prices={"USD": "$1.99", "ILS": "₪7", "RUB": "₽200"},
        max_file_size_mb=2000,
        unlimited_downloads=False,
        unlimited_conversions=True,
        downloads_remaining=0,
        conversions_remaining=None,
        priority_level=1,
    ),
    "all_in_one": Plan(
        key="all_in_one",
        name="All-in-One Pro",
        plan_type="subscription",
        duration_days=30,
        prices={"USD": "$2.99", "ILS": "₪11", "RUB": "₽300"},
        max_file_size_mb=2000,
        unlimited_downloads=True,
        unlimited_conversions=True,
        downloads_remaining=None,
        conversions_remaining=None,
        priority_level=1,
    ),
    "annual": Plan(
        key="annual",
        name="Annual All-in-One",
        plan_type="subscription",
        duration_days=365,
        prices={"USD": "$29.99", "ILS": "₪110", "RUB": "₽3000"},
        max_file_size_mb=2000,
        unlimited_downloads=True,
        unlimited_conversions=True,
        downloads_remaining=None,
        conversions_remaining=None,
        priority_level=2,
    ),
    "starter_pack": Plan(
        key="starter_pack",
        name="Starter Pack",
        plan_type="package",
        duration_days=30,
        prices={"USD": "$1.50", "ILS": "₪5", "RUB": "₽150"},
        max_file_size_mb=500,
        unlimited_downloads=False,
        unlimited_conversions=False,
        downloads_remaining=15,
        conversions_remaining=15,
        priority_level=0,
    ),
    "pro_pack": Plan(
        key="pro_pack",
        name="Pro Pack",
        plan_type="package",
        duration_days=60,
        prices={"USD": "$4.99", "ILS": "₪18", "RUB": "₽500"},
        max_file_size_mb=2000,
        unlimited_downloads=False,
        unlimited_conversions=False,
        downloads_remaining=60,
        conversions_remaining=60,
        priority_level=1,
    ),
    "ultra_pack": Plan(
        key="ultra_pack",
        name="Ultra Pack",
        plan_type="package",
        duration_days=90,
        prices={"USD": "$9.99", "ILS": "₪36", "RUB": "₽1000"},
        max_file_size_mb=2000,
        unlimited_downloads=False,
        unlimited_conversions=False,
        downloads_remaining=150,
        conversions_remaining=150,
        priority_level=1,
    ),
}


def get_plan(plan_key: str) -> Plan | None:
    return PLANS.get(plan_key)


def get_plan_amount(plan_key: str, currency: str) -> str | None:
    plan = get_plan(plan_key)
    if plan is None:
        return None
    return plan.prices.get(currency)
