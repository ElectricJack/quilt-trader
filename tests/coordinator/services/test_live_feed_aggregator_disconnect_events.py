import pytest
from sqlalchemy import select

from coordinator.database.connection import create_engine, create_session_factory
from coordinator.database.models import Base, WorkerActivity, Worker
from coordinator.services.live_feed_aggregator import LiveFeedAggregator


@pytest.mark.asyncio
async def test_emit_stream_disconnect_inserts_worker_activity_row():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(engine)

    # A worker row is required (worker_id FK).
    async with sf() as session:
        w = Worker(name="coord", status="online")
        session.add(w)
        await session.commit()
        worker_id = w.id

    agg = LiveFeedAggregator(session_factory=sf, encryption=None)
    agg._coord_worker_id = worker_id  # injected by the lifespan in prod

    await agg._emit_stream_event(
        broker="tradier", asset_class="equities", symbols=["SPY", "QQQ"],
        event_type="stream_disconnect", reason="connection reset",
    )

    async with sf() as session:
        rows = (await session.execute(select(WorkerActivity))).scalars().all()
        assert len(rows) == 1
        assert rows[0].event_type == "stream_disconnect"
        assert rows[0].severity == "warn"
        assert "SPY" in rows[0].payload["symbols"]

    await engine.dispose()
