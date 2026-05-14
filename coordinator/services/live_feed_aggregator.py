"""Live broker WebSocket -> ticks parquet + 1min bars.

This Phase-2 implementation lays the structure (per-subscription task,
retention sweeper) but the broker stream itself is a stub. Phase 4
wires real BrokerAdapter streams in once those exist.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from coordinator.database.models import LiveSubscription

logger = logging.getLogger(__name__)


class LiveFeedAggregator:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._retention_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        # Resume any rows already marked as running.
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(LiveSubscription).where(LiveSubscription.status == "running")
                )
            ).scalars().all()
            for r in rows:
                await self.start_subscription(r.broker, r.symbol)
        self._retention_task = asyncio.create_task(self._retention_loop())

    async def stop(self) -> None:
        if self._retention_task:
            self._retention_task.cancel()
        for t in list(self._tasks.values()):
            t.cancel()

    async def start_subscription(self, broker: str, symbol: str) -> None:
        key = (broker, symbol)
        if key in self._tasks:
            return
        self._tasks[key] = asyncio.create_task(self._run(broker, symbol))

    async def stop_subscription(self, broker: str, symbol: str) -> None:
        t = self._tasks.pop((broker, symbol), None)
        if t:
            t.cancel()

    async def _run(self, broker: str, symbol: str) -> None:
        # Phase 4 wires real broker streams. For now: mark running, idle.
        logger.info("[stub] LiveFeedAggregator running for %s/%s", broker, symbol)
        try:
            while True:
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            return

    async def _retention_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(3600)
                await self._sweep_old_ticks()
        except asyncio.CancelledError:
            return

    async def _sweep_old_ticks(self) -> None:
        # Walks data/market/{broker}_live/{symbol}/ticks/ and removes files
        # whose date is older than retention. Spec B §3.
        async with self._sf() as session:
            rows = (
                await session.execute(select(LiveSubscription))
            ).scalars().all()
        for sub in rows:
            ticks_dir = (
                Path("data/market") / f"{sub.broker}_live" / sub.symbol / "ticks"
            )
            if not ticks_dir.exists():
                continue
            cutoff = (
                datetime.now(timezone.utc) - timedelta(hours=sub.tick_retention_hours)
            ).date()
            for f in ticks_dir.glob("*.parquet"):
                try:
                    name = f.stem  # e.g. "trades-2026-05-14"
                    d = date.fromisoformat(name.split("-", 1)[1])
                    if d < cutoff:
                        f.unlink()
                except (ValueError, OSError):
                    continue
