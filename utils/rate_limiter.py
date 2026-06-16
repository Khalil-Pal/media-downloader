"""
utils/rate_limiter.py – Per-user rate limiting and cooldown tracking
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from config.settings import settings


class RateLimiter:
    """
    Sliding-window rate limiter with per-user cooldown.

    * rate_limit_max downloads per rate_limit_window seconds
    * cooldown_seconds minimum gap between any two requests
    """

    def __init__(self) -> None:
        # user_id → deque of timestamps within the current window
        self._windows: dict[int, deque[float]] = defaultdict(deque)
        # user_id → timestamp of last request
        self._last_request: dict[int, float] = {}
        self._lock = asyncio.Lock()

    async def check(self, user_id: int) -> tuple[bool, str]:
        """
        Return (allowed, reason).
        *allowed* is True when the user may proceed.
        """
        async with self._lock:
            now = time.monotonic()

            # Cooldown check
            last = self._last_request.get(user_id)
            if last is not None:
                elapsed = now - last
                remaining = settings.cooldown_seconds - elapsed
                if remaining > 0:
                    return False, f"⏳ Please wait {remaining:.1f}s before sending another request."

            # Sliding-window check
            window = self._windows[user_id]
            cutoff = now - settings.rate_limit_window
            while window and window[0] < cutoff:
                window.popleft()

            if len(window) >= settings.rate_limit_max:
                reset_in = int(window[0] - cutoff) + 1
                return (
                    False,
                    f"🚫 Rate limit reached ({settings.rate_limit_max} downloads "
                    f"per {settings.rate_limit_window}s). "
                    f"Try again in {reset_in}s.",
                )

            # Record this request
            window.append(now)
            self._last_request[user_id] = now
            return True, ""


# Singleton
rate_limiter = RateLimiter()
