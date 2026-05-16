import pytest
from datetime import datetime, timezone, timedelta

from coordinator.database.models import Worker, WorkerActivity


@pytest.mark.asyncio
async def test_list_worker_activity_newest_first_with_severity_filter(client, db_session):
    w = Worker(name="w", status="online")
    db_session.add(w)
    await db_session.flush()
    t0 = datetime.now(timezone.utc)
    db_session.add_all([
        WorkerActivity(worker_id=w.id, kind="event", event_type="x", severity="debug", timestamp=t0 - timedelta(seconds=30)),
        WorkerActivity(worker_id=w.id, kind="event", event_type="y", severity="info",  timestamp=t0 - timedelta(seconds=20)),
        WorkerActivity(worker_id=w.id, kind="event", event_type="z", severity="error", timestamp=t0 - timedelta(seconds=10)),
    ])
    await db_session.commit()
    wid = w.id

    r = await client.get(f"/api/workers/{wid}/activity?severity=info")
    rows = r.json()["items"]
    assert [r_["event_type"] for r_ in rows] == ["z", "y"]


@pytest.mark.asyncio
async def test_list_worker_activity_kind_filter(client, db_session):
    w = Worker(name="w", status="online")
    db_session.add(w)
    await db_session.flush()
    t0 = datetime.now(timezone.utc)
    db_session.add_all([
        WorkerActivity(worker_id=w.id, kind="event", event_type="evt", severity="info", timestamp=t0 - timedelta(seconds=20)),
        WorkerActivity(worker_id=w.id, kind="log",   logger_name="a", severity="info", message="m", timestamp=t0 - timedelta(seconds=10)),
    ])
    await db_session.commit()

    r = await client.get(f"/api/workers/{w.id}/activity?kind=log")
    rows = r.json()["items"]
    assert len(rows) == 1
    assert rows[0]["kind"] == "log"


@pytest.mark.asyncio
async def test_list_worker_activity_event_types_csv(client, db_session):
    w = Worker(name="w", status="online")
    db_session.add(w)
    await db_session.flush()
    t0 = datetime.now(timezone.utc)
    db_session.add_all([
        WorkerActivity(worker_id=w.id, kind="event", event_type="a", severity="info", timestamp=t0 - timedelta(seconds=30)),
        WorkerActivity(worker_id=w.id, kind="event", event_type="b", severity="info", timestamp=t0 - timedelta(seconds=20)),
        WorkerActivity(worker_id=w.id, kind="event", event_type="c", severity="info", timestamp=t0 - timedelta(seconds=10)),
    ])
    await db_session.commit()

    r = await client.get(f"/api/workers/{w.id}/activity?event_types=a,c")
    rows = r.json()["items"]
    assert sorted(r_["event_type"] for r_ in rows) == ["a", "c"]


@pytest.mark.asyncio
async def test_list_worker_activity_before_cursor(client, db_session):
    w = Worker(name="w", status="online")
    db_session.add(w)
    await db_session.flush()
    t0 = datetime.now(timezone.utc)
    db_session.add_all([
        WorkerActivity(worker_id=w.id, kind="event", event_type="x", severity="info", timestamp=t0 - timedelta(seconds=30)),
        WorkerActivity(worker_id=w.id, kind="event", event_type="y", severity="info", timestamp=t0 - timedelta(seconds=10)),
    ])
    await db_session.commit()

    cutoff = (t0 - timedelta(seconds=20)).isoformat().replace("+00:00", "Z")
    r = await client.get(f"/api/workers/{w.id}/activity?before={cutoff}")
    rows = r.json()["items"]
    assert [r_["event_type"] for r_ in rows] == ["x"]


@pytest.mark.asyncio
async def test_list_deployment_activity_filters_by_instance(client, db_session):
    from coordinator.database.models import Algorithm, Account, AlgorithmInstance
    algo = Algorithm(repo_url="x", name="A")
    acct = Account(name="A", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
    w = Worker(name="W", status="online")
    db_session.add_all([algo, acct, w])
    await db_session.flush()
    inst = AlgorithmInstance(algorithm_id=algo.id, account_id=acct.id, worker_id=w.id, status="running")
    db_session.add(inst)
    await db_session.flush()
    db_session.add_all([
        WorkerActivity(worker_id=w.id, instance_id=inst.id, kind="event", event_type="a", severity="info"),
        WorkerActivity(worker_id=w.id, instance_id=None,    kind="event", event_type="b", severity="info"),
    ])
    await db_session.commit()

    r = await client.get(f"/api/deployments/{inst.id}/activity")
    rows = r.json()["items"]
    assert [r_["event_type"] for r_ in rows] == ["a"]
