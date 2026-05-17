import pytest


@pytest.mark.asyncio
async def test_get_report_returns_404_when_no_report(client, db_session):
    from coordinator.database.models import Algorithm, Account, Worker, AlgorithmInstance
    algo = Algorithm(repo_url="x", name="A")
    acct = Account(name="A", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
    w = Worker(name="W", status="online")
    db_session.add_all([algo, acct, w])
    await db_session.flush()
    inst = AlgorithmInstance(algorithm_id=algo.id, account_id=acct.id, worker_id=w.id, status="stopped")
    db_session.add(inst)
    await db_session.commit()
    did = inst.id

    r = await client.get(f"/api/deployments/{did}/report")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_report_returns_payload_when_present(client, db_session):
    from coordinator.database.models import (
        Algorithm, Account, Worker, AlgorithmInstance, AlgorithmDeploymentReport,
    )
    algo = Algorithm(repo_url="x", name="A")
    acct = Account(name="A", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
    w = Worker(name="W", status="online")
    db_session.add_all([algo, acct, w])
    await db_session.flush()
    inst = AlgorithmInstance(algorithm_id=algo.id, account_id=acct.id, worker_id=w.id, status="running")
    db_session.add(inst)
    await db_session.flush()
    db_session.add(AlgorithmDeploymentReport(
        deployment_id=inst.id,
        total_return=0.1, sharpe_ratio=1.5,
        equity_curve=[{"timestamp": "2026-05-16T12:00:00Z", "portfolio_value": 110.0}],
        key_metrics={"strategy": {"cagr": 0.2}},
        runs_index=[{"run_id": "r1", "run_number": 1, "status": "running"}],
    ))
    await db_session.commit()

    r = await client.get(f"/api/deployments/{inst.id}/report")
    body = r.json()
    assert r.status_code == 200
    assert body["deployment_id"] == inst.id
    assert body["total_return"] == 0.1
    assert body["sharpe_ratio"] == 1.5
    assert body["equity_curve"][0]["portfolio_value"] == 110.0
    assert body["key_metrics"]["strategy"]["cagr"] == 0.2
    assert body["runs_index"][0]["run_id"] == "r1"


@pytest.mark.asyncio
async def test_list_deployment_trades_filters_by_instance(client, db_session):
    from datetime import datetime, timezone, timedelta
    from coordinator.database.models import (
        Algorithm, Account, Worker, AlgorithmInstance, TradeLog,
    )
    algo = Algorithm(repo_url="x", name="A")
    acct = Account(name="A", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
    w = Worker(name="W", status="online")
    db_session.add_all([algo, acct, w])
    await db_session.flush()
    inst = AlgorithmInstance(algorithm_id=algo.id, account_id=acct.id, worker_id=w.id, status="running")
    db_session.add(inst)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add_all([
        TradeLog(
            instance_id=inst.id, account_id=acct.id, source="live",
            symbol="AAPL", side="buy", quantity=10, filled_price=100.0,
            timestamp=now - timedelta(minutes=2),
        ),
        TradeLog(
            instance_id=inst.id, account_id=acct.id, source="live",
            symbol="MSFT", side="buy", quantity=5, filled_price=300.0,
            timestamp=now - timedelta(minutes=1),
        ),
        TradeLog(
            instance_id=None, account_id=acct.id, source="manual",
            symbol="OTHER", side="buy", quantity=1, filled_price=10.0,
            timestamp=now,
        ),
    ])
    await db_session.commit()

    r = await client.get(f"/api/deployments/{inst.id}/trades")
    body = r.json()
    items = body["items"]
    # Newest-first: MSFT then AAPL. OTHER is excluded (no instance_id).
    assert [t["symbol"] for t in items] == ["MSFT", "AAPL"]
