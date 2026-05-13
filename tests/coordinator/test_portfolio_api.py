import pytest
from datetime import datetime, timedelta, timezone


@pytest.mark.asyncio
async def test_portfolio_equity_empty(client):
    response = await client.get("/api/portfolio/equity?range=1m")
    assert response.status_code == 200
    body = response.json()
    assert body == {"accounts": []}


@pytest.mark.asyncio
async def test_portfolio_equity_with_snapshots(client, db_session):
    from coordinator.database.models import Account, AccountSnapshot

    acct = Account(
        name="Alpaca Main", broker_type="alpaca",
        supported_asset_types=["equities"], pdt_mode="off",
        credentials="{}",
    )
    db_session.add(acct)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    for i in range(5):
        db_session.add(AccountSnapshot(
            account_id=acct.id,
            timestamp=now - timedelta(days=4 - i),
            total_value=10000.0 + i * 100,
            cash=5000.0,
            positions_value=5000.0 + i * 100,
            source="seed",
        ))
    await db_session.commit()

    response = await client.get("/api/portfolio/equity?range=1m")
    assert response.status_code == 200
    body = response.json()
    assert len(body["accounts"]) == 1
    assert body["accounts"][0]["account_name"] == "Alpaca Main"
    assert len(body["accounts"][0]["points"]) == 5


@pytest.mark.asyncio
async def test_portfolio_kpis_empty(client):
    response = await client.get("/api/portfolio/kpis")
    assert response.status_code == 200
    body = response.json()
    assert body["total_equity"] == 0
    assert body["today_pnl"] == 0
    assert body["trades_today"] == 0
    assert body["open_positions"] == 0


@pytest.mark.asyncio
async def test_portfolio_kpis_with_data(client, db_session):
    from coordinator.database.models import (
        Account, AccountSnapshot, Position, TradeLog,
    )

    acct = Account(name="A", broker_type="alpaca", supported_asset_types=["equities"], pdt_mode="off", credentials="{}")
    db_session.add(acct)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    db_session.add(AccountSnapshot(
        account_id=acct.id, timestamp=now, total_value=100000.0,
        cash=40000.0, positions_value=60000.0, source="seed",
    ))
    db_session.add(Position(
        account_id=acct.id, status="open", legs=[],
        unrealized_pnl=500.0, net_cost=10000.0,
    ))
    db_session.add(TradeLog(
        account_id=acct.id, source="test", symbol="SPY", side="buy",
        quantity=1.0, filled_price=400.0, timestamp=now,
    ))
    await db_session.commit()

    response = await client.get("/api/portfolio/kpis")
    assert response.status_code == 200
    body = response.json()
    assert body["total_equity"] == 100000.0
    assert body["trades_today"] == 1
    assert body["open_positions"] == 1
    assert body["open_risk"] == 500.0
    assert body["deployed_pct"] == 60.0
    assert body["buying_power"] == 40000.0


@pytest.mark.asyncio
async def test_portfolio_allocation_empty(client):
    response = await client.get("/api/portfolio/allocation")
    assert response.status_code == 200
    body = response.json()
    assert body["by_class"] == []
    assert body["by_symbol"] == []


@pytest.mark.asyncio
async def test_portfolio_allocation_with_positions(client, db_session):
    from coordinator.database.models import Account, AccountSnapshot, Position

    acct = Account(name="A", broker_type="alpaca", supported_asset_types=["equities"], pdt_mode="off", credentials="{}")
    db_session.add(acct)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add(AccountSnapshot(
        account_id=acct.id, timestamp=now, total_value=100000.0,
        cash=20000.0, positions_value=80000.0, source="seed",
    ))
    db_session.add(Position(
        account_id=acct.id, status="open",
        legs=[{"symbol": "SPY", "asset_type": "equities", "value": 50000.0}],
        net_cost=50000.0,
    ))
    db_session.add(Position(
        account_id=acct.id, status="open",
        legs=[{"symbol": "BTC", "asset_type": "crypto", "value": 30000.0}],
        net_cost=30000.0,
    ))
    await db_session.commit()

    response = await client.get("/api/portfolio/allocation")
    body = response.json()
    classes = {seg["key"]: seg for seg in body["by_class"]}
    assert classes["equities"]["value_usd"] == 50000.0
    assert classes["crypto"]["value_usd"] == 30000.0
    assert classes["cash"]["value_usd"] == 20000.0
    symbols = {seg["key"]: seg for seg in body["by_symbol"]}
    assert "SPY" in symbols
    assert "BTC" in symbols
