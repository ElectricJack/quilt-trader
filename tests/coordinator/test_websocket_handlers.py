"""
Unit tests for coordinator WebSocket message handlers.

We test handle_worker_message() directly by:
  - Creating a real in-memory DB session via the app lifespan
  - Providing a mock WebSocket that captures sent messages
"""
import pytest
import pytest_asyncio
from sqlalchemy import select

from coordinator.main import create_app
from coordinator.api.dependencies import get_container
from coordinator.database.models import (
    Algorithm,
    AlgorithmInstance,
    Worker,
    Account,
    DecisionLog,
)


class FakeWebSocket:
    """Minimal mock that records messages sent to it."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


@pytest_asyncio.fixture
async def running_app():
    app = create_app(
        database_url="sqlite+aiosqlite:///file::memory:?cache=shared&uri=true",
        encryption_key="test-key-32-bytes-long!!!!!!!!",
    )
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def db_session(running_app):
    container = get_container()
    async with container.session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def worker_and_account(db_session):
    """Create a Worker and Account row, return their ids."""
    worker = Worker(name="test-worker", tailscale_ip="100.0.0.1", status="offline")
    account = Account(
        name="test-account",
        broker_type="alpaca",
        credentials="{}",
        supported_asset_types=["equities"],
    )
    db_session.add(worker)
    db_session.add(account)
    await db_session.flush()
    await db_session.commit()
    return worker.id, account.id


@pytest_asyncio.fixture
async def algo_instance(db_session, worker_and_account):
    """Create a full Algorithm + AlgorithmInstance, return instance id."""
    worker_id, account_id = worker_and_account
    algo = Algorithm(repo_url="https://github.com/test/algo", name="test-algo")
    db_session.add(algo)
    await db_session.flush()

    instance = AlgorithmInstance(
        algorithm_id=algo.id,
        account_id=account_id,
        worker_id=worker_id,
        status="stopped",
    )
    db_session.add(instance)
    await db_session.flush()
    await db_session.commit()
    return instance.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ping_message(running_app):
    from coordinator.api.websocket import handle_worker_message

    ws = FakeWebSocket()
    await handle_worker_message(ws, {"type": "ping"})
    assert ws.sent == [{"type": "pong"}]


@pytest.mark.asyncio
async def test_signal_request_auto_approve(running_app):
    from coordinator.api.websocket import handle_worker_message

    ws = FakeWebSocket()
    await handle_worker_message(
        ws,
        {"type": "signal_request", "instance_id": "abc", "signal": {"action": "buy"}},
    )
    assert len(ws.sent) == 1
    resp = ws.sent[0]
    assert resp["type"] == "signal_response"
    assert resp["approved"] is True
    assert resp["instance_id"] == "abc"
    assert resp["signal"] == {"action": "buy"}


@pytest.mark.asyncio
async def test_state_checkpoint_updates_instance(running_app, algo_instance):
    from coordinator.api.websocket import handle_worker_message

    ws = FakeWebSocket()
    new_state = {"position": "long", "qty": 5}
    await handle_worker_message(
        ws,
        {"type": "state_checkpoint", "instance_id": algo_instance, "state": new_state},
    )
    # No reply expected
    assert ws.sent == []

    # Verify DB was updated
    container = get_container()
    async with container.session_factory() as session:
        result = await session.execute(
            select(AlgorithmInstance).where(AlgorithmInstance.id == algo_instance)
        )
        inst = result.scalar_one()
        assert inst.persisted_state == new_state


@pytest.mark.asyncio
async def test_decision_log_inserts_row(running_app, algo_instance):
    from coordinator.api.websocket import handle_worker_message

    ws = FakeWebSocket()
    await handle_worker_message(
        ws,
        {
            "type": "decision_log",
            "instance_id": algo_instance,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "mode": "live",
            "signals_produced": [{"symbol": "AAPL", "action": "buy"}],
            "tick_data": {"price": 150.0},
            "reasoning": {"why": "momentum"},
            "data_sources_used": {"source": "polygon"},
        },
    )
    assert ws.sent == []

    container = get_container()
    async with container.session_factory() as session:
        result = await session.execute(
            select(DecisionLog).where(DecisionLog.instance_id == algo_instance)
        )
        rows = result.scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.mode == "live"
        assert row.signals_produced == [{"symbol": "AAPL", "action": "buy"}]


@pytest.mark.asyncio
async def test_heartbeat_updates_worker(running_app, worker_and_account):
    from coordinator.api.websocket import handle_worker_message

    worker_id, _ = worker_and_account

    ws = FakeWebSocket()
    await handle_worker_message(ws, {"type": "heartbeat", "worker_id": worker_id})
    assert ws.sent == [{"type": "heartbeat_ack"}]

    container = get_container()
    async with container.session_factory() as session:
        result = await session.execute(select(Worker).where(Worker.id == worker_id))
        worker = result.scalar_one()
        assert worker.status == "online"
        assert worker.last_heartbeat is not None


@pytest.mark.asyncio
async def test_instance_started_sets_status(running_app, algo_instance):
    from coordinator.api.websocket import handle_worker_message

    ws = FakeWebSocket()
    await handle_worker_message(ws, {"type": "instance_started", "instance_id": algo_instance})

    container = get_container()
    async with container.session_factory() as session:
        result = await session.execute(
            select(AlgorithmInstance).where(AlgorithmInstance.id == algo_instance)
        )
        inst = result.scalar_one()
        assert inst.status == "running"


@pytest.mark.asyncio
async def test_instance_stopped_sets_status(running_app, algo_instance):
    from coordinator.api.websocket import handle_worker_message

    ws = FakeWebSocket()
    await handle_worker_message(ws, {"type": "instance_stopped", "instance_id": algo_instance})

    container = get_container()
    async with container.session_factory() as session:
        result = await session.execute(
            select(AlgorithmInstance).where(AlgorithmInstance.id == algo_instance)
        )
        inst = result.scalar_one()
        assert inst.status == "stopped"


@pytest.mark.asyncio
async def test_instance_error_sets_status(running_app, algo_instance):
    from coordinator.api.websocket import handle_worker_message

    ws = FakeWebSocket()
    await handle_worker_message(ws, {"type": "instance_error", "instance_id": algo_instance})

    container = get_container()
    async with container.session_factory() as session:
        result = await session.execute(
            select(AlgorithmInstance).where(AlgorithmInstance.id == algo_instance)
        )
        inst = result.scalar_one()
        assert inst.status == "error"


# ---------------------------------------------------------------------------
# Dashboard → worker relay (start/stop)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_start_forwards_to_worker(running_app, algo_instance, worker_and_account):
    from coordinator.api.websocket import handle_dashboard_message, manager

    worker_id, _ = worker_and_account

    # Set config_values + persisted_state on the instance so we can assert they're forwarded.
    container = get_container()
    async with container.session_factory() as session:
        result = await session.execute(
            select(AlgorithmInstance).where(AlgorithmInstance.id == algo_instance)
        )
        inst = result.scalar_one()
        inst.config_values = {"foo": 1}
        inst.persisted_state = {"bar": 2}
        await session.commit()

    worker_ws = FakeWebSocket()
    manager.register_worker(worker_id, worker_ws)
    try:
        dashboard_ws = FakeWebSocket()
        await handle_dashboard_message(
            dashboard_ws,
            {"type": "start_instance", "instance_id": algo_instance},
        )
        assert dashboard_ws.sent == []
        assert worker_ws.sent == [{
            "type": "start_instance",
            "instance_id": algo_instance,
            "config": {"foo": 1},
            "persisted_state": {"bar": 2},
        }]
    finally:
        manager.disconnect_worker_by_socket(worker_ws)


@pytest.mark.asyncio
async def test_dashboard_stop_forwards_to_worker(running_app, algo_instance, worker_and_account):
    from coordinator.api.websocket import handle_dashboard_message, manager

    worker_id, _ = worker_and_account
    worker_ws = FakeWebSocket()
    manager.register_worker(worker_id, worker_ws)
    try:
        dashboard_ws = FakeWebSocket()
        await handle_dashboard_message(
            dashboard_ws,
            {"type": "stop_instance", "instance_id": algo_instance},
        )
        assert dashboard_ws.sent == []
        assert worker_ws.sent == [{
            "type": "stop_instance",
            "instance_id": algo_instance,
        }]
    finally:
        manager.disconnect_worker_by_socket(worker_ws)


@pytest.mark.asyncio
async def test_dashboard_start_errors_when_worker_offline(running_app, algo_instance):
    from coordinator.api.websocket import handle_dashboard_message

    dashboard_ws = FakeWebSocket()
    await handle_dashboard_message(
        dashboard_ws,
        {"type": "start_instance", "instance_id": algo_instance},
    )
    assert len(dashboard_ws.sent) == 1
    msg = dashboard_ws.sent[0]
    assert msg["type"] == "error"
    assert msg["related_to"] == "start_instance"
    assert msg["error"] == "worker offline"


@pytest.mark.asyncio
async def test_dashboard_start_errors_when_instance_missing(running_app):
    from coordinator.api.websocket import handle_dashboard_message

    dashboard_ws = FakeWebSocket()
    await handle_dashboard_message(
        dashboard_ws,
        {"type": "start_instance", "instance_id": "does-not-exist"},
    )
    assert len(dashboard_ws.sent) == 1
    assert dashboard_ws.sent[0]["error"] == "instance not found"


@pytest.mark.asyncio
async def test_heartbeat_registers_worker_in_connection_map(running_app, worker_and_account):
    from coordinator.api.websocket import handle_worker_message, manager

    worker_id, _ = worker_and_account
    ws = FakeWebSocket()
    await handle_worker_message(ws, {"type": "heartbeat", "worker_id": worker_id})
    try:
        assert manager.worker_connections.get(worker_id) is ws
    finally:
        manager.disconnect_worker_by_socket(ws)


@pytest.mark.asyncio
async def test_worker_marked_offline_on_disconnect(running_app, db_session):
    from coordinator.api.websocket import manager, handle_worker_disconnect
    worker = Worker(name="w", status="online")
    db_session.add(worker)
    await db_session.flush()
    await db_session.commit()
    wid = worker.id

    fake = FakeWebSocket()
    manager.register_worker(wid, fake)
    await handle_worker_disconnect(fake)

    # Verify the row is now offline (use a fresh query — the session-cached
    # row may need a refresh; safest is a new session via container)
    from coordinator.api.dependencies import get_container
    container = get_container()
    async with container.session_factory() as session:
        w = (await session.execute(select(Worker).where(Worker.id == wid))).scalar_one()
        assert w.status == "offline"
