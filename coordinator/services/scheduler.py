import logging
from typing import Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        self._scheduler.start()

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    def add_cron_job(
        self,
        job_id: str,
        func: Callable,
        cron_expr: str,
        jitter: Optional[int] = None,
    ) -> None:
        parts = cron_expr.split()
        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
            jitter=jitter,
        )
        self._scheduler.add_job(func, trigger=trigger, id=job_id, replace_existing=True)

    def remove_job(self, job_id: str) -> None:
        self._scheduler.remove_job(job_id)

    def list_jobs(self) -> list[dict]:
        result = []
        for job in self._scheduler.get_jobs():
            next_run = getattr(job, "next_run_time", None)
            result.append({"id": job.id, "next_run": str(next_run)})
        return result
