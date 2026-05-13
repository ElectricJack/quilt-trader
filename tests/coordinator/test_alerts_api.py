import pytest
from datetime import datetime, timedelta, timezone


@pytest.mark.asyncio
async def test_alerts_empty(client):
    response = await client.get("/api/alerts")
    assert response.status_code == 200
    assert response.json() == {"items": []}


@pytest.mark.asyncio
async def test_alerts_combines_events_and_backtests(client, db_session):
    from coordinator.database.models import (
        Account, Algorithm, AlgorithmInstance, Worker, Event, BacktestComparison,
    )

    acct = Account(name="A", broker_type="alpaca", supported_asset_types=["equities"], pdt_mode="off", credentials="{}")
    db_session.add(acct)
    worker = Worker(name="pi-alpha", tailscale_ip="1.1.1.1", status="active", max_algorithms=1)
    db_session.add(worker)
    algo = Algorithm(repo_url="x", name="momentum-btc", install_status="installed")
    db_session.add(algo)
    await db_session.flush()
    inst = AlgorithmInstance(
        algorithm_id=algo.id, account_id=acct.id, worker_id=worker.id,
        status="running", state_stale=False,
    )
    db_session.add(inst)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    db_session.add(Event(
        source_type="instance", source_id=inst.id,
        event_type="signal_rejected", severity="warning",
        timestamp=now,
    ))
    db_session.add(Event(
        source_type="worker", source_id=worker.id,
        event_type="worker_disconnected", severity="error",
        timestamp=now - timedelta(minutes=5),
    ))
    db_session.add(BacktestComparison(
        instance_id=inst.id, algorithm_id=algo.id,
        time_range_start=now - timedelta(days=1),
        time_range_end=now,
        total_ticks=100, matching_ticks=87, match_percentage=87.0,
    ))
    await db_session.commit()

    response = await client.get("/api/alerts")
    body = response.json()
    assert len(body["items"]) == 3

    sources = {item["source_name"] for item in body["items"]}
    assert "momentum-btc" in sources
    assert "pi-alpha" in sources
    for item in body["items"]:
        assert item["link_path"] is not None
