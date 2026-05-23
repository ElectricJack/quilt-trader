import pytest
from httpx import AsyncClient

from coordinator.database.models import Account, Position


@pytest.mark.asyncio
async def test_close_single_leg_position_uses_submit_order(
    client: AsyncClient, db_session, monkeypatch
):
    """Single-leg position close uses submit_order instead of submit_multileg_order."""
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

    pos = Position(
        account_id=account.id,
        strategy_type="single",
        legs=[
            {
                "symbol": "SPY",
                "asset_type": "equities",
                "side": "buy",
                "quantity": 10,
                "avg_price": 520.0,
            },
        ],
        status="open",
        net_cost=5200.0,
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()
    pos_id = pos.id

    submitted = []

    class FakeAdapter:
        def supports_multileg_orders(self, legs):
            return False

        def compose_symbol(self, leg):
            return leg.symbol

        def submit_order(
            self,
            symbol,
            side,
            quantity,
            order_type="market",
            limit_price=None,
            stop_price=None,
            asset_type="equities",
        ):
            submitted.append(
                {"symbol": symbol, "side": side, "quantity": quantity}
            )
            return OrderResult(
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type=order_type,
                filled_price=525.0,
                fees=1.0,
                broker_order_id="ord-1",
            )

        def close(self):
            pass

    async def fake_adapter(acct):
        return FakeAdapter()

    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter)

    r = await client.post(
        f"/api/accounts/{account.id}/positions/{pos_id}/close", json={}
    )
    assert r.status_code == 200, r.text

    # Side should be inverted: buy -> sell
    assert len(submitted) == 1
    assert submitted[0]["side"] == "sell"
    assert submitted[0]["quantity"] == 10
    assert submitted[0]["symbol"] == "SPY"
