"""
services/stats.py – In-memory statistics tracker (thread-safe via asyncio)
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class StatsTracker:
    start_time: float = field(default_factory=time.time)
    total_downloads: int = 0
    failed_downloads: int = 0
    active_users: set[int] = field(default_factory=set)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def record_success(self, user_id: int) -> None:
        async with self._lock:
            self.total_downloads += 1
            self.active_users.add(user_id)

    async def record_failure(self, user_id: int) -> None:
        async with self._lock:
            self.failed_downloads += 1
            self.active_users.add(user_id)

    async def get_snapshot(self) -> dict[str, object]:
        async with self._lock:
            uptime_s = int(time.time() - self.start_time)
            h, rem = divmod(uptime_s, 3600)
            m, s = divmod(rem, 60)
            return {
                "uptime": f"{h}h {m}m {s}s",
                "total_downloads": self.total_downloads,
                "failed_downloads": self.failed_downloads,
                "unique_users": len(self.active_users),
            }


# Singleton
stats = StatsTracker()
