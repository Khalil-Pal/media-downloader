"""
utils/schedule.py - Working hours checker
"""
from __future__ import annotations

from datetime import datetime, time
import zoneinfo

# Configure your timezone and hours here
TIMEZONE = zoneinfo.ZoneInfo("Asia/Jerusalem")

WORKING_HOURS: dict[int, tuple[time, time]] = {
    0: (time(9, 0), time(15, 0)),   # Monday
    1: (time(9, 0), time(15, 0)),   # Tuesday
    2: (time(9, 0), time(15, 0)),   # Wednesday
    3: (time(9, 0), time(15, 0)),   # Thursday
    4: (time(9, 0), time(15, 0)),   # Friday
    5: (time(9, 0), time(13, 0)),   # Saturday
    6: (time(9, 0), time(13, 0)),   # Sunday
}


def is_open() -> bool:
    """Return True if current time is within working hours."""
    now = datetime.now(TIMEZONE)
    weekday = now.weekday()
    current_time = now.time().replace(tzinfo=None)

    if weekday not in WORKING_HOURS:
        return False

    open_time, close_time = WORKING_HOURS[weekday]
    return open_time <= current_time <= close_time


def get_status_message() -> str:
    """Return a friendly message about current availability."""
    now = datetime.now(TIMEZONE)
    weekday = now.weekday()
    current_time = now.time().replace(tzinfo=None)

    if weekday not in WORKING_HOURS:
        return (
            "🔴 Sandy Squirrel is currently offline.\n\n"
            "🕐 Working hours:\n"
            "Mon-Fri: 9:00 - 15:00\n"
            "Sat-Sun: 9:00 - 13:00"
        )

    open_time, close_time = WORKING_HOURS[weekday]

    if current_time < open_time:
        return (
            f"🔴 Sandy Squirrel is not available yet today.\n\n"
            f"Opens at {open_time.strftime('%H:%M')} today.\n\n"
            "🕐 Working hours:\n"
            "Mon-Fri: 9:00 - 15:00\n"
            "Sat-Sun: 9:00 - 13:00"
        )

    if current_time > close_time:
        return (
            "🔴 Sandy Squirrel is closed for today.\n\n"
            "🕐 Working hours:\n"
            "Mon-Fri: 9:00 - 15:00\n"
            "Sat-Sun: 9:00 - 13:00\n\n"
            "See you tomorrow! 🐿️"
        )

    return ""