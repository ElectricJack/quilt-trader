"""Periodic database jobs finish their active sweep during coordinator shutdown."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from coordinator.services import archival, worker_health
from coordinator.services.live_feed_aggregator import LiveFeedAggregator, _SubState
from coordinator.services.live_finalizer import LiveFinalizer


@pytest.mark.asyncio
@pytest.mark.parametrize("job", ["worker_health", "activity_retention", "live_finalizer"])
async def test_periodic_job_stops_after_active_sweep(monkeypatch, job):
    started = asyncio.Event()
    release = asyncio.Event()
    stop = asyncio.Event()
    calls = 0

    async def blocked_sweep(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return [] if job == "worker_health" else 0

    if job == "worker_health":
        monkeypatch.setattr(worker_health, "sweep_stale_workers", blocked_sweep)
        coroutine = worker_health.run_worker_health_loop(
            None, interval_seconds=3600, stop_event=stop
        )
    elif job == "activity_retention":
        monkeypatch.setattr(archival, "prune_worker_activity", blocked_sweep)
        coroutine = archival.run_worker_activity_retention_loop(
            None, interval_seconds=3600, stop_event=stop
        )
    else:
        finalizer = LiveFinalizer(None, None, Path("."), interval_seconds=3600)
        monkeypatch.setattr(finalizer, "_tick", blocked_sweep)
        coroutine = finalizer.run_loop(stop)

    task = asyncio.create_task(coroutine)
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        stop.set()
        await asyncio.sleep(0)
        assert not task.done(), "shutdown interrupted the active sweep"
        release.set()
        await asyncio.wait_for(task, timeout=1)
        assert calls == 1
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_aggregator_stop_waits_for_active_rate_update(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    clock_calls = 0

    def clock():
        nonlocal clock_calls
        clock_calls += 1
        return now if clock_calls == 1 else now + timedelta(seconds=61)

    async def flush_ticks(*_args):
        pass

    async def update_rate(*_args):
        started.set()
        await release.wait()

    aggregator = LiveFeedAggregator(
        None, encryption=None, flush_interval_s=0.001, now_fn=clock
    )
    aggregator._states[("broker", "SPY")] = _SubState()
    monkeypatch.setattr(aggregator, "_flush_ticks", flush_ticks)
    monkeypatch.setattr(aggregator, "_update_rate", update_rate)
    aggregator._tasks[("broker", "SPY")] = asyncio.create_task(
        aggregator._run("broker", "SPY")
    )
    stop_task = None
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        stop_task = asyncio.create_task(aggregator.stop())
        await asyncio.sleep(0)
        assert not stop_task.done(), "shutdown interrupted the rate update"
        release.set()
        await asyncio.wait_for(stop_task, timeout=1)
        assert not aggregator._tasks
    finally:
        release.set()
        if stop_task is not None and not stop_task.done():
            stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)
        for task in aggregator._tasks.values():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
