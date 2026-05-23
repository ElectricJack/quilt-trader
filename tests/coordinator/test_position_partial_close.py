import pytest
from sqlalchemy import select
from coordinator.database.models import Account, Position, TradeLog


@pytest.mark.asyncio
async def test_partial_close_decrements_remaining_quantity(client, db_session, monkeypatch):
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials="{}", supported_asset_types=["equities"], pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    pos = Position(
        account_id=account.id, strategy_type="single",
        legs=[{"symbol": "SPY", "asset_type": "equities", "side": "buy", "quantity": 5, "avg_price": 520.0}],
        status="open", net_cost=2600.0, remaining_quantity=5,
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()
    acct_id = account.id
    pos_id = pos.id

    class FakeAdapter:
        def supports_multileg_orders(self, legs): return False
        def compose_symbol(self, leg): return leg.symbol
        def submit_order(self, symbol, side, quantity, order_type, limit_price=None, stop_price=None, asset_type=None):
            return OrderResult(symbol=symbol, side=side, quantity=quantity, order_type=order_type, filled_price=530.0, broker_order_id="ord-partial")
        def close(self): pass

    async def fake_adapter(acct): return FakeAdapter()
    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter)

    r = await client.post(f"/api/accounts/{acct_id}/positions/{pos_id}/close", json={"quantity": 2})
    assert r.status_code == 200, r.text

    db_session.expire_all()
    refreshed = (await db_session.execute(select(Position).where(Position.id == pos_id))).scalar_one()
    assert refreshed.status == "open"  # Still open -- only partial close
    assert refreshed.remaining_quantity == 3  # 5 - 2 = 3


@pytest.mark.asyncio
async def test_partial_close_full_quantity_closes_position(client, db_session, monkeypatch):
    """When partial close quantity equals remaining_quantity, position should be closed."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials="{}", supported_asset_types=["equities"], pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    pos = Position(
        account_id=account.id, strategy_type="single",
        legs=[{"symbol": "SPY", "asset_type": "equities", "side": "buy", "quantity": 3, "avg_price": 520.0}],
        status="open", net_cost=1560.0, remaining_quantity=3,
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()
    acct_id = account.id
    pos_id = pos.id

    class FakeAdapter:
        def supports_multileg_orders(self, legs): return False
        def compose_symbol(self, leg): return leg.symbol
        def submit_order(self, symbol, side, quantity, order_type, limit_price=None, stop_price=None, asset_type=None):
            return OrderResult(symbol=symbol, side=side, quantity=quantity, order_type=order_type, filled_price=530.0, broker_order_id="ord-full")
        def close(self): pass

    async def fake_adapter(acct): return FakeAdapter()
    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter)

    r = await client.post(f"/api/accounts/{acct_id}/positions/{pos_id}/close", json={"quantity": 3})
    assert r.status_code == 200, r.text

    db_session.expire_all()
    refreshed = (await db_session.execute(select(Position).where(Position.id == pos_id))).scalar_one()
    assert refreshed.status == "closed"
    assert refreshed.remaining_quantity == 0
    assert refreshed.closed_at is not None


@pytest.mark.asyncio
async def test_partial_close_rejects_quantity_exceeding_remaining(client, db_session, monkeypatch):
    """Requesting more than remaining_quantity should be rejected with 422."""
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials="{}", supported_asset_types=["equities"], pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    pos = Position(
        account_id=account.id, strategy_type="single",
        legs=[{"symbol": "SPY", "asset_type": "equities", "side": "buy", "quantity": 5, "avg_price": 520.0}],
        status="open", net_cost=2600.0, remaining_quantity=3,
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()
    acct_id = account.id
    pos_id = pos.id

    r = await client.post(f"/api/accounts/{acct_id}/positions/{pos_id}/close", json={"quantity": 5})
    assert r.status_code == 422, r.text
    assert "exceeds" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_full_close_sets_remaining_quantity_to_zero(client, db_session, monkeypatch):
    """Full close (quantity=None) should set remaining_quantity to 0."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials="{}", supported_asset_types=["equities"], pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    pos = Position(
        account_id=account.id, strategy_type="single",
        legs=[{"symbol": "SPY", "asset_type": "equities", "side": "buy", "quantity": 5, "avg_price": 520.0}],
        status="open", net_cost=2600.0, remaining_quantity=5,
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()
    acct_id = account.id
    pos_id = pos.id

    class FakeAdapter:
        def supports_multileg_orders(self, legs): return False
        def compose_symbol(self, leg): return leg.symbol
        def submit_order(self, symbol, side, quantity, order_type, limit_price=None, stop_price=None, asset_type=None):
            return OrderResult(symbol=symbol, side=side, quantity=quantity, order_type=order_type, filled_price=530.0, broker_order_id="ord-full-close")
        def close(self): pass

    async def fake_adapter(acct): return FakeAdapter()
    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter)

    # No quantity specified -- full close
    r = await client.post(f"/api/accounts/{acct_id}/positions/{pos_id}/close", json={})
    assert r.status_code == 200, r.text

    db_session.expire_all()
    refreshed = (await db_session.execute(select(Position).where(Position.id == pos_id))).scalar_one()
    assert refreshed.status == "closed"
    assert refreshed.remaining_quantity == 0
    assert refreshed.closed_at is not None
