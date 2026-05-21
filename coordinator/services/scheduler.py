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

    @staticmethod
    def _convert_dow(posix_dow: str) -> str:
        """Convert POSIX cron day-of-week (0=Sun) to APScheduler (0=Mon).

        APScheduler's CronTrigger uses Python weekday convention (0=Monday)
        while standard POSIX cron uses 0=Sunday. This converts ranges, lists,
        and single values so manifests can use the familiar cron syntax.
        """
        mapping = {"0": "6", "1": "0", "2": "1", "3": "2", "4": "3", "5": "4", "6": "5", "7": "6"}
        if posix_dow == "*":
            return "*"
        parts = []
        for segment in posix_dow.split(","):
            if "-" in segment:
                lo, hi = segment.split("-", 1)
                parts.append(f"{mapping.get(lo, lo)}-{mapping.get(hi, hi)}")
            else:
                parts.append(mapping.get(segment, segment))
        return ",".join(parts)

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
            day_of_week=self._convert_dow(parts[4]),
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
