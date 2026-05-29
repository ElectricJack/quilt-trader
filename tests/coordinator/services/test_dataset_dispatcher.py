import asyncio
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
import pytest
from sqlalchemy import select
from coordinator.database.models import DatasetDownload
from coordinator.services.download_job import DatasetJobDispatcher
from coordinator.services.datasets.quota import QuotaExhausted
from coordinator.services.datasets.registry import (
    DatasetSpec, Pagination, register, clear_registry,
)


@pytest.fixture(autouse=True)
def _clean():
    clear_registry()
    register(DatasetSpec(
        name="fmp.t", provider="fmp", endpoint_path="/x",
        event_date_column="d", knowledge_date_column=None,
        symbol_keyed=False, id_columns=("d",),
        columns={"d": "date"}, pagination=Pagination.PAGE,
    ))
    yield
    clear_registry()


@pytest.fixture
def adapter():
    a = MagicMock()
    a.provider = "fmp"

    async def _fetch(spec, params, *, on_rows, on_page):
        await on_rows([{"d": "2024-01-01"}], 0)

    a.fetch_dataset = AsyncMock(side_effect=_fetch)
    return a


@pytest.fixture
def svc():
    s = MagicMock()
    s.upsert = AsyncMock(return_value=1)
    return s


@pytest.mark.asyncio
async def test_execute_success_marks_completed(adapter, svc, db_session_factory):
    job = DatasetDownload(dataset_name="fmp.t", provider="fmp",
                          request_payload={}, status="queued")
    async with db_session_factory() as s:
        s.add(job); await s.commit(); await s.refresh(job)
    d = DatasetJobDispatcher(adapters={"fmp": adapter}, service=svc,
                             session_factory=db_session_factory)
    await d.execute(job, manager=None)
    assert job.status == "completed"
    assert job.rows_fetched == 1
    assert job.completed_at is not None


@pytest.mark.asyncio
async def test_execute_quota_exhausted_marks_paused_quota(adapter, svc, db_session_factory):
    adapter.fetch_dataset.side_effect = QuotaExhausted("fmp", 250, 250)
    job = DatasetDownload(dataset_name="fmp.t", provider="fmp",
                          request_payload={}, status="queued")
    async with db_session_factory() as s:
        s.add(job); await s.commit(); await s.refresh(job)
    d = DatasetJobDispatcher(adapters={"fmp": adapter}, service=svc,
                             session_factory=db_session_factory)
    await d.execute(job, manager=None)
    assert job.status == "paused_quota"


@pytest.mark.asyncio
async def test_execute_generic_exception_marks_failed_with_message(adapter, svc, db_session_factory):
    adapter.fetch_dataset.side_effect = RuntimeError("boom")
    job = DatasetDownload(dataset_name="fmp.t", provider="fmp",
                          request_payload={}, status="queued")
    async with db_session_factory() as s:
        s.add(job); await s.commit(); await s.refresh(job)
    d = DatasetJobDispatcher(adapters={"fmp": adapter}, service=svc,
                             session_factory=db_session_factory)
    await d.execute(job, manager=None)
    assert job.status == "failed"
    assert "boom" in (job.error_message or "")


@pytest.mark.asyncio
async def test_execute_cancelled_marks_cancelled_and_reraises(adapter, svc, db_session_factory):
    adapter.fetch_dataset.side_effect = asyncio.CancelledError
    job = DatasetDownload(dataset_name="fmp.t", provider="fmp",
                          request_payload={}, status="queued")
    async with db_session_factory() as s:
        s.add(job); await s.commit(); await s.refresh(job)
    d = DatasetJobDispatcher(adapters={"fmp": adapter}, service=svc,
                             session_factory=db_session_factory)
    with pytest.raises(asyncio.CancelledError):
        await d.execute(job, manager=None)
    assert job.status == "cancelled"


@pytest.mark.asyncio
async def test_recover_orphaned_jobs_flips_running_to_queued(db_session_factory):
    async with db_session_factory() as s:
        s.add_all([
            DatasetDownload(dataset_name="fmp.t", provider="fmp",
                            request_payload={}, status="running"),
            DatasetDownload(dataset_name="fmp.t", provider="fmp",
                            request_payload={}, status="completed"),
        ])
        await s.commit()
    d = DatasetJobDispatcher(adapters={}, service=None,
                             session_factory=db_session_factory)
    await d.recover_orphaned_jobs()
    async with db_session_factory() as s:
        statuses = sorted(r.status for r in (await s.execute(select(DatasetDownload))).scalars())
        assert statuses == ["completed", "queued"]
