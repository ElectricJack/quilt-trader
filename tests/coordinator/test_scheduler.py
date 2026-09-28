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
async def test_drain_finishes_running_job_before_shutdown():
    scheduler = SchedulerService()
    entered = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()

    async def job():
        entered.set()
        await release.wait()
        completed.set()

    scheduler.add_cron_job("database-job", job, "0 * * * *")
    scheduler.start()
    scheduler._scheduler.modify_job("database-job", next_run_time=datetime.now(timezone.utc))
    await asyncio.wait_for(entered.wait(), timeout=5)

    drain_task = asyncio.create_task(scheduler.drain())
    await asyncio.sleep(0)
    assert not drain_task.done()
    assert not completed.is_set()

    release.set()
    await asyncio.wait_for(drain_task, timeout=5)
    assert completed.is_set()
