import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

class ArchivalService:
    def __init__(self, archive_dir: str) -> None:
        self._archive_dir = archive_dir

    def archive_path(self, table_name: str, start: str, end: str) -> str:
        return os.path.join(self._archive_dir, table_name, f"{start}_to_{end}.parquet")

    def export_to_parquet(self, table_name: str, start: str, end: str, df: pd.DataFrame) -> Optional[str]:
        if df.empty:
            return None
        path = self.archive_path(table_name, start, end)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        df.to_parquet(path, index=False)
        return path

    def list_archives(self) -> list[dict]:
        results = []
        if not os.path.exists(self._archive_dir):
            return results
        for table_name in os.listdir(self._archive_dir):
            table_dir = os.path.join(self._archive_dir, table_name)
            if not os.path.isdir(table_dir):
                continue
            for f in os.listdir(table_dir):
                if f.endswith(".parquet"):
                    path = os.path.join(table_dir, f)
                    results.append({
                        "table_name": table_name, "file_name": f,
                        "file_path": path, "size_bytes": os.path.getsize(path),
                    })
        return results

    def load_archive(self, path: str) -> pd.DataFrame:
        return pd.read_parquet(path)


async def prune_worker_activity(
    session_factory: async_sessionmaker[AsyncSession], retention_days: int
) -> int:
    """Delete worker_activity rows older than `retention_days`.

    Returns the number of rows deleted.
    """
    from coordinator.database.models import WorkerActivity

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    async with session_factory() as session:
        result = await session.execute(
            delete(WorkerActivity).where(WorkerActivity.timestamp < cutoff)
        )
        await session.commit()
        return result.rowcount or 0


async def run_worker_activity_retention_loop(
    session_factory: async_sessionmaker[AsyncSession],
    interval_seconds: int = 3600,
    retention_days: int = 7,
) -> None:
    """Periodic prune loop. Runs every `interval_seconds` (default 1 hour)."""
    while True:
        try:
            deleted = await prune_worker_activity(session_factory, retention_days)
            if deleted:
                logger.info(
                    "Pruned %d worker_activity rows older than %d days",
                    deleted,
                    retention_days,
                )
        except Exception:
            logger.exception("worker_activity retention sweep failed")
        await asyncio.sleep(interval_seconds)
