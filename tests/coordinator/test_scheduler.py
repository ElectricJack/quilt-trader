import asyncio
from datetime import datetime, timezone

import pytest

from coordinator.services.scheduler import SchedulerService


def test_scheduler_creates():
    scheduler = SchedulerService()
    assert scheduler is not None


def test_add_cron_job():
    scheduler = SchedulerService()
    called = []

    def job():
        called.append(True)

    scheduler.add_cron_job("test-job", job, "*/5 * * * *")
    jobs = scheduler.list_jobs()
    assert any(j["id"] == "test-job" for j in jobs)


def test_remove_job():
    scheduler = SchedulerService()
    scheduler.add_cron_job("removable", lambda: None, "0 * * * *")
    scheduler.remove_job("removable")
    jobs = scheduler.list_jobs()
    assert not any(j["id"] == "removable" for j in jobs)


def test_list_empty():
    scheduler = SchedulerService()
    assert scheduler.list_jobs() == []


@pytest.mark.asyncio
async def test_shutdown_waits_for_running_job():
    scheduler = SchedulerService()
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def job():
        started.set()
        await release.wait()
        finished.set()

    scheduler.start()
    scheduler.add_cron_job("running-job", job, "0 0 * * *")
    scheduler._scheduler.modify_job(
        "running-job", next_run_time=datetime.now(timezone.utc)
    )
    await asyncio.wait_for(started.wait(), timeout=2)

    shutdown = asyncio.create_task(scheduler.shutdown(timeout=2))
    await asyncio.sleep(0)
    assert not shutdown.done()
    release.set()
    await asyncio.wait_for(shutdown, timeout=2)
    assert finished.is_set()
