"""Scheduler must run in UTC.

Background: APScheduler defaults to the host's local timezone, so a cron
expression like `0 14 * * 1-5` is interpreted as 14:00 LOCAL. Several jobs
in this codebase have comments stating UTC times (alpha-picks-scraper's
`0 14 * * 1-5` → 14:00 UTC; account_daily_close's `35 20 * * 1-5` → 20:35
UTC) which is wrong unless the scheduler is explicitly UTC-anchored.
"""
from __future__ import annotations

from datetime import datetime, timezone

from coordinator.services.scheduler import SchedulerService


def test_scheduler_uses_utc():
    svc = SchedulerService()
    sched_tz = svc._scheduler.timezone
    # zoneinfo.UTC, datetime.timezone.utc, or pytz.UTC all answer "UTC"
    # via utcoffset on a representative datetime.
    assert sched_tz.utcoffset(datetime(2026, 1, 1)).total_seconds() == 0


def test_cron_0_14_resolves_to_14_utc():
    svc = SchedulerService()

    captured = {}

    async def noop():
        pass

    def capture(*args, **kwargs):
        captured["trigger"] = kwargs.get("trigger")
        captured["kwargs"] = kwargs
        return None

    # Monkey-patch the inner scheduler's add_job to capture the trigger
    # without actually scheduling it.
    svc._scheduler.add_job = capture

    svc.add_cron_job(job_id="test", func=noop, cron_expr="0 14 * * 1-5")

    trigger = captured["trigger"]
    # Use a Sunday so the next valid weekday fire is unambiguous.
    sun = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)
    next_fire = trigger.get_next_fire_time(None, sun)
    assert next_fire.hour == 14
    assert next_fire.minute == 0
    # weekday 0 = Monday in Python; cron 1-5 → Mon-Fri
    assert next_fire.weekday() == 0
    assert next_fire.utcoffset().total_seconds() == 0
