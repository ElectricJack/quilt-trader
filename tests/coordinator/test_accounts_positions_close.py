import pytest
from httpx import AsyncClient

from coordinator.database.models import Account


@pytest.mark.asyncio
async def test_close_long_position_submits_sell_market_order(
    client: AsyncClient, db_session, monkeypatch
):
    """Closing a long position must submit an opposite-side (sell) market order."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A",
        broker_type="alpaca",
        environment="paper",
        credentials="{}",
        supported_asset_types=["equities"],
        pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()
    await db_session.commit()

    captured = {}

    class FakeAdapter:
        def submit_order(self, symbol, side, quantity, order_type,
                         limit_price=None, stop_price=None):
            captured["symbol"] = symbol
            captured["side"] = side
            captured["quantity"] = quantity
            captured["order_type"] = order_type
            return OrderResult(
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type=order_type,
                filled_price=521.23,
                fees=0.0,
                broker_order_id="ord-abc",
            )

        def close(self):
            pass

    async def fake_adapter_for_account(acct):
        return FakeAdapter()

    monkeypatch.setattr(
        accounts_routes, "_adapter_for_account", fake_adapter_for_account
    )

    body = {
        "symbol": "SPY",
        "asset_type": "equities",
        "side": "long",
        "quantity": 5,
    }
    r = await client.post(f"/api/accounts/{account.id}/positions/close", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["broker_order_id"] == "ord-abc"
    assert data["filled_price"] == 521.23
    assert data["status"] == "filled"
    # Side passed to adapter must be the *opposite* of the position side.
    assert captured["side"] == "sell"
    assert captured["symbol"] == "SPY"
    assert captured["quantity"] == 5
    assert captured["order_type"] == "market"


@pytest.mark.asyncio
async def test_close_short_position_submits_buy_market_order(
    client: AsyncClient, db_session, monkeypatch
):
    """Closing a short position must submit a buy order."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A",
        broker_type="alpaca",
        environment="paper",
        credentials="{}",
        supported_asset_types=["equities"],
        pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()
    await db_session.commit()

    captured = {}

    class FakeAdapter:
        def submit_order(self, symbol, side, quantity, order_type,
                         limit_price=None, stop_price=None):
            captured["side"] = side
            return OrderResult(
                symbol=symbol, side=side, quantity=quantity,
                order_type=order_type, filled_price=100.0,
                broker_order_id="ord-x",
            )
        def close(self): pass

    async def fake_adapter_for_account(acct):
        return FakeAdapter()
    monkeypatch.setattr(
        accounts_routes, "_adapter_for_account", fake_adapter_for_account
    )

    body = {"symbol": "TSLA", "asset_type": "equities",
            "side": "short", "quantity": 2}
    r = await client.post(f"/api/accounts/{account.id}/positions/close", json=body)
    assert r.status_code == 200
    assert captured["side"] == "buy"


@pytest.mark.asyncio
async def test_close_marks_internal_position_closed_and_writes_trade(
    client: AsyncClient, db_session, monkeypatch
):
    """If Quilt has an internal Position for the symbol, mark it closed
    and write a closing TradeLog row."""
    from datetime import datetime, timezone
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes
    from coordinator.database.models import Position, TradeLog
    from sqlalchemy import select

    account = Account(
        name="A",
        broker_type="alpaca",
        environment="paper",
        credentials="{}",
        supported_asset_types=["equities"],
        pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    pos = Position(
        account_id=account.id,
        strategy_type="single",
        legs=[{"symbol": "SPY", "asset_type": "equities",
               "side": "buy", "quantity": 5, "avg_price": 500.0}],
        status="open",
        net_cost=2500.0,
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()
    pos_id = pos.id

    class FakeAdapter:
        def submit_order(self, symbol, side, quantity, order_type,
                         limit_price=None, stop_price=None):
            return OrderResult(
                symbol=symbol, side=side, quantity=quantity,
                order_type=order_type, filled_price=520.0,
                fees=0.0, broker_order_id="ord-xyz",
            )
        def close(self): pass

    async def fake_adapter_for_account(acct):
        return FakeAdapter()
    monkeypatch.setattr(
        accounts_routes, "_adapter_for_account", fake_adapter_for_account
    )

    body = {"symbol": "SPY", "asset_type": "equities",
            "side": "long", "quantity": 5}
    r = await client.post(f"/api/accounts/{account.id}/positions/close", json=body)
    assert r.status_code == 200, r.text

    db_session.expire_all()
    refreshed = (await db_session.execute(
        select(Position).where(Position.id == pos_id)
    )).scalar_one()
    assert refreshed.status == "closed"
    assert refreshed.closed_at is not None

    trades = (await db_session.execute(
        select(TradeLog).where(TradeLog.position_id == pos_id)
    )).scalars().all()
    assert len(trades) == 1
    assert trades[0].symbol == "SPY"
    assert trades[0].side == "sell"
    assert trades[0].quantity == 5
    assert trades[0].filled_price == 520.0
    assert trades[0].broker_txn_id == "ord-xyz"
    assert trades[0].source == "manual"
