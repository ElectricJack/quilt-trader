"""Tests for scraper catch-up + persistence (spec 2026-05-27-scraper-catchup).

Background: the alpha-picks scraper failed to fire for over a week because
each restart re-rolled the cron's jittered fire time and most restarts
landed after today's base time (14:00 PDT pre-fix), causing APScheduler to
roll the next fire to the next weekday. These tests pin the three pieces of
the fix: persistence so we know what ran when across restarts, catch-up so
we fire immediately when today's window was missed, and a 3-attempts/day
guardrail so a failing scraper doesn't burn the API forever.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select

from coordinator.database.connection import create_engine, create_session_factory
from coordinator.database.models import Base, Scraper
from coordinator.services.scraper_engine import ScraperResult
from coordinator.services.scraper_registry import ScraperRecord, ScraperRegistry


@pytest_asyncio.fixture
async def session_factory():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(engine)
    yield sf
    await engine.dispose()


class TestScraperPersistenceColumns:
    """The new columns must exist on the Scraper model so the registry can
    persist last_attempt_at and the daily attempts counter."""

    @pytest.mark.asyncio
    async def test_can_write_and_read_new_persistence_columns(self, session_factory):
        now = datetime.now(timezone.utc)
        today = now.date()
        async with session_factory() as session:
            session.add(Scraper(
                id=str(uuid4()),
                repo_url="local://test",
                name="dummy-scraper",
                status="stopped",
                last_attempt_at=now,
                attempts_today=2,
                attempts_day=today,
            ))
            await session.commit()

        async with session_factory() as session:
            row = (await session.execute(
                select(Scraper).where(Scraper.name == "dummy-scraper")
            )).scalar_one()
            assert row.last_attempt_at is not None
            assert row.attempts_today == 2
            assert row.attempts_day == today

    @pytest.mark.asyncio
    async def test_attempts_today_defaults_to_zero_on_insert(self, session_factory):
        async with session_factory() as session:
            session.add(Scraper(id=str(uuid4()), repo_url="x", name="y"))
            await session.commit()
        async with session_factory() as session:
            row = (await session.execute(
                select(Scraper).where(Scraper.name == "y")
            )).scalar_one()
            assert row.attempts_today == 0
            assert row.attempts_day is None


def _make_registry(session_factory, *, engine_result: ScraperResult):
    """Build a ScraperRegistry with a stubbed engine + scheduler.

    The registry's scheduler is only used by discover_and_register paths;
    these tests exercise run() directly so a MagicMock scheduler is fine.
    """
    engine = MagicMock()
    engine.run_scraper = MagicMock(return_value=engine_result)
    reg = ScraperRegistry(
        engine=engine,
        scheduler=MagicMock(),
        packages_dir="/nonexistent",
        configs_dir="/nonexistent",
        session_factory=session_factory,
    )
    return reg, engine


def _add_record(reg, name="dummy-scraper"):
    record = ScraperRecord(
        name=name,
        schedule="0 14 * * 1-5",
        manifest={"description": "test", "version": "1.0"},
        config={},
        jitter_seconds=None,
    )
    reg._scrapers[name] = record
    return record


class TestRunPersistence:
    """run() must persist attempts and success state to the scrapers table
    so the catch-up logic can decide on the next coordinator startup."""

    @pytest.mark.asyncio
    async def test_run_success_persists_last_success(self, session_factory):
        reg, _ = _make_registry(session_factory,
                                engine_result=ScraperResult(success=True,
                                                            output_path=None))
        _add_record(reg)

        await reg.run("dummy-scraper")

        async with session_factory() as session:
            row = (await session.execute(
                select(Scraper).where(Scraper.name == "dummy-scraper")
            )).scalar_one()
            assert row.last_success is not None
            assert row.last_attempt_at is not None
            assert row.attempts_today == 1
            assert row.attempts_day == datetime.now(timezone.utc).date()
            assert row.last_error is None

    @pytest.mark.asyncio
    async def test_run_failure_records_attempt_but_not_success(self, session_factory):
        reg, _ = _make_registry(session_factory,
                                engine_result=ScraperResult(success=False,
                                                            error="boom"))
        _add_record(reg)

        await reg.run("dummy-scraper")

        async with session_factory() as session:
            row = (await session.execute(
                select(Scraper).where(Scraper.name == "dummy-scraper")
            )).scalar_one()
            assert row.last_success is None
            assert row.attempts_today == 1
            assert row.last_error == "boom"

    @pytest.mark.asyncio
    async def test_attempts_today_resets_on_new_utc_day(self, session_factory):
        # Pre-seed a row with yesterday's attempts_day and a high count.
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        async with session_factory() as session:
            session.add(Scraper(
                id=str(uuid4()),
                repo_url="x",
                name="dummy-scraper",
                attempts_today=3,
                attempts_day=yesterday,
            ))
            await session.commit()

        reg, _ = _make_registry(session_factory,
                                engine_result=ScraperResult(success=True))
        _add_record(reg)

        await reg.run("dummy-scraper")

        async with session_factory() as session:
            row = (await session.execute(
                select(Scraper).where(Scraper.name == "dummy-scraper")
            )).scalar_one()
            # Yesterday's 3 attempts should not count toward today.
            assert row.attempts_today == 1
            assert row.attempts_day == datetime.now(timezone.utc).date()


class TestCatchUp:
    """_maybe_catch_up decides whether to fire a missed scrape now.

    The five gating conditions (spec section 3):
      1. attempts_today < 3
      2. today is a configured cron weekday
      3. now_utc >= today's base cron fire time
      4. last_success < today's base cron fire time
      5. record is registered
    All must hold for catch-up to fire.
    """

    @pytest.mark.asyncio
    async def test_eligible_when_after_base_time_and_no_success_today(self, session_factory):
        # Tuesday 16:00 UTC, cron base is 14:00 UTC → 2 hours late, no run today.
        reg, engine = _make_registry(session_factory,
                                     engine_result=ScraperResult(success=True))
        _add_record(reg)
        # Pre-seed an empty scrapers row so _maybe_catch_up has something to read.
        async with session_factory() as session:
            session.add(Scraper(id=str(uuid4()), repo_url="x", name="dummy-scraper"))
            await session.commit()

        # Simulate Tuesday 2026-06-02 16:00 UTC.
        tue_after = datetime(2026, 6, 2, 16, 0, tzinfo=timezone.utc)
        fired = await reg._maybe_catch_up("dummy-scraper", now_utc=tue_after)

        assert fired is True

    @pytest.mark.asyncio
    async def test_skipped_before_base_time(self, session_factory):
        reg, _ = _make_registry(session_factory,
                                engine_result=ScraperResult(success=True))
        _add_record(reg)
        async with session_factory() as session:
            session.add(Scraper(id=str(uuid4()), repo_url="x", name="dummy-scraper"))
            await session.commit()

        # 13:30 UTC, before the 14:00 UTC cron base → cron will handle it.
        tue_before = datetime(2026, 6, 2, 13, 30, tzinfo=timezone.utc)
        fired = await reg._maybe_catch_up("dummy-scraper", now_utc=tue_before)

        assert fired is False

    @pytest.mark.asyncio
    async def test_skipped_when_last_success_after_today_base_time(self, session_factory):
        reg, _ = _make_registry(session_factory,
                                engine_result=ScraperResult(success=True))
        _add_record(reg)
        # last_success is today 14:30 UTC — already ran today.
        ran_today = datetime(2026, 6, 2, 14, 30, tzinfo=timezone.utc)
        async with session_factory() as session:
            session.add(Scraper(
                id=str(uuid4()), repo_url="x", name="dummy-scraper",
                last_success=ran_today,
            ))
            await session.commit()

        tue_after = datetime(2026, 6, 2, 16, 0, tzinfo=timezone.utc)
        fired = await reg._maybe_catch_up("dummy-scraper", now_utc=tue_after)

        assert fired is False

    @pytest.mark.asyncio
    async def test_skipped_on_weekend(self, session_factory):
        reg, _ = _make_registry(session_factory,
                                engine_result=ScraperResult(success=True))
        _add_record(reg)
        async with session_factory() as session:
            session.add(Scraper(id=str(uuid4()), repo_url="x", name="dummy-scraper"))
            await session.commit()

        # 2026-06-06 is a Saturday; the cron is `0 14 * * 1-5`.
        sat = datetime(2026, 6, 6, 16, 0, tzinfo=timezone.utc)
        fired = await reg._maybe_catch_up("dummy-scraper", now_utc=sat)

        assert fired is False

    @pytest.mark.asyncio
    async def test_skipped_when_three_attempts_today(self, session_factory):
        reg, _ = _make_registry(session_factory,
                                engine_result=ScraperResult(success=False, error="boom"))
        _add_record(reg)
        today = datetime(2026, 6, 2, tzinfo=timezone.utc).date()
        async with session_factory() as session:
            session.add(Scraper(
                id=str(uuid4()), repo_url="x", name="dummy-scraper",
                attempts_today=3, attempts_day=today,
            ))
            await session.commit()

        tue_after = datetime(2026, 6, 2, 16, 0, tzinfo=timezone.utc)
        fired = await reg._maybe_catch_up("dummy-scraper", now_utc=tue_after)

        assert fired is False

    @pytest.mark.asyncio
    async def test_attempts_from_a_prior_day_do_not_block_today(self, session_factory):
        # attempts_today=3 but attempts_day is yesterday → today is fresh.
        reg, _ = _make_registry(session_factory,
                                engine_result=ScraperResult(success=True))
        _add_record(reg)
        yesterday = datetime(2026, 6, 1, tzinfo=timezone.utc).date()
        async with session_factory() as session:
            session.add(Scraper(
                id=str(uuid4()), repo_url="x", name="dummy-scraper",
                attempts_today=3, attempts_day=yesterday,
            ))
            await session.commit()

        tue_after = datetime(2026, 6, 2, 16, 0, tzinfo=timezone.utc)
        fired = await reg._maybe_catch_up("dummy-scraper", now_utc=tue_after)

        assert fired is True


class TestPersistentState:
    """The API surface reads last_run_at / last_status / last_error /
    attempts_today from the DB so they survive coordinator restarts."""

    @pytest.mark.asyncio
    async def test_returns_ok_when_last_attempt_succeeded(self, session_factory):
        reg, _ = _make_registry(session_factory,
                                engine_result=ScraperResult(success=True))
        _add_record(reg)
        now = datetime.now(timezone.utc)
        async with session_factory() as session:
            session.add(Scraper(
                id=str(uuid4()), repo_url="x", name="dummy-scraper",
                last_success=now, last_attempt_at=now,
                attempts_today=1, attempts_day=now.date(),
            ))
            await session.commit()

        state = await reg.get_persistent_state("dummy-scraper")
        assert state["last_status"] == "ok"
        assert state["last_run_at"] is not None
        assert state["last_run_at"].endswith("+00:00") or state["last_run_at"].endswith("Z")
        assert state["attempts_today"] == 1
        assert state["last_error"] is None

    @pytest.mark.asyncio
    async def test_returns_failed_when_last_attempt_was_a_failure(self, session_factory):
        reg, _ = _make_registry(session_factory,
                                engine_result=ScraperResult(success=False))
        _add_record(reg)
        success_ts = datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)
        fail_ts = datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc)
        async with session_factory() as session:
            session.add(Scraper(
                id=str(uuid4()), repo_url="x", name="dummy-scraper",
                last_success=success_ts, last_attempt_at=fail_ts,
                attempts_today=1, attempts_day=fail_ts.date(),
                last_error="boom",
            ))
            await session.commit()

        state = await reg.get_persistent_state("dummy-scraper")
        assert state["last_status"] == "failed"
        assert state["last_error"] == "boom"

    @pytest.mark.asyncio
    async def test_attempts_today_zero_when_attempts_day_is_stale(self, session_factory):
        reg, _ = _make_registry(session_factory,
                                engine_result=ScraperResult(success=True))
        _add_record(reg)
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        async with session_factory() as session:
            session.add(Scraper(
                id=str(uuid4()), repo_url="x", name="dummy-scraper",
                attempts_today=3, attempts_day=yesterday,
            ))
            await session.commit()
        state = await reg.get_persistent_state("dummy-scraper")
        assert state["attempts_today"] == 0


class TestRunConcurrencyGuard:
    """A second run() while the first is in flight must short-circuit so
    catch-up cannot race a cron fire and double-invoke the engine."""

    @pytest.mark.asyncio
    async def test_run_skipped_when_record_already_running(self, session_factory):
        reg, engine = _make_registry(session_factory,
                                     engine_result=ScraperResult(success=True))
        record = _add_record(reg)
        record.last_status = "running"

        result = await reg.run("dummy-scraper")

        assert result.success is False
        assert "already running" in (result.error or "").lower()
        engine.run_scraper.assert_not_called()
