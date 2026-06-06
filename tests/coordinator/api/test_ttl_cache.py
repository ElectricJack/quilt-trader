"""Tests for the per-process TTL cache used by read-mostly routes."""
from __future__ import annotations

import asyncio

import pytest

from coordinator.api._ttl_cache import TTLCache


@pytest.mark.asyncio
async def test_caches_within_ttl():
    cache = TTLCache(ttl_seconds=60.0)
    calls = 0

    async def produce() -> int:
        nonlocal calls
        calls += 1
        return 42

    v1 = await cache.get("k", produce)
    v2 = await cache.get("k", produce)
    assert v1 == 42 and v2 == 42
    assert calls == 1


@pytest.mark.asyncio
async def test_refetches_after_ttl():
    cache = TTLCache(ttl_seconds=0.05)
    calls = 0

    async def produce() -> int:
        nonlocal calls
        calls += 1
        return calls

    assert await cache.get("k", produce) == 1
    await asyncio.sleep(0.1)
    assert await cache.get("k", produce) == 2


@pytest.mark.asyncio
async def test_coalesces_concurrent_misses():
    """Thundering-herd guard: N concurrent misses must hit producer once."""
    cache = TTLCache(ttl_seconds=60.0)
    calls = 0
    gate = asyncio.Event()

    async def produce() -> int:
        nonlocal calls
        calls += 1
        await gate.wait()
        return 7

    waiters = [asyncio.create_task(cache.get("k", produce)) for _ in range(10)]
    await asyncio.sleep(0.01)  # let them all queue
    gate.set()
    results = await asyncio.gather(*waiters)
    assert results == [7] * 10
    assert calls == 1


@pytest.mark.asyncio
async def test_invalidate_forces_refetch():
    cache = TTLCache(ttl_seconds=60.0)
    calls = 0

    async def produce() -> int:
        nonlocal calls
        calls += 1
        return calls

    assert await cache.get("k", produce) == 1
    cache.invalidate("k")
    assert await cache.get("k", produce) == 2
    cache.invalidate()  # clear all
    assert await cache.get("k", produce) == 3
