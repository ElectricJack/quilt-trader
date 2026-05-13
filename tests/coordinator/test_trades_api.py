import pytest
from datetime import datetime, timedelta, timezone


@pytest.mark.asyncio
async def test_trades_empty(client):
    response = await client.get("/api/trades")
    assert response.status_code == 200
    assert response.json() == {"items": []}


@pytest.mark.asyncio
async def test_trades_sorted_desc_with_algo_name(client, db_session):
    from coordinator.database.models import (
        Account, Algorithm, AlgorithmInstance, Worker, TradeLog,
    )

    acct = Account(name="A", broker_type="alpaca", supported_asset_types=["equities"], pdt_mode="off", credentials="{}")
    db_session.add(acct)
    worker = Worker(name="w", tailscale_ip="1.1.1.1", status="active", max_algorithms=1)
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
    db_session.add(TradeLog(
        account_id=acct.id, instance_id=inst.id, source="alpaca",
        symbol="BTC", side="buy", quantity=0.05, filled_price=70000.0,
        timestamp=now - timedelta(minutes=5),
    ))
    db_session.add(TradeLog(
        account_id=acct.id, instance_id=inst.id, source="alpaca",
        symbol="ETH", side="sell", quantity=1.0, filled_price=3200.0,
        timestamp=now,
    ))
    await db_session.commit()

    response = await client.get("/api/trades?limit=10")
    body = response.json()
    assert len(body["items"]) == 2
    assert body["items"][0]["symbol"] == "ETH"  # newest first
    assert body["items"][0]["algorithm_name"] == "momentum-btc"
    assert body["items"][0]["notional"] == 3200.0
