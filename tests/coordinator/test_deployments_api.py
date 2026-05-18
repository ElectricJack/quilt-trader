import pytest
from coordinator.database.models import Worker, Account, Algorithm, AlgorithmInstance


@pytest.mark.asyncio
async def test_list_deployments_includes_hydrated_names(client, db_session):
    algo = Algorithm(repo_url="x", name="TrendBot")
    acct = Account(name="Paper-1", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
    worker = Worker(name="Pi-1", status="online")
    db_session.add_all([algo, acct, worker])
    await db_session.flush()
    inst = AlgorithmInstance(algorithm_id=algo.id, account_id=acct.id, worker_id=worker.id, status="stopped")
    db_session.add(inst)
    await db_session.commit()

    r = await client.get("/api/deployments")
    body = r.json()
    assert r.status_code == 200
    assert len(body) == 1
    d = body[0]
    assert d["algorithm_name"] == "TrendBot"
    assert d["account_name"] == "Paper-1"
    assert d["worker_name"] == "Pi-1"
    assert d["status"] == "stopped"


@pytest.mark.asyncio
async def test_list_deployments_filters_by_worker_id(client, db_session):
    algo = Algorithm(repo_url="x", name="A")
    acct = Account(name="Acc", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
    w1 = Worker(name="W1", status="online")
    w2 = Worker(name="W2", status="online")
    db_session.add_all([algo, acct, w1, w2])
    await db_session.flush()
    inst1 = AlgorithmInstance(algorithm_id=algo.id, account_id=acct.id, worker_id=w1.id, status="stopped")
    inst2 = AlgorithmInstance(algorithm_id=algo.id, account_id=acct.id, worker_id=w2.id, status="stopped")
    db_session.add_all([inst1, inst2])
    await db_session.commit()

    r = await client.get(f"/api/deployments?worker_id={w1.id}")
    body = r.json()
    assert len(body) == 1
    assert body[0]["worker_name"] == "W1"


@pytest.mark.asyncio
async def test_get_deployment_returns_404_for_unknown_id(client):
    r = await client.get("/api/deployments/nope")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_deployment_returns_hydrated_response(client, db_session):
    algo = Algorithm(repo_url="x", name="TrendBot")
    acct = Account(name="Paper", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
    worker = Worker(name="Pi-1", status="online")
    db_session.add_all([algo, acct, worker])
    await db_session.flush()
    inst = AlgorithmInstance(algorithm_id=algo.id, account_id=acct.id, worker_id=worker.id, status="stopped")
    db_session.add(inst)
    await db_session.commit()

    r = await client.get(f"/api/deployments/{inst.id}")
    body = r.json()
    assert r.status_code == 200
    assert body["algorithm_name"] == "TrendBot"
    assert body["account_name"] == "Paper"
    assert body["worker_name"] == "Pi-1"


@pytest.mark.asyncio
async def test_list_runs_for_deployment(client, db_session):
    from coordinator.database.models import AlgorithmRun
    algo = Algorithm(repo_url="x", name="A")
    acct = Account(name="A", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
    w = Worker(name="W", status="online")
    db_session.add_all([algo, acct, w])
    await db_session.flush()
    inst = AlgorithmInstance(algorithm_id=algo.id, account_id=acct.id, worker_id=w.id, status="stopped")
    db_session.add(inst)
    await db_session.flush()
    db_session.add_all([
        AlgorithmRun(instance_id=inst.id, run_number=1, status="stopped"),
        AlgorithmRun(instance_id=inst.id, run_number=2, status="running"),
    ])
    await db_session.commit()

    r = await client.get(f"/api/deployments/{inst.id}/runs")
    body = r.json()
    assert len(body) == 2
    # Newest run_number first
    assert body[0]["run_number"] == 2


@pytest.mark.asyncio
async def test_delete_deployment_cascades_runs_and_persists(client, db_session):
    """DELETE must actually remove the row + cascade child runs.

    Regression: previously the route returned 204 but the IntegrityError on
    the AlgorithmRun.instance_id NOT NULL constraint was swallowed after the
    response was sent, leaving the deployment row in the DB."""
    from sqlalchemy import select
    from coordinator.database.models import AlgorithmRun

    algo = Algorithm(repo_url="x", name="X")
    acct = Account(name="A", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
    worker = Worker(name="W", status="online")
    db_session.add_all([algo, acct, worker])
    await db_session.flush()
    inst = AlgorithmInstance(
        algorithm_id=algo.id, account_id=acct.id, worker_id=worker.id, status="stopped",
    )
    db_session.add(inst)
    await db_session.flush()
    run = AlgorithmRun(instance_id=inst.id, run_number=1, status="stopped")
    db_session.add(run)
    await db_session.commit()

    inst_id = inst.id

    r = await client.delete(f"/api/deployments/{inst_id}")
    assert r.status_code == 204

    db_session.expire_all()
    assert (await db_session.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.id == inst_id)
    )).scalar_one_or_none() is None
    # Runs are cascaded (the dialog tells the user "Run history will also be removed").
    assert (await db_session.execute(
        select(AlgorithmRun).where(AlgorithmRun.instance_id == inst_id)
    )).scalar_one_or_none() is None
