import pytest
from sqlalchemy import select
from coordinator.database.models import WorkerActivity, Worker


@pytest.mark.asyncio
async def test_worker_activity_round_trip(db_session):
    w = Worker(name="w", status="online")
    db_session.add(w)
    await db_session.flush()
    row = WorkerActivity(
        worker_id=w.id, kind="event", event_type="trade_executed",
        severity="info", message="BUY 10 AAPL", payload={"symbol": "AAPL"},
    )
    db_session.add(row)
    await db_session.commit()
    fetched = (await db_session.execute(
        select(WorkerActivity).where(WorkerActivity.worker_id == w.id)
    )).scalar_one()
    assert fetched.payload == {"symbol": "AAPL"}
    assert fetched.kind == "event"
    assert fetched.event_type == "trade_executed"
    assert fetched.severity == "info"
    assert fetched.message == "BUY 10 AAPL"
