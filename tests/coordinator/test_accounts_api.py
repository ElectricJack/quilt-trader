import pytest


@pytest.mark.asyncio
async def test_create_account(client):
    response = await client.post("/api/accounts", json={
        "name": "Alpaca Main",
        "broker_type": "alpaca",
        "credentials": {"api_key": "pk_123", "api_secret": "sk_456"},
        "supported_asset_types": ["equities", "options", "crypto"],
        "options_level": 3,
        "account_features": ["margin"],
        "pdt_mode": "warn",
    })
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Alpaca Main"
    assert body["broker_type"] == "alpaca"
    assert body["supported_asset_types"] == ["equities", "options", "crypto"]
    assert body["options_level"] == 3
    assert body["pdt_mode"] == "warn"
    assert "id" in body
    assert "credentials" not in body


@pytest.mark.asyncio
async def test_list_accounts(client):
    await client.post("/api/accounts", json={
        "name": "Account 1",
        "broker_type": "alpaca",
        "credentials": {"api_key": "k1"},
        "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    await client.post("/api/accounts", json={
        "name": "Account 2",
        "broker_type": "tradier",
        "credentials": {"api_key": "k2"},
        "supported_asset_types": ["equities", "options"],
        "pdt_mode": "block",
    })
    response = await client.get("/api/accounts")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2


@pytest.mark.asyncio
async def test_get_account(client):
    create_resp = await client.post("/api/accounts", json={
        "name": "Get Test",
        "broker_type": "alpaca",
        "credentials": {"api_key": "k"},
        "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    account_id = create_resp.json()["id"]
    response = await client.get(f"/api/accounts/{account_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Get Test"


@pytest.mark.asyncio
async def test_get_account_not_found(client):
    response = await client.get("/api/accounts/nonexistent-id")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_account(client):
    create_resp = await client.post("/api/accounts", json={
        "name": "Before Update",
        "broker_type": "alpaca",
        "credentials": {"api_key": "k"},
        "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    account_id = create_resp.json()["id"]
    response = await client.patch(f"/api/accounts/{account_id}", json={
        "name": "After Update",
        "pdt_mode": "block",
    })
    assert response.status_code == 200
    assert response.json()["name"] == "After Update"
    assert response.json()["pdt_mode"] == "block"


@pytest.mark.asyncio
async def test_delete_account(client):
    create_resp = await client.post("/api/accounts", json={
        "name": "To Delete",
        "broker_type": "alpaca",
        "credentials": {"api_key": "k"},
        "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    account_id = create_resp.json()["id"]
    response = await client.delete(f"/api/accounts/{account_id}")
    assert response.status_code == 204

    get_resp = await client.get(f"/api/accounts/{account_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_account_with_dependents(client, db_session):
    """DELETE /api/accounts/:id should cascade-delete all dependent rows."""
    from coordinator.database.models import (
        Account,
        AccountCashFlow,
        AccountSnapshot,
        Algorithm,
        AlgorithmInstance,
        AlgorithmRun,
        Position,
        TradeLog,
        Worker,
    )
    from sqlalchemy import select

    # Seed supporting rows.
    algo = Algorithm(
        repo_url="https://github.com/example/algo",
        name="TestAlgo",
        install_status="installed",
    )
    worker = Worker(name="w1", tailscale_ip="100.64.0.1", status="online")
    db_session.add_all([algo, worker])
    await db_session.flush()

    acct = Account(
        name="Cascade Test",
        broker_type="alpaca",
        supported_asset_types=["equities"],
        pdt_mode="off",
        credentials="{}",
    )
    db_session.add(acct)
    await db_session.flush()

    instance = AlgorithmInstance(
        algorithm_id=algo.id,
        account_id=acct.id,
        worker_id=worker.id,
        status="stopped",
    )
    db_session.add(instance)
    await db_session.flush()

    run = AlgorithmRun(instance_id=instance.id, run_number=1, status="completed")
    db_session.add(run)
    await db_session.flush()

    snap = AccountSnapshot(
        account_id=acct.id,
        total_value=50000.0,
        cash=20000.0,
        positions_value=30000.0,
        source="seed",
    )
    trade = TradeLog(
        account_id=acct.id,
        instance_id=instance.id,
        source="algo",
        symbol="AAPL",
        side="buy",
        quantity=10.0,
        filled_price=150.0,
    )
    position = Position(
        account_id=acct.id,
        instance_id=instance.id,
        legs=[],
        status="open",
        net_cost=1500.0,
    )
    cash_flow = AccountCashFlow(account_id=acct.id, type="deposit", amount=5000.0)
    db_session.add_all([snap, trade, position, cash_flow])
    await db_session.commit()

    account_id = acct.id
    instance_id = instance.id

    response = await client.delete(f"/api/accounts/{account_id}")
    assert response.status_code == 204

    # Verify the account is gone.
    get_resp = await client.get(f"/api/accounts/{account_id}")
    assert get_resp.status_code == 404

    # Verify all dependent rows were cleaned up.
    assert (await db_session.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.account_id == account_id)
    )).scalar_one_or_none() is None

    assert (await db_session.execute(
        select(AlgorithmRun).where(AlgorithmRun.instance_id == instance_id)
    )).scalar_one_or_none() is None

    assert (await db_session.execute(
        select(AccountSnapshot).where(AccountSnapshot.account_id == account_id)
    )).scalar_one_or_none() is None

    assert (await db_session.execute(
        select(TradeLog).where(TradeLog.account_id == account_id)
    )).scalar_one_or_none() is None

    assert (await db_session.execute(
        select(Position).where(Position.account_id == account_id)
    )).scalar_one_or_none() is None

    assert (await db_session.execute(
        select(AccountCashFlow).where(AccountCashFlow.account_id == account_id)
    )).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_accounts_snapshots_latest(client, db_session):
    from datetime import datetime, timedelta, timezone
    from coordinator.database.models import Account, AccountSnapshot

    acct = Account(
        name="Alpaca Main", broker_type="alpaca",
        supported_asset_types=["equities"], pdt_mode="off",
        credentials="{}",
    )
    db_session.add(acct)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add(AccountSnapshot(
        account_id=acct.id, timestamp=now - timedelta(hours=25),
        total_value=10000.0, cash=4000.0, positions_value=6000.0,
        source="seed",
    ))
    db_session.add(AccountSnapshot(
        account_id=acct.id, timestamp=now,
        total_value=10500.0, cash=4000.0, positions_value=6500.0,
        source="seed",
    ))
    await db_session.commit()

    response = await client.get("/api/accounts/snapshots/latest")
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["account_name"] == "Alpaca Main"
    assert item["latest"]["total_value"] == 10500.0
    assert item["prior"]["total_value"] == 10000.0
    assert item["day_pct"] == pytest.approx(5.0)
