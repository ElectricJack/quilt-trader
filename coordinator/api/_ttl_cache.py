"""Tiny per-process async TTL cache for read-mostly endpoints.

Use for endpoints whose data changes slowly relative to dashboard refresh
rates (git status, filesystem-level summaries). Coalesces concurrent calls
on the same key onto a single in-flight task so a thundering herd doesn't
trigger N upstream calls.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Hashable


class TTLCache:
    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._values: dict[Hashable, tuple[float, Any]] = {}
        self._inflight: dict[Hashable, asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: Hashable, producer: Callable[[], Awaitable[Any]]) -> Any:
        now = time.monotonic()
        entry = self._values.get(key)
        if entry is not None and (now - entry[0]) < self._ttl:
            return entry[1]
        # Coalesce concurrent misses on the same key.
        async with self._lock:
            entry = self._values.get(key)
            if entry is not None and (time.monotonic() - entry[0]) < self._ttl:
                return entry[1]
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(producer())
                self._inflight[key] = task
        try:
            value = await task
        finally:
            async with self._lock:
                self._inflight.pop(key, None)
        self._values[key] = (time.monotonic(), value)
        return value

    def invalidate(self, key: Hashable | None = None) -> None:
        if key is None:
            self._values.clear()
        else:
            self._values.pop(key, None)
