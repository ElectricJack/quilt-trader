"""Scraper auth state and back-off (review rev-nimble-bridge, sections 6.3-6.5).

A run that ends in a typed auth error (auth_required / bot_blocked) marks the
scraper needs_login: one warning Event, a dashboard broadcast, and scheduled
and catch-up runs paused (no process, no attempt counted) until a login is
verified or any run succeeds. Manual runs still go ahead. The state lives in
the scrapers table, so it survives a coordinator restart.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from coordinator.database.models import Event, Scraper
from coordinator.services.scraper_engine import ScraperResult
from coordinator.services.scraper_registry import (
    AUTH_MESSAGE_MAX_CHARS,
    ScrapeRunning,
    ScraperRecord,
    ScraperRegistry,
)
from sdk.scraper_auth import ScraperAuth, ScraperAuthVerify

NAME = "alpha-picks-scraper"
OK = ScraperResult(success=True, output_path=None)
AUTH_REQUIRED = ScraperResult(
    success=False, error="login wall at /alpha-picks", error_kind="auth_required",
)
BOT_BLOCKED = ScraperResult(
    success=False, error="PerimeterX 403", error_kind="bot_blocked",
)
PROFILE_BUSY = ScraperResult(
    success=False,
    error="profile /home/me/.cache/p is in use by login session, pid 1234, since 14:02 UTC",
    error_kind="profile_busy",
)
PLAIN_ERROR = ScraperResult(success=False, error="Traceback ... KeyError", error_kind="error")


def _make_registry(session_factory, *results: ScraperResult, broadcast=None):
    """A registry whose engine returns `results` in order, one per spawned run."""
    pending = list(results) or [OK]

    def run_scraper(name, fmt, config):
        # An unexpected extra spawn must fail the test, not hang it: a
        # StopIteration from an exhausted side_effect list can't cross
        # asyncio.to_thread and leaves the run awaiting forever.
        assert pending, "engine.run_scraper called more often than the test expects"
        return pending.pop(0)

    engine = MagicMock()
    engine.run_scraper = MagicMock(side_effect=run_scraper)
    reg = ScraperRegistry(
        engine=engine,
        scheduler=MagicMock(),
        packages_dir="/nonexistent",
        configs_dir="/nonexistent",
        session_factory=session_factory,
        broadcast=broadcast,
    )
    reg._scrapers[NAME] = ScraperRecord(
        name=NAME,
        schedule="0 14 * * 1-5",
        manifest={"description": "test", "version": "1.0"},
    )
    return reg, engine


async def _row(session_factory) -> Scraper:
    async with session_factory() as session:
        return (await session.execute(
            select(Scraper).where(Scraper.name == NAME)
        )).scalar_one()


async def _events(session_factory) -> list[Event]:
    async with session_factory() as session:
        return list((await session.execute(
            select(Event).where(Event.source_type == "scraper").order_by(Event.timestamp)
        )).scalars())


async def _seed_needs_login(session_factory, *, attempts_today: int = 1) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        session.add(Scraper(
            id=str(uuid4()), repo_url="local", name=NAME,
            attempts_today=attempts_today, attempts_day=now.date(), last_attempt_at=now,
            auth_state="needs_login", auth_reason="bot_blocked",
            auth_message="PerimeterX 403", auth_changed_at=now,
        ))
        await session.commit()


class TestNeedsLoginTransition:
    @pytest.mark.parametrize("result", [AUTH_REQUIRED, BOT_BLOCKED], ids=lambda r: r.error_kind)
    @pytest.mark.parametrize("trigger", ["schedule", "catch_up", "manual"])
    @pytest.mark.asyncio
    async def test_auth_error_marks_needs_login_with_one_event(
        self, db_session_factory, result, trigger,
    ):
        broadcast = AsyncMock()
        reg, _ = _make_registry(db_session_factory, result, broadcast=broadcast)

        out = await reg.run(NAME, trigger=trigger)

        assert out.error_kind == result.error_kind
        row = await _row(db_session_factory)
        assert row.auth_state == "needs_login"
        assert row.auth_reason == result.error_kind
        assert row.auth_message == result.error
        assert row.auth_changed_at is not None
        events = await _events(db_session_factory)
        assert len(events) == 1
        ev = events[0]
        assert (ev.source_id, ev.event_type, ev.severity) == (
            NAME, "scraper_needs_login", "warning",
        )
        assert ev.payload == {"reason": result.error_kind, "message": result.error}
        broadcast.assert_awaited_once_with(
            {"type": "scraper_auth_changed", "name": NAME, "auth_state": "needs_login"}
        )

    @pytest.mark.asyncio
    async def test_repeat_auth_failure_updates_message_without_new_event(self, db_session_factory):
        broadcast = AsyncMock()
        second = ScraperResult(success=False, error="still walled", error_kind="auth_required")
        reg, _ = _make_registry(db_session_factory, AUTH_REQUIRED, second, broadcast=broadcast)

        await reg.run(NAME)
        first_changed = (await _row(db_session_factory)).auth_changed_at
        await reg.run(NAME)

        row = await _row(db_session_factory)
        assert row.auth_state == "needs_login"
        assert row.auth_reason == "auth_required"
        assert row.auth_message == "still walled"
        assert row.auth_changed_at >= first_changed
        assert len(await _events(db_session_factory)) == 1
        assert broadcast.await_count == 1

    @pytest.mark.asyncio
    async def test_auth_message_is_truncated(self, db_session_factory):
        long = ScraperResult(success=False, error="x" * 2000, error_kind="auth_required")
        reg, _ = _make_registry(db_session_factory, long)

        await reg.run(NAME)

        row = await _row(db_session_factory)
        assert row.auth_message == "x" * AUTH_MESSAGE_MAX_CHARS
        # last_error keeps the full message, as before.
        assert row.last_error == "x" * 2000

    @pytest.mark.parametrize("result", [PLAIN_ERROR, PROFILE_BUSY], ids=lambda r: r.error_kind)
    @pytest.mark.asyncio
    async def test_other_failures_leave_ok_alone(self, db_session_factory, result):
        broadcast = AsyncMock()
        reg, _ = _make_registry(db_session_factory, result, broadcast=broadcast)

        await reg.run(NAME)

        row = await _row(db_session_factory)
        assert row.auth_state is None
        assert await _events(db_session_factory) == []
        broadcast.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_profile_busy_names_the_holder_and_keeps_needs_login(self, db_session_factory):
        await _seed_needs_login(db_session_factory)
        broadcast = AsyncMock()
        reg, _ = _make_registry(db_session_factory, PROFILE_BUSY, broadcast=broadcast)

        await reg.run(NAME)

        row = await _row(db_session_factory)
        assert row.last_error == PROFILE_BUSY.error
        assert "login session, pid 1234" in reg.get(NAME).last_error
        assert (row.auth_state, row.auth_reason, row.auth_message) == (
            "needs_login", "bot_blocked", "PerimeterX 403",
        )
        assert await _events(db_session_factory) == []
        broadcast.assert_not_awaited()


class TestBackOff:
    @pytest.mark.parametrize("trigger", ["schedule", "catch_up"])
    @pytest.mark.asyncio
    async def test_paused_trigger_skips_without_spawning_or_counting(
        self, db_session_factory, trigger, caplog,
    ):
        await _seed_needs_login(db_session_factory, attempts_today=1)
        before = await _row(db_session_factory)
        reg, engine = _make_registry(db_session_factory)
        record = reg.get(NAME)
        record.last_status = "failed"
        record.last_run_at = "2026-09-28T14:00:00+00:00"

        with caplog.at_level("INFO", logger="coordinator.services.scraper_registry"):
            result = await reg.run(NAME, trigger=trigger)

        assert result.success is False
        assert result.error_kind == "paused"
        engine.run_scraper.assert_not_called()
        after = await _row(db_session_factory)
        assert after.attempts_today == before.attempts_today == 1
        assert after.last_attempt_at == before.last_attempt_at
        # The slot claimed before the auth read is handed back untouched.
        assert record.last_status == "failed"
        assert record.last_run_at == "2026-09-28T14:00:00+00:00"
        skips = [r for r in caplog.records if "needs login" in r.getMessage()]
        assert len(skips) == 1 and skips[0].levelname == "INFO"

    @pytest.mark.asyncio
    async def test_manual_run_still_runs_and_success_clears_needs_login(self, db_session_factory):
        await _seed_needs_login(db_session_factory)
        broadcast = AsyncMock()
        reg, engine = _make_registry(db_session_factory, OK, OK, broadcast=broadcast)

        result = await reg.run(NAME, trigger="manual")

        assert result.success is True
        engine.run_scraper.assert_called_once()
        row = await _row(db_session_factory)
        assert (row.auth_state, row.auth_reason, row.auth_message) == (None, None, None)
        assert row.auth_changed_at is not None
        assert row.attempts_today == 2
        events = await _events(db_session_factory)
        assert [(e.event_type, e.severity) for e in events] == [
            ("scraper_login_restored", "info"),
        ]
        assert events[0].payload == {"via": "manual"}
        broadcast.assert_awaited_once_with(
            {"type": "scraper_auth_changed", "name": NAME, "auth_state": "ok"}
        )

        # Scheduling resumes once the state is back to ok.
        assert (await reg.run(NAME, trigger="schedule")).success is True
        assert engine.run_scraper.call_count == 2
        assert len(await _events(db_session_factory)) == 1

    @pytest.mark.asyncio
    async def test_manual_run_with_ordinary_error_stays_paused(self, db_session_factory):
        await _seed_needs_login(db_session_factory)
        reg, engine = _make_registry(db_session_factory, PLAIN_ERROR)

        await reg.run(NAME, trigger="manual")

        assert (await _row(db_session_factory)).auth_state == "needs_login"
        assert (await reg.run(NAME, trigger="schedule")).error_kind == "paused"
        assert engine.run_scraper.call_count == 1

    @pytest.mark.asyncio
    async def test_state_survives_a_new_registry_instance(self, db_session_factory):
        reg, _ = _make_registry(db_session_factory, BOT_BLOCKED)
        await reg.run(NAME, trigger="schedule")

        # A coordinator restart: a fresh registry over the same database.
        fresh, engine = _make_registry(db_session_factory)
        auth = await fresh.get_auth_state(NAME)
        assert auth.needs_login and auth.reason == "bot_blocked"
        assert (await fresh.run(NAME, trigger="schedule")).error_kind == "paused"
        engine.run_scraper.assert_not_called()

        state = await fresh.get_persistent_state(NAME)
        assert state["auth_state"] == "needs_login"
        assert state["auth_reason"] == "bot_blocked"
        assert state["auth_message"] == "PerimeterX 403"
        assert state["auth_changed_at"].endswith("+00:00")

    @pytest.mark.asyncio
    async def test_slot_is_claimed_before_the_auth_read(self, db_session_factory):
        """Neither a second run nor begin_login can slip in while run() awaits the auth read."""
        reg, engine = _make_registry(db_session_factory)
        record = reg.get(NAME)
        record.last_status = "ok"
        await _seed_needs_login(db_session_factory)
        gate = asyncio.Event()
        real_read = reg.get_auth_state

        async def slow_read(name):
            await gate.wait()
            return await real_read(name)

        with patch.object(reg, "get_auth_state", side_effect=slow_read):
            task = asyncio.create_task(reg.run(NAME, trigger="schedule"))
            await asyncio.sleep(0)
            assert record.last_status == "running"
            with pytest.raises(ScrapeRunning):
                reg.begin_login(NAME)
            second = await reg.run(NAME, trigger="manual")
            assert second.error == "already running"
            gate.set()
            result = await task

        assert result.error_kind == "paused"
        assert record.last_status == "ok"
        engine.run_scraper.assert_not_called()

    @pytest.mark.asyncio
    async def test_state_in_memory_without_a_database(self):
        broadcast = AsyncMock()
        reg, engine = _make_registry(None, AUTH_REQUIRED, OK, broadcast=broadcast)

        await reg.run(NAME, trigger="schedule")
        assert (await reg.run(NAME, trigger="catch_up")).error_kind == "paused"
        assert (await reg.get_persistent_state(NAME))["auth_state"] == "needs_login"
        await reg.run(NAME, trigger="manual")

        assert (await reg.get_persistent_state(NAME))["auth_state"] == "ok"
        assert engine.run_scraper.call_count == 2
        assert [c.args[0]["auth_state"] for c in broadcast.await_args_list] == [
            "needs_login", "ok",
        ]


class TestLoginHooks:
    @pytest.mark.parametrize("trigger", ["schedule", "catch_up", "manual"])
    @pytest.mark.asyncio
    async def test_runs_refused_during_a_login_session(self, db_session_factory, trigger):
        reg, engine = _make_registry(db_session_factory)
        record = reg.get(NAME)
        record.last_status = "ok"

        reg.begin_login(NAME)
        result = await reg.run(NAME, trigger=trigger)

        assert result.success is False
        assert result.error_kind == "login_in_progress"
        assert result.error == "login session in progress"
        engine.run_scraper.assert_not_called()
        assert record.last_status == "ok"
        async with db_session_factory() as session:
            assert (await session.execute(select(Scraper))).first() is None

    @pytest.mark.asyncio
    async def test_login_confirm_runs_during_its_session_and_end_login_clears(
        self, db_session_factory,
    ):
        reg, engine = _make_registry(db_session_factory, OK, OK)
        reg.begin_login(NAME)
        assert reg.login_active(NAME)

        assert (await reg.run(NAME, trigger="login_confirm")).success is True
        reg.end_login(NAME)

        assert not reg.login_active(NAME)
        assert (await reg.run(NAME, trigger="manual")).success is True
        assert engine.run_scraper.call_count == 2

    @pytest.mark.asyncio
    async def test_begin_login_refuses_while_running_and_unknown(self, db_session_factory):
        reg, _ = _make_registry(db_session_factory)
        reg.get(NAME).last_status = "running"
        with pytest.raises(ScrapeRunning, match="a scrape is running"):
            reg.begin_login(NAME)
        assert not reg.login_active(NAME)
        with pytest.raises(KeyError):
            reg.begin_login("nope")

    @pytest.mark.asyncio
    async def test_mark_login_verified_restores_ok(self, db_session_factory):
        await _seed_needs_login(db_session_factory)
        broadcast = AsyncMock()
        reg, _ = _make_registry(db_session_factory, broadcast=broadcast)

        await reg.mark_login_verified(NAME)

        row = await _row(db_session_factory)
        assert row.auth_state is None
        events = await _events(db_session_factory)
        assert [(e.event_type, e.severity, e.payload) for e in events] == [
            ("scraper_login_restored", "info", {"via": "login"}),
        ]
        broadcast.assert_awaited_once_with(
            {"type": "scraper_auth_changed", "name": NAME, "auth_state": "ok"}
        )

    @pytest.mark.asyncio
    async def test_mark_login_verified_is_a_noop_when_ok(self, db_session_factory):
        broadcast = AsyncMock()
        reg, _ = _make_registry(db_session_factory, OK, broadcast=broadcast)
        await reg.run(NAME)

        await reg.mark_login_verified(NAME)
        await reg.mark_login_verified("never-ran")

        assert await _events(db_session_factory) == []
        broadcast.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_confirmation_run_hitting_auth_goes_back_to_needs_login(
        self, db_session_factory,
    ):
        await _seed_needs_login(db_session_factory)
        reg, _ = _make_registry(db_session_factory, AUTH_REQUIRED)
        reg.begin_login(NAME)

        await reg.mark_login_verified(NAME)
        await reg.run(NAME, trigger="login_confirm")
        reg.end_login(NAME)

        row = await _row(db_session_factory)
        assert (row.auth_state, row.auth_reason) == ("needs_login", "auth_required")
        assert [e.event_type for e in await _events(db_session_factory)] == [
            "scraper_login_restored", "scraper_needs_login",
        ]

    @pytest.mark.asyncio
    async def test_broadcast_failure_does_not_break_the_run(self, db_session_factory):
        broadcast = AsyncMock(side_effect=RuntimeError("socket gone"))
        reg, _ = _make_registry(db_session_factory, AUTH_REQUIRED, broadcast=broadcast)

        result = await reg.run(NAME)

        assert result.error_kind == "auth_required"
        assert (await _row(db_session_factory)).auth_state == "needs_login"


AUTH = ScraperAuth(
    kind="browser_profile",
    engine="patchright",
    profile_dir_param="profile_dir",
    login_url="https://example.com/login",
    verify=ScraperAuthVerify(url="https://example.com/picks", selectors=("table",), timeout_s=30),
    session_timeout_s=1200,
)


class TestApiFields:
    """GET /api/scrapers and /api/scrapers/{name} carry the section-6.5 fields."""

    @pytest.mark.asyncio
    async def test_fields_for_a_paused_scraper_with_auth(self, client, db_session_factory):
        from coordinator.api.routes import scrapers as routes

        await _seed_needs_login(db_session_factory)
        reg, _ = _make_registry(db_session_factory)
        record = reg.get(NAME)
        record.auth = AUTH
        record.manifest = {"config": {"parameters": [
            {"name": "headless", "type": "bool", "default": False},
        ]}}

        with patch.object(routes, "_require_registry", return_value=reg), \
             patch.object(routes, "_next_run_for", return_value=None):
            one = (await client.get(f"/api/scrapers/{NAME}")).json()
            listed = (await client.get("/api/scrapers")).json()

        assert listed == [one]
        assert one["auth"] == {"kind": "browser_profile", "login_supported": True, "headed": True}
        assert one["auth_state"] == "needs_login"
        assert one["auth_reason"] == "bot_blocked"
        assert one["auth_message"] == "PerimeterX 403"
        assert one["auth_changed_at"] is not None
        assert one["schedule_paused"] is True
        assert one["login_session"] is None

    @pytest.mark.asyncio
    async def test_fields_for_a_healthy_scraper_without_auth(self, client, db_session_factory):
        from coordinator.api.routes import scrapers as routes

        reg, _ = _make_registry(db_session_factory)
        with patch.object(routes, "_require_registry", return_value=reg), \
             patch.object(routes, "_next_run_for", return_value=None):
            body = (await client.get(f"/api/scrapers/{NAME}")).json()

        assert body["auth"] is None
        assert body["auth_state"] == "ok"
        assert body["auth_reason"] is None
        assert body["auth_message"] is None
        assert body["auth_changed_at"] is None
        assert body["schedule_paused"] is False
        assert body["login_session"] is None

    @pytest.mark.asyncio
    async def test_run_now_is_manual_and_reports_error_kind(self, client, db_session_factory):
        from coordinator.api.routes import scrapers as routes

        await _seed_needs_login(db_session_factory)
        reg, engine = _make_registry(db_session_factory, AUTH_REQUIRED)
        with patch.object(routes, "_require_registry", return_value=reg), \
             patch.object(routes, "_next_run_for", return_value=None):
            body = (await client.post(f"/api/scrapers/{NAME}/run")).json()

        engine.run_scraper.assert_called_once()
        assert body["success"] is False
        assert body["error_kind"] == "auth_required"
        assert body["record"]["schedule_paused"] is True

    @pytest.mark.asyncio
    async def test_run_now_during_login_session(self, client, db_session_factory):
        from coordinator.api.routes import scrapers as routes

        reg, engine = _make_registry(db_session_factory)
        reg.begin_login(NAME)
        with patch.object(routes, "_require_registry", return_value=reg), \
             patch.object(routes, "_next_run_for", return_value=None):
            body = (await client.post(f"/api/scrapers/{NAME}/run")).json()

        engine.run_scraper.assert_not_called()
        assert body["success"] is False
        assert body["error"] == "login session in progress"
