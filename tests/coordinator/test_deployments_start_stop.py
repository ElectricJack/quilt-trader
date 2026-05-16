import pytest
from sqlalchemy import select

from coordinator.api.websocket import manager
from coordinator.database.models import (
    Worker, Account, Algorithm, AlgorithmInstance, AlgorithmRun,
)


class FakeWorkerWS:
    def __init__(self):
        self.sent = []
    async def send_json(self, data):
        self.sent.append(data)


@pytest.mark.asyncio
async def test_start_writes_starting_status_and_creates_run_immediately(client, db_session):
    algo = Algorithm(repo_url="x", name="A")
    acct = Account(name="A", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
    worker = Worker(name="W", status="online")
    db_session.add_all([algo, acct, worker])
    await db_session.flush()
    inst = AlgorithmInstance(
        algorithm_id=algo.id, account_id=acct.id,
        worker_id=worker.id, status="stopped",
    )
    db_session.add(inst)
    await db_session.commit()
    wid, did = worker.id, inst.id

    fake_ws = FakeWorkerWS()
    manager.register_worker(wid, fake_ws)
    try:
        r = await client.post(f"/api/deployments/{did}/start")
    finally:
        manager.worker_connections.pop(wid, None)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["active_run_id"]

    from coordinator.api.dependencies import get_container
    container = get_container()
    async with container.session_factory() as session:
        i = (await session.execute(
            select(AlgorithmInstance).where(AlgorithmInstance.id == did)
        )).scalar_one()
        assert i.status == "starting"
        assert i.active_run_id is not None
        run = (await session.execute(
            select(AlgorithmRun).where(AlgorithmRun.id == i.active_run_id)
        )).scalar_one()
        assert run.status == "running"
        assert run.run_number == 1
    assert any(m["type"] == "start_instance" for m in fake_ws.sent)


@pytest.mark.asyncio
async def test_start_when_worker_offline_returns_502_and_leaves_status_stopped(client, db_session):
    algo = Algorithm(repo_url="x", name="A")
    acct = Account(name="A", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
    worker = Worker(name="W", status="offline")
    db_session.add_all([algo, acct, worker])
    await db_session.flush()
    inst = AlgorithmInstance(
        algorithm_id=algo.id, account_id=acct.id,
        worker_id=worker.id, status="stopped",
    )
    db_session.add(inst)
    await db_session.commit()
    did = inst.id

    r = await client.post(f"/api/deployments/{did}/start")
    assert r.status_code == 502

    from coordinator.api.dependencies import get_container
    container = get_container()
    async with container.session_factory() as session:
        i = (await session.execute(
            select(AlgorithmInstance).where(AlgorithmInstance.id == did)
        )).scalar_one()
        assert i.status == "stopped"


@pytest.mark.asyncio
async def test_start_returns_409_when_not_in_startable_state(client, db_session):
    algo = Algorithm(repo_url="x", name="A")
    acct = Account(name="A", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
    worker = Worker(name="W", status="online")
    db_session.add_all([algo, acct, worker])
    await db_session.flush()
    inst = AlgorithmInstance(
        algorithm_id=algo.id, account_id=acct.id,
        worker_id=worker.id, status="running",
    )
    db_session.add(inst)
    await db_session.commit()

    fake = FakeWorkerWS()
    manager.register_worker(worker.id, fake)
    try:
        r = await client.post(f"/api/deployments/{inst.id}/start")
    finally:
        manager.worker_connections.pop(worker.id, None)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_stop_marks_stopping_and_sends_to_worker(client, db_session):
    algo = Algorithm(repo_url="x", name="A")
    acct = Account(name="A", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
    worker = Worker(name="W", status="online")
    db_session.add_all([algo, acct, worker])
    await db_session.flush()
    inst = AlgorithmInstance(
        algorithm_id=algo.id, account_id=acct.id,
        worker_id=worker.id, status="running",
    )
    db_session.add(inst)
    await db_session.commit()
    did = inst.id

    fake = FakeWorkerWS()
    manager.register_worker(worker.id, fake)
    try:
        r = await client.post(f"/api/deployments/{did}/stop")
    finally:
        manager.worker_connections.pop(worker.id, None)
    assert r.status_code == 200
    assert any(m["type"] == "stop_instance" for m in fake.sent)

    from coordinator.api.dependencies import get_container
    container = get_container()
    async with container.session_factory() as session:
        i = (await session.execute(
            select(AlgorithmInstance).where(AlgorithmInstance.id == did)
        )).scalar_one()
        assert i.status == "stopping"
