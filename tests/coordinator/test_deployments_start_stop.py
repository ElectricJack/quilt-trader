import json as _json
import pytest
from sqlalchemy import select
from unittest.mock import MagicMock, AsyncMock

from coordinator.api.websocket import manager
from coordinator.database.models import (
    Worker, Account, Algorithm, AlgorithmInstance, AlgorithmRun,
)


class FakeWorkerWS:
    def __init__(self):
        self.sent = []
    async def send_json(self, data):
        self.sent.append(data)


def _make_encrypted_creds(container, d: dict) -> str:
    """Return encryption.encrypt(json.dumps(d)) using the app's container."""
    return container.encryption.encrypt(_json.dumps(d))


@pytest.mark.asyncio
async def test_start_writes_starting_status_and_creates_run_immediately(client, db_session):
    from coordinator.api.dependencies import get_container
    import pathlib

    container = get_container()
    algo = Algorithm(repo_url="https://github.com/x/test-algo", name="A", commit_hash="sha-xyz")
    acct = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials=_make_encrypted_creds(container, {"api_key": "k", "secret_key": "s"}),
        supported_asset_types=["equities"],
    )
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

    pkg_dir = pathlib.Path("data/packages/test-algo")
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "quilt.yaml").write_text(
        "name: A\ntype: algorithm\nversion: 1.0.0\nentry_point: test_algo.algorithm\n"
        "class_name: TestAlgo\ntrigger: bar:1min\n"
        "requirements:\n  asset_types: [equities]\n  data_dependencies: []\n"
    )

    fake_ws = FakeWorkerWS()
    manager.register_worker(wid, fake_ws)
    try:
        r = await client.post(f"/api/deployments/{did}/start")
    finally:
        manager.worker_connections.pop(wid, None)
        (pkg_dir / "quilt.yaml").unlink(missing_ok=True)
        try:
            pkg_dir.rmdir()
        except Exception:
            pass

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["active_run_id"]

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
    from coordinator.api.dependencies import get_container

    container = get_container()
    algo = Algorithm(repo_url="x", name="A")
    acct = Account(
        name="A", broker_type="alpaca",
        credentials=_make_encrypted_creds(container, {}),
        supported_asset_types=["equities"],
    )
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

    async with container.session_factory() as session:
        i = (await session.execute(
            select(AlgorithmInstance).where(AlgorithmInstance.id == did)
        )).scalar_one()
        assert i.status == "stopped"


@pytest.mark.asyncio
async def test_start_returns_409_when_not_in_startable_state(client, db_session):
    from coordinator.api.dependencies import get_container

    container = get_container()
    algo = Algorithm(repo_url="x", name="A")
    acct = Account(
        name="A", broker_type="alpaca",
        credentials=_make_encrypted_creds(container, {}),
        supported_asset_types=["equities"],
    )
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
    from coordinator.api.dependencies import get_container

    container = get_container()
    algo = Algorithm(repo_url="x", name="A")
    acct = Account(
        name="A", broker_type="alpaca",
        credentials=_make_encrypted_creds(container, {}),
        supported_asset_types=["equities"],
    )
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

    async with container.session_factory() as session:
        i = (await session.execute(
            select(AlgorithmInstance).where(AlgorithmInstance.id == did)
        )).scalar_one()
        assert i.status == "stopping"


@pytest.mark.asyncio
async def test_start_instance_payload_includes_run_id_manifest_and_credentials(client, db_session, tmp_path, monkeypatch):
    from coordinator.api.dependencies import get_container

    algo = Algorithm(repo_url="https://github.com/x/test-algo", name="A", commit_hash="sha-abc")
    encryption = get_container().encryption
    acct = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials=encryption.encrypt(_json.dumps({"api_key": "k", "secret_key": "s"})),
        supported_asset_types=["equities"],
    )
    worker = Worker(name="W", status="online")
    db_session.add_all([algo, acct, worker])
    await db_session.flush()
    inst = AlgorithmInstance(
        algorithm_id=algo.id, account_id=acct.id,
        worker_id=worker.id, status="stopped",
    )
    db_session.add(inst)
    await db_session.commit()

    # Stash manifest on disk so the endpoint can read it.
    import pathlib
    pkg_dir = pathlib.Path("data/packages/test-algo")
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "quilt.yaml").write_text(
        """
name: A
type: algorithm
version: 1.0.0
entry_point: test_algo.algorithm
class_name: TestAlgo
trigger: bar:1min
requirements:
  asset_types: [equities]
  data_dependencies:
    - { symbol: "AAPL", timeframe: "1min" }
""".strip()
    )

    fake_ws = MagicMock()
    fake_ws.send_json = AsyncMock()
    manager.register_worker(worker.id, fake_ws)
    try:
        r = await client.post(f"/api/deployments/{inst.id}/start")
    finally:
        manager.worker_connections.pop(worker.id, None)
        (pkg_dir / "quilt.yaml").unlink(missing_ok=True)
        try:
            pkg_dir.rmdir()
        except Exception:
            pass

    assert r.status_code == 200
    sent = fake_ws.send_json.call_args.args[0]
    assert sent["type"] == "start_instance"
    assert sent["run_id"]
    assert sent["algorithm_id"] == algo.id
    assert sent["algorithm_commit_sha"] == "sha-abc"
    assert sent["broker_type"] == "alpaca"
    assert sent["environment"] == "paper"
    assert sent["credentials"]["api_key"] == "k"
    assert sent["manifest"]["entry_point"] == "test_algo.algorithm"
    assert sent["manifest"]["trigger"] == "bar:1min"
