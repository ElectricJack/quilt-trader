import os
import pytest
from datetime import datetime, timezone, timedelta
import pandas as pd
from coordinator.services.archival import ArchivalService

@pytest.fixture
def archive_dir(tmp_path):
    d = tmp_path / "archive"
    d.mkdir()
    return str(d)

def test_archive_path(archive_dir):
    svc = ArchivalService(archive_dir=archive_dir)
    path = svc.archive_path("decision_log", "2025-01-01", "2025-01-31")
    assert "decision_log" in path
    assert "2025-01-01" in path
    assert path.endswith(".parquet")

def test_export_to_parquet(archive_dir):
    svc = ArchivalService(archive_dir=archive_dir)
    df = pd.DataFrame({"id": ["1", "2", "3"], "timestamp": ["2025-01-01", "2025-01-02", "2025-01-03"], "data": ["a", "b", "c"]})
    path = svc.export_to_parquet("decision_log", "2025-01-01", "2025-01-31", df)
    assert os.path.exists(path)
    loaded = pd.read_parquet(path)
    assert len(loaded) == 3

def test_export_empty_dataframe(archive_dir):
    svc = ArchivalService(archive_dir=archive_dir)
    df = pd.DataFrame()
    path = svc.export_to_parquet("trade_log", "2025-01-01", "2025-01-31", df)
    assert path is None

def test_list_archives(archive_dir):
    svc = ArchivalService(archive_dir=archive_dir)
    df = pd.DataFrame({"id": ["1"], "data": ["a"]})
    svc.export_to_parquet("decision_log", "2025-01-01", "2025-01-31", df)
    svc.export_to_parquet("trade_log", "2025-02-01", "2025-02-28", df)
    archives = svc.list_archives()
    assert len(archives) == 2


@pytest.mark.asyncio
async def test_prune_worker_activity_deletes_rows_older_than_retention(test_app, db_session):
    from coordinator.api.dependencies import get_container
    from coordinator.database.models import Worker, WorkerActivity

    w = Worker(name="w", status="online")
    db_session.add(w)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add_all([
        WorkerActivity(worker_id=w.id, kind="event", event_type="old", severity="info",
                       timestamp=now - timedelta(days=8)),
        WorkerActivity(worker_id=w.id, kind="event", event_type="new", severity="info",
                       timestamp=now - timedelta(days=1)),
    ])
    await db_session.commit()
    wid = w.id

    from coordinator.services.archival import prune_worker_activity
    from sqlalchemy import select

    container = get_container()
    deleted = await prune_worker_activity(container.session_factory, retention_days=7)
    assert deleted == 1

    async with container.session_factory() as session:
        rows = (await session.execute(
            select(WorkerActivity).where(WorkerActivity.worker_id == wid)
        )).scalars().all()
        assert [r.event_type for r in rows] == ["new"]
