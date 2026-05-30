from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import date, datetime, timezone
from typing import ClassVar, TYPE_CHECKING

import pandas as pd
from sqlalchemy import update

from coordinator.database.models import Base, DatasetDownload, MarketDataDownload
from coordinator.services.datasets.quota import QuotaExhausted
from coordinator.services.datasets.registry import get as _registry_get

if TYPE_CHECKING:
    from coordinator.services.download_manager import DownloadManager

logger = logging.getLogger(__name__)


class JobDispatcher(ABC):
    job_model: ClassVar[type[Base]]

    @abstractmethod
    async def execute(self, job, manager: "DownloadManager") -> None:
        ...


class BarsJobDispatcher(JobDispatcher):
    job_model = MarketDataDownload

    async def execute(self, job: MarketDataDownload, manager: "DownloadManager") -> None:
        await manager._run_download_body(
            job.id,
            job.symbols,
            job.provider,
            job.data_type,
            job.timeframe,
            job.date_range_start,
            job.date_range_end,
        )


class DatasetJobDispatcher(JobDispatcher):
    job_model = DatasetDownload

    def __init__(self, adapters: dict, service, session_factory):
        self._adapters = adapters
        self._service = service
        self._sf = session_factory

    async def _set(self, job, **fields):
        async with self._sf() as s:
            for k, v in fields.items():
                setattr(job, k, v)
            s.add(job)
            await s.commit()

    async def execute(self, job: DatasetDownload, manager) -> None:
        spec = _registry_get(job.dataset_name)
        adapter = self._adapters[spec.provider]
        params = job.request_payload or {}
        symbol = params.get("symbol") if spec.symbol_keyed else None

        await self._set(job, status="running", started_at=datetime.now(timezone.utc))

        async def on_rows(rows, page_idx):
            await self._service.upsert(spec, rows, symbol=symbol)
            await self._set(job,
                            rows_fetched=job.rows_fetched + len(rows),
                            last_page=page_idx + 1)

        async def on_page(idx, total):
            await self._set(job, progress_message=f"page {idx} / {total} rows")

        # Strip framework-only keys (e.g. storage partition hint) before
        # the params are passed through to the upstream API.
        api_params = {k: v for k, v in params.items()
                      if k not in spec.storage_only_keys}

        try:
            await adapter.fetch_dataset(spec, dict(api_params),
                                        on_rows=on_rows, on_page=on_page)
            await self._set(job, status="completed",
                            completed_at=datetime.now(timezone.utc),
                            progress_pct=1.0)
        except QuotaExhausted:
            await self._set(job, status="paused_quota",
                            progress_message="quota exhausted; paused until reset")
        except asyncio.CancelledError:
            await self._set(job, status="cancelled")
            raise
        except Exception as e:
            await self._set(job, status="failed", error_message=str(e),
                            completed_at=datetime.now(timezone.utc))

    async def recover_orphaned_jobs(self) -> None:
        """Flip rows left 'running' (from a killed process) back to 'queued'."""
        async with self._sf() as s:
            await s.execute(
                update(DatasetDownload)
                .where(DatasetDownload.status == "running")
                .values(status="queued", started_at=None)
            )
            await s.commit()
