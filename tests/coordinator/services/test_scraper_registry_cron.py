"""A registered scraper cron job must actually run ScraperRegistry.run.

Background: the job func used to be a sync lambda wrapping
`asyncio.create_task(self.run(n))`. AsyncIOScheduler's AsyncIOExecutor only
awaits coroutine functions on the event loop; anything else runs in the
loop's default thread pool, where `create_task` raises "no running event
loop". Every scheduled scrape failed that way and only the catch-up path
(which fires once at registration) ever ran scrapers.

These tests drive a real, started SchedulerService so the executor dispatch
is exercised end to end.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
import yaml
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

from coordinator.services.scheduler import SchedulerService
from coordinator.services.scraper_engine import ScraperEngine, ScraperResult
from coordinator.services.scraper_registry import ScraperRegistry


def _write_manifest(pkg_dir: str, *, name: str) -> None:
    os.makedirs(pkg_dir, exist_ok=True)
    with open(os.path.join(pkg_dir, "quilt.yaml"), "w") as f:
        yaml.safe_dump({
            "type": "scraper",
            "name": name,
            "schedule": "0 14 * * 1-5",
            "entry_point": "scraper.py",
        }, f)


async def _fire_now_and_wait(scheduler: SchedulerService, job_id: str):
    """Make the job due now and wait for it to finish; return the job event."""
    done: asyncio.Future = asyncio.get_running_loop().create_future()

    def on_event(event):
        if event.job_id == job_id and not done.done():
            done.get_loop().call_soon_threadsafe(
                lambda: done.done() or done.set_result(event)
            )

    scheduler._scheduler.add_listener(on_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
    scheduler._scheduler.modify_job(job_id, next_run_time=datetime.now(timezone.utc))
    return await asyncio.wait_for(done, timeout=5)


@pytest.mark.parametrize("register", ["discover_and_register", "register_scraper"])
@pytest.mark.asyncio
async def test_registered_cron_job_runs_scraper(tmp_path, register):
    packages_dir = tmp_path / "packages"
    _write_manifest(str(packages_dir / "my-scraper"), name="my-scraper")

    engine = MagicMock(spec=ScraperEngine)
    engine.run_scraper.return_value = ScraperResult(success=False, error="stub")

    scheduler = SchedulerService()
    scheduler.start()
    try:
        registry = ScraperRegistry(
            engine=engine,
            scheduler=scheduler,
            packages_dir=str(packages_dir),
            configs_dir=str(tmp_path / "scraper_configs"),
        )
        if register == "discover_and_register":
            registry.discover_and_register()
        else:
            registry.register_scraper("my-scraper")

        event = await _fire_now_and_wait(scheduler, "scraper:my-scraper")
    finally:
        scheduler.shutdown()

    assert event.exception is None, repr(event.exception)
    engine.run_scraper.assert_called_once_with("my-scraper", "csv", {})
    assert registry.get("my-scraper").last_status == "failed"
