import asyncio
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import select

from coordinator.database.models import Worker
from coordinator.api.dependencies import get_container


@pytest.mark.asyncio
async def test_sweeper_marks_stale_workers_offline(test_app, db_session):
    stale = Worker(
        name="stale", status="online",
        last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=120),
    )
    fresh = Worker(
        name="fresh", status="online",
        last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    db_session.add_all([stale, fresh])
    await db_session.flush()
    await db_session.commit()
    stale_id, fresh_id = stale.id, fresh.id

    from coordinator.services.worker_health import sweep_stale_workers
    container = get_container()
    transitioned = await sweep_stale_workers(container.session_factory, offline_after_seconds=60)
    assert stale_id in transitioned
    assert fresh_id not in transitioned

    async with container.session_factory() as session:
        s = (await session.execute(select(Worker).where(Worker.id == stale_id))).scalar_one()
        f = (await session.execute(select(Worker).where(Worker.id == fresh_id))).scalar_one()
        assert s.status == "offline"
        assert f.status == "online"
