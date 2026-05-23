import pytest
from sqlalchemy import select
from coordinator.database.models import Account, Position


@pytest.mark.asyncio
async def test_limit_close_passes_order_type_and_price_to_adapter(client, db_session, monkeypatch):
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(name="A", broker_type="alpaca", environment="paper",
                      credentials="{}", supported_asset_types=["equities"], pdt_mode="off")
    db_session.add(account)
    await db_session.flush()
    pos = Position(account_id=account.id, strategy_type="single",
                   legs=[{"symbol": "SPY", "asset_type": "equities", "side": "buy", "quantity": 5, "avg_price": 520.0}],
                   status="open", net_cost=2600.0, remaining_quantity=5)
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()

    captured = {}
    class FakeAdapter:
        def supports_multileg_orders(self, legs): return False
        def compose_symbol(self, leg): return leg.symbol
        def submit_order(self, symbol, side, quantity, order_type, limit_price=None, stop_price=None, asset_type=None):
            captured["order_type"] = order_type
            captured["limit_price"] = limit_price
            return OrderResult(symbol=symbol, side=side, quantity=quantity, order_type=order_type, filled_price=525.0, broker_order_id="ord-limit")
        def close(self): pass

    async def fake_adapter(acct): return FakeAdapter()
    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter)

    r = await client.post(f"/api/accounts/{account.id}/positions/{pos.id}/close", json={"order_type": "limit", "limit_price": 525.0})
    assert r.status_code == 200, r.text
    assert captured["order_type"] == "limit"
    assert captured["limit_price"] == 525.0


@pytest.mark.asyncio
async def test_limit_close_requires_limit_price(client, db_session, monkeypatch):
    from coordinator.api.routes import accounts as accounts_routes
    account = Account(name="A", broker_type="alpaca", environment="paper",
                      credentials="{}", supported_asset_types=["equities"], pdt_mode="off")
    db_session.add(account)
    await db_session.flush()
    pos = Position(account_id=account.id, strategy_type="single",
                   legs=[{"symbol": "SPY", "asset_type": "equities", "side": "buy", "quantity": 5, "avg_price": 520.0}],
                   status="open", net_cost=2600.0, remaining_quantity=5)
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()

    r = await client.post(f"/api/accounts/{account.id}/positions/{pos.id}/close", json={"order_type": "limit"})
    assert r.status_code == 422
    assert "limit_price" in r.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_limit_close_sets_status_to_closing_when_not_filled(client, db_session, monkeypatch):
    """A limit order that returns filled_price=None means pending; status should be 'closing'."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(name="A", broker_type="alpaca", environment="paper",
                      credentials="{}", supported_asset_types=["equities"], pdt_mode="off")
    db_session.add(account)
    await db_session.flush()
    pos = Position(account_id=account.id, strategy_type="single",
                   legs=[{"symbol": "SPY", "asset_type": "equities", "side": "buy", "quantity": 5, "avg_price": 520.0}],
                   status="open", net_cost=2600.0, remaining_quantity=5)
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()

    class FakeAdapter:
        def supports_multileg_orders(self, legs): return False
        def compose_symbol(self, leg): return leg.symbol
        def submit_order(self, symbol, side, quantity, order_type, limit_price=None, stop_price=None, asset_type=None):
            return OrderResult(symbol=symbol, side=side, quantity=quantity,
                               order_type=order_type, filled_price=None, broker_order_id="ord-pending")
        def close(self): pass

    async def fake_adapter(acct): return FakeAdapter()
    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter)

    pos_id = pos.id
    acct_id = account.id

    r = await client.post(f"/api/accounts/{acct_id}/positions/{pos_id}/close",
                          json={"order_type": "limit", "limit_price": 525.0})
    assert r.status_code == 200, r.text

    from sqlalchemy import select
    db_session.expire_all()
    refreshed = (await db_session.execute(select(Position).where(Position.id == pos_id))).scalar_one()
    assert refreshed.status == "closing"
    assert refreshed.remaining_quantity == 5  # Not decremented yet
