import pytest
from unittest.mock import AsyncMock, MagicMock
from coordinator.api.websocket import handle_worker_message, manager
from coordinator.database.models import Worker


@pytest.mark.asyncio
async def test_heartbeat_updates_status_and_broadcasts_on_transition(test_app, db_session):
    worker = Worker(id="w-1", name="pi-1", tailscale_ip=None,
                    status="offline", install_status="pending")
    db_session.add(worker); await db_session.flush()

    ws = AsyncMock()
    broadcasts = []
    async def fake_broadcast(msg): broadcasts.append(msg)
    manager.broadcast_to_dashboards = fake_broadcast

    await handle_worker_message(ws, {
        "type": "heartbeat", "worker_id": "w-1",
        "tailscale_ip": "100.64.0.5",
    })

    # Re-read
    from coordinator.api.dependencies import get_container
    container = get_container()
    async with container.session_factory() as session:
        from sqlalchemy import select
        w = (await session.execute(
            select(Worker).where(Worker.id == "w-1")
        )).scalar_one()
        assert w.status == "online"
        assert w.tailscale_ip == "100.64.0.5"
        assert w.last_heartbeat is not None
    assert len(broadcasts) == 1
    assert broadcasts[0]["type"] == "worker_connected"
    assert broadcasts[0]["worker_id"] == "w-1"


@pytest.mark.asyncio
async def test_heartbeat_does_not_rebroadcast_when_already_online(test_app, db_session):
    worker = Worker(id="w-2", name="pi-2", tailscale_ip="100.64.0.6",
                    status="online", install_status="claimed")
    db_session.add(worker); await db_session.flush()

    ws = AsyncMock()
    broadcasts = []
    async def fake_broadcast(msg): broadcasts.append(msg)
    manager.broadcast_to_dashboards = fake_broadcast

    await handle_worker_message(ws, {"type": "heartbeat", "worker_id": "w-2"})
    assert len(broadcasts) == 0
