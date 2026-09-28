"""Background task that marks workers offline when their heartbeat goes stale."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from coordinator.database.models import Worker
from coordinator.services.periodic import wait_for_next_tick

logger = logging.getLogger(__name__)


async def sweep_stale_workers(
    session_factory: async_sessionmaker[AsyncSession],
    offline_after_seconds: int,
) -> list[str]:
    """Mark any 'online' worker whose heartbeat is older than the threshold offline.

    Returns the list of worker ids that were transitioned.
    """
    threshold = datetime.now(timezone.utc) - timedelta(seconds=offline_after_seconds)
    transitioned: list[str] = []
    async with session_factory() as session:
        result = await session.execute(
            select(Worker).where(
                Worker.status == "online",
                Worker.last_heartbeat < threshold,
            )
        )
        for worker in result.scalars().all():
            worker.status = "offline"
            transitioned.append(worker.id)
        if transitioned:
            await session.commit()
    return transitioned


async def run_worker_health_loop(
    session_factory: async_sessionmaker[AsyncSession],
    interval_seconds: int = 30,
    offline_after_seconds: int = 60,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run the sweeper until stopped, finishing the current database operation."""
    while stop_event is None or not stop_event.is_set():
        try:
            transitioned = await sweep_stale_workers(
                session_factory, offline_after_seconds
            )
            for wid in transitioned:
                logger.info("Marked stale worker %s offline", wid)
        except Exception:
            logger.exception("Worker health sweep failed")
        await wait_for_next_tick(interval_seconds, stop_event)
