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
