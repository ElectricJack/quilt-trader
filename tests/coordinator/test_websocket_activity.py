import pytest
import pytest_asyncio
from sqlalchemy import select

from coordinator.database.models import Worker, WorkerActivity
from coordinator.main import create_app
from coordinator.api.dependencies import get_container


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


@pytest_asyncio.fixture
async def running_app():
    import asyncio
    app = create_app(
        database_url="sqlite+aiosqlite:///file::memory:?cache=shared&uri=true",
        encryption_key="test-key-32-bytes-long!!!!!!!!",
    )
    async with app.router.lifespan_context(app):
        # Drain so background tasks' first iterations finish before fixtures
        # start writing — avoids SQLite "table is locked" under shared-cache.
        await asyncio.sleep(0.05)
        yield app


@pytest_asyncio.fixture
async def db_session(running_app):
    container = get_container()
    async with container.session_factory() as session:
        yield session
        await session.rollback()


@pytest.mark.asyncio
async def test_activity_event_persisted_and_broadcast_to_worker_subscribers(running_app, db_session):
    from coordinator.api.websocket import manager, handle_worker_message
    w = Worker(name="w", status="online")
    db_session.add(w)
    await db_session.commit()
    wid = w.id

    dashboard_ws = FakeWebSocket()
    manager.subscribe(dashboard_ws, f"worker:{wid}")
    try:
        await handle_worker_message(FakeWebSocket(), {
            "type": "activity_event",
            "worker_id": wid,
            "instance_id": None,
            "timestamp": "2026-05-16T12:00:00Z",
            "event_type": "instance_started",
            "severity": "info",
            "payload": {"foo": "bar"},
        })
    finally:
        manager.unsubscribe(dashboard_ws, f"worker:{wid}")

    from coordinator.api.dependencies import get_container
    container = get_container()
    async with container.session_factory() as session:
        rows = (await session.execute(
            select(WorkerActivity).where(WorkerActivity.worker_id == wid)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].event_type == "instance_started"
        assert rows[0].kind == "event"
        assert rows[0].payload == {"foo": "bar"}

    assert any(
        m.get("type") == "activity_event" and m.get("event_type") == "instance_started"
        for m in dashboard_ws.sent
    )


@pytest.mark.asyncio
async def test_algo_log_maps_python_log_level_to_severity(running_app, db_session):
    from coordinator.api.websocket import manager, handle_worker_message
    w = Worker(name="w", status="online")
    db_session.add(w)
    await db_session.commit()
    wid = w.id

    await handle_worker_message(FakeWebSocket(), {
        "type": "algo_log",
        "worker_id": wid,
        "instance_id": None,
        "timestamp": "2026-05-16T12:00:00Z",
        "logger_name": "myalgo.signals",
        "level": "WARNING",
        "message": "RSI threshold breached",
    })

    from coordinator.api.dependencies import get_container
    container = get_container()
    async with container.session_factory() as session:
        row = (await session.execute(
            select(WorkerActivity).where(WorkerActivity.worker_id == wid)
        )).scalar_one()
        assert row.kind == "log"
        assert row.severity == "warn"
        assert row.logger_name == "myalgo.signals"
        assert row.message == "RSI threshold breached"


@pytest.mark.asyncio
async def test_subscribe_target_routes_broadcasts_to_deployment_subscribers(running_app, db_session):
    from coordinator.api.websocket import manager, handle_worker_message, handle_dashboard_message
    from coordinator.database.models import (
        Algorithm, Account, AlgorithmInstance,
    )
    algo = Algorithm(repo_url="x", name="A")
    acct = Account(name="A", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
    w = Worker(name="W", status="online")
    db_session.add_all([algo, acct, w])
    await db_session.flush()
    inst = AlgorithmInstance(algorithm_id=algo.id, account_id=acct.id, worker_id=w.id, status="running")
    db_session.add(inst)
    await db_session.commit()

    dashboard_ws = FakeWebSocket()
    await handle_dashboard_message(dashboard_ws, {"type": "subscribe", "target": f"deployment:{inst.id}"})
    assert any(m.get("type") == "subscribed" and m.get("target") == f"deployment:{inst.id}"
               for m in dashboard_ws.sent)

    await handle_worker_message(FakeWebSocket(), {
        "type": "activity_event",
        "worker_id": w.id, "instance_id": inst.id,
        "timestamp": "2026-05-16T12:00:00Z",
        "event_type": "trade_executed", "severity": "info",
        "payload": {"symbol": "AAPL"},
    })
    assert any(
        m.get("type") == "activity_event" and m.get("instance_id") == inst.id
        for m in dashboard_ws.sent
    )
    manager.unsubscribe_all(dashboard_ws)
