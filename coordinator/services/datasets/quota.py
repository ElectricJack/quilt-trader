from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, date, tzinfo, timezone

from sqlalchemy import select

from coordinator.database.models import QuotaUsage


class QuotaExhausted(Exception):
    def __init__(self, provider: str, used: int, limit: int):
        super().__init__(f"{provider} quota exhausted: {used}/{limit}")
        self.provider = provider
        self.used = used
        self.limit = limit


class QuotaTracker:
    def __init__(self, session_factory, reset_tz: tzinfo = timezone.utc):
        self._sf = session_factory
        self._tz = reset_tz
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _current_window(self) -> date:
        return datetime.now(self._tz).date()

    async def _get_or_create(
        self, session, provider: str, daily_limit: int
    ) -> QuotaUsage:
        window = self._current_window()
        row = (
            await session.execute(
                select(QuotaUsage).where(
                    QuotaUsage.provider == provider,
                    QuotaUsage.reset_window == window,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = QuotaUsage(
                provider=provider,
                reset_window=window,
                calls_used=0,
                daily_limit=daily_limit,
                exhausted=False,
            )
            session.add(row)
            await session.flush()  # populate id
        return row

    async def acquire(self, provider: str, daily_limit: int) -> None:
        async with self._locks[provider]:
            async with self._sf() as session:
                row = await self._get_or_create(session, provider, daily_limit)
                if row.exhausted or row.calls_used >= row.daily_limit:
                    raise QuotaExhausted(provider, row.calls_used, row.daily_limit)
                row.calls_used += 1
                await session.commit()

    async def mark_exhausted(self, provider: str) -> None:
        async with self._locks[provider]:
            async with self._sf() as session:
                row = await self._get_or_create(session, provider, daily_limit=0)
                row.exhausted = True
                await session.commit()

    async def remaining(self, provider: str, daily_limit: int) -> int:
        async with self._sf() as session:
            row = await self._get_or_create(session, provider, daily_limit)
            await session.commit()
            if row.exhausted:
                return 0
            return max(0, row.daily_limit - row.calls_used)
