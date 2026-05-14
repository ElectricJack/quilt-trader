import pytest
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_positions_empty(client):
    response = await client.get("/api/positions")
    assert response.status_code == 200
    assert response.json() == {"items": []}


@pytest.mark.asyncio
async def test_positions_open_only(client, db_session):
    from coordinator.database.models import (
        Account, Algorithm, AlgorithmInstance, Worker, Position,
    )

    acct = Account(name="A", broker_type="alpaca", supported_asset_types=["equities"], pdt_mode="off", credentials="{}")
    db_session.add(acct)
    await db_session.flush()
    worker = Worker(name="w1", tailscale_ip="1.1.1.1", status="active", max_algorithms=5)
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

    db_session.add(Position(
        account_id=acct.id, instance_id=inst.id, status="open",
        legs=[{"symbol": "BTC", "side": "long", "quantity": 0.5,
               "avg_price": 68000.0, "current_price": 70000.0, "asset_type": "crypto"}],
        unrealized_pnl=1000.0, net_cost=34000.0,
    ))
    db_session.add(Position(
        account_id=acct.id, instance_id=inst.id, status="closed",
        legs=[], net_cost=0.0,
    ))
    await db_session.commit()

    response = await client.get("/api/positions?status=open")
    body = response.json()
    assert len(body["items"]) == 1
    row = body["items"][0]
    assert row["symbol"] == "BTC"
    assert row["side"] == "long"
    assert row["quantity"] == 0.5
    assert row["algorithm_name"] == "momentum-btc"
    assert row["instance_id"] == inst.id
