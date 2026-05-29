"""Unit tests for QuotaTracker — DB-backed daily counter + 429 escalation."""
import asyncio
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from coordinator.services.datasets.quota import QuotaTracker, QuotaExhausted
from coordinator.database.models import QuotaUsage


@pytest_asyncio.fixture
async def tracker(db_session_factory):
    return QuotaTracker(db_session_factory, reset_tz=timezone.utc)


@pytest.mark.asyncio
async def test_acquire_increments_counter(tracker, db_session_factory):
    """acquire() writes calls_used=1 to the DB."""
    await tracker.acquire("fmp", daily_limit=3)
    async with db_session_factory() as s:
        row = (
            await s.execute(select(QuotaUsage).where(QuotaUsage.provider == "fmp"))
        ).scalar_one()
        assert row.calls_used == 1


@pytest.mark.asyncio
async def test_acquire_raises_at_limit(tracker):
    """acquire() raises QuotaExhausted once calls_used reaches daily_limit."""
    for _ in range(3):
        await tracker.acquire("fmp", daily_limit=3)
    with pytest.raises(QuotaExhausted):
        await tracker.acquire("fmp", daily_limit=3)


@pytest.mark.asyncio
async def test_mark_exhausted_blocks_further_acquire(tracker):
    """mark_exhausted() sets the flag; subsequent acquire() raises immediately."""
    await tracker.acquire("fmp", daily_limit=100)
    await tracker.mark_exhausted("fmp")
    with pytest.raises(QuotaExhausted):
        await tracker.acquire("fmp", daily_limit=100)


@pytest.mark.asyncio
async def test_remaining_reflects_count_and_flag(tracker):
    """remaining() returns limit-used, and 0 once exhausted flag is set."""
    await tracker.acquire("fmp", daily_limit=10)
    assert await tracker.remaining("fmp", daily_limit=10) == 9
    await tracker.mark_exhausted("fmp")
    assert await tracker.remaining("fmp", daily_limit=10) == 0


@pytest.mark.asyncio
async def test_concurrent_acquires_never_overshoot(tracker):
    """100 concurrent acquires against limit=10 succeed exactly 10 times."""

    async def one():
        try:
            await tracker.acquire("fmp", daily_limit=10)
            return True
        except QuotaExhausted:
            return False

    results = await asyncio.gather(*[one() for _ in range(100)])
    assert sum(results) == 10


@pytest.mark.asyncio
async def test_new_window_creates_fresh_counter(db_session_factory):
    """A row from yesterday does not block today's acquire — new window, new row."""
    tracker = QuotaTracker(db_session_factory, reset_tz=timezone.utc)
    # Manually insert a "yesterday" row that is already at-limit.
    async with db_session_factory() as s:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        s.add(
            QuotaUsage(
                provider="fmp",
                reset_window=yesterday,
                calls_used=10,
                daily_limit=10,
            )
        )
        await s.commit()
    # Today's acquire should succeed (new row for today's window).
    await tracker.acquire("fmp", daily_limit=10)
    async with db_session_factory() as s:
        rows = (
            await s.execute(
                select(QuotaUsage).where(QuotaUsage.provider == "fmp")
            )
        ).scalars().all()
        assert len(rows) == 2
