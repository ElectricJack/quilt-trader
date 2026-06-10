"""In-memory snapshot cache with stale-while-revalidate semantics.

A CachedSnapshot wraps an async producer that computes some response payload.
Readers call get() and receive the most recently computed value once the first
refresh has succeeded; subsequent invalidations refresh in the background
without blocking readers. Repeated invalidations during a single in-flight
refresh are coalesced to at most one queued follow-up refresh.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Generic, Optional, TypeVar, cast

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CachedSnapshot(Generic[T]):
    def __init__(self, name: str, producer: Callable[[], Awaitable[T]]) -> None:
        self._name = name
        self._producer = producer
        self._value: Optional[T] = None
        self._ready = asyncio.Event()
        self._refresh_pending = False
        self._refresh_task: Optional[asyncio.Task[None]] = None

    async def get(self) -> T:
        """Return the latest cached value, blocking only on the first-ever refresh."""
        await self._ready.wait()
        return cast(T, self._value)

    async def refresh_now(self) -> None:
        """Run the producer once, awaited by the caller. Raises on producer failure."""
        start = time.perf_counter()
        value = await self._producer()
        self._value = value
        self._ready.set()
        logger.info(
            "snapshot %s refreshed in %.1fs", self._name, time.perf_counter() - start
        )

    def invalidate(self) -> None:
        """Mark stale and ensure exactly one drainer task is running. Non-blocking."""
        self._refresh_pending = True
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        while self._refresh_pending:
            self._refresh_pending = False  # consume flag BEFORE producing
            start = time.perf_counter()
            try:
                value = await self._producer()
            except Exception:
                logger.exception("snapshot %s refresh failed", self._name)
                logger.info(
                    "snapshot %s drainer exiting after failure", self._name
                )
                return
            self._value = value
            self._ready.set()
            logger.info(
                "snapshot %s refreshed in %.1fs",
                self._name,
                time.perf_counter() - start,
            )
