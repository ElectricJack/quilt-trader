from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import date
from typing import ClassVar, TYPE_CHECKING

import pandas as pd
from sqlalchemy import update

from coordinator.database.models import Base, MarketDataDownload

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
