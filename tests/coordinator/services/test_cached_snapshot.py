import asyncio
import pytest

from coordinator.services.cached_snapshot import CachedSnapshot


@pytest.mark.asyncio
async def test_get_blocks_until_first_refresh():
    """get() must wait for the first successful refresh before returning."""
    async def producer() -> str:
        return "value-1"

    snap = CachedSnapshot[str]("test", producer)

    # Before refresh: get() hangs.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(snap.get(), timeout=0.05)

    # After refresh: get() returns immediately.
    await snap.refresh_now()
    assert await asyncio.wait_for(snap.get(), timeout=0.5) == "value-1"


@pytest.mark.asyncio
async def test_get_returns_latest_after_refresh():
    """Successive refreshes replace the cached value."""
    counter = {"n": 0}

    async def producer() -> int:
        counter["n"] += 1
        return counter["n"]

    snap = CachedSnapshot[int]("test", producer)
    await snap.refresh_now()
    assert await snap.get() == 1

    await snap.refresh_now()
    assert await snap.get() == 2


@pytest.mark.asyncio
async def test_stale_while_revalidate():
    """While a slow refresh is in flight, get() returns the previous value."""
    release = asyncio.Event()
    call_count = {"n": 0}

    async def producer() -> int:
        call_count["n"] += 1
        if call_count["n"] >= 2:
            await release.wait()  # block subsequent refreshes
        return call_count["n"]

    snap = CachedSnapshot[int]("test", producer)
    await snap.refresh_now()  # value = 1
    assert await snap.get() == 1

    # Trigger an invalidate that will block in the producer.
    snap.invalidate()

    # Give the drainer a chance to start the producer.
    await asyncio.sleep(0.01)

    # Reader still sees the OLD value.
    assert await asyncio.wait_for(snap.get(), timeout=0.05) == 1

    # Let the refresh finish and verify the new value.
    release.set()
    if snap._refresh_task is not None:
        await snap._refresh_task
    assert await snap.get() == 2


@pytest.mark.asyncio
async def test_invalidate_coalesces_concurrent_calls():
    """N rapid invalidations during one in-flight refresh produce at most
    one queued refresh after it, not N."""
    call_count = {"n": 0}
    started = asyncio.Event()
    release = asyncio.Event()

    async def producer() -> int:
        call_count["n"] += 1
        started.set()
        await release.wait()
        return call_count["n"]

    snap = CachedSnapshot[int]("test", producer)

    # Start the first refresh; producer blocks on `release`.
    snap.invalidate()
    await started.wait()
    started.clear()

    # Fire 10 rapid invalidations while the producer is blocked.
    for _ in range(10):
        snap.invalidate()

    # Release the producer; release stays set so the next call returns instantly.
    release.set()

    # The drainer loops once more (queued by the burst above).
    await started.wait()

    # Wait for the drainer task to fully exit.
    assert snap._refresh_task is not None
    await snap._refresh_task

    # Exactly two producer invocations: the initial + one coalesced.
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_producer_exception_keeps_old_value():
    """If a refresh raises, the previously-cached value remains readable."""
    call_count = {"n": 0}

    async def producer() -> str:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("transient")
        return f"v{call_count['n']}"

    snap = CachedSnapshot[str]("test", producer)
    await snap.refresh_now()  # v1
    assert await snap.get() == "v1"

    # Trigger a refresh that will raise.
    snap.invalidate()
    assert snap._refresh_task is not None
    await snap._refresh_task

    # Old value is still served.
    assert await snap.get() == "v1"


@pytest.mark.asyncio
async def test_drainer_exits_after_failure_and_resumes_on_invalidate():
    """Failed drainer exits; the next invalidate() starts a fresh drainer."""
    call_count = {"n": 0}
    should_fail = {"yes": True}

    async def producer() -> str:
        call_count["n"] += 1
        if should_fail["yes"]:
            raise RuntimeError("boom")
        return f"v{call_count['n']}"

    snap = CachedSnapshot[str]("test", producer)
    should_fail["yes"] = False
    await snap.refresh_now()  # v1
    should_fail["yes"] = True

    snap.invalidate()
    assert snap._refresh_task is not None
    await snap._refresh_task  # drainer exits after the failure

    # Fix the producer and try again.
    should_fail["yes"] = False
    snap.invalidate()
    assert snap._refresh_task is not None
    await snap._refresh_task

    assert await snap.get() == "v3"


@pytest.mark.asyncio
async def test_first_refresh_failure_blocks_readers():
    """If the first-ever refresh fails, readers stay blocked (no _ready set).
    Documents the boot-time hang semantics that the route-level wait_for mitigates."""
    async def failing_producer() -> str:
        raise RuntimeError("boom")

    snap = CachedSnapshot[str]("test", failing_producer)

    with pytest.raises(RuntimeError):
        await snap.refresh_now()

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(snap.get(), timeout=0.05)
