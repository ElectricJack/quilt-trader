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
        database_url="sqlite+aiosqlite://",
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
