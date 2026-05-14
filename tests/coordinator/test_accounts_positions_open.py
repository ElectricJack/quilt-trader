import pytest
from httpx import AsyncClient

from coordinator.database.models import Account


@pytest.mark.asyncio
async def test_open_position_returns_423_when_locked(client: AsyncClient, db_session):
    account = Account(
        name="A",
        broker_type="alpaca",
        environment="paper",
        credentials="{}",  # encrypted_creds placeholder for this scope-restricted test
        supported_asset_types=["options"],
        pdt_mode="off",
        locked_by="instance-1",
    )
    db_session.add(account)
    await db_session.flush()
    await db_session.commit()
    body = {
        "legs": [
            {
                "symbol": "SPY",
                "asset_type": "options",
                "side": "buy",
                "quantity": 1,
                "expiry": "2026-06-20",
                "strike": 560.0,
                "right": "call",
            }
        ],
        "order_type": "market",
    }
    r = await client.post(f"/api/accounts/{account.id}/positions/open", json=body)
    assert r.status_code == 423
    assert r.json()["detail"]["locked_by"] == "instance-1"


@pytest.mark.asyncio
async def test_open_position_422_on_disallowed_asset_type(client: AsyncClient, db_session):
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
    body = {
        "legs": [
            {
                "symbol": "SPY",
                "asset_type": "options",
                "side": "buy",
                "quantity": 1,
                "expiry": "2026-06-20",
                "strike": 560,
                "right": "call",
            }
        ],
        "order_type": "market",
    }
    r = await client.post(f"/api/accounts/{account.id}/positions/open", json=body)
    assert r.status_code == 422
    assert "options" in r.json()["detail"]


@pytest.mark.asyncio
async def test_open_position_atomic_path_persists_position(
    client: AsyncClient, db_session, monkeypatch
):
    from worker.broker_adapter import MultilegOrderResult, MultilegLegResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A",
        broker_type="alpaca",
        environment="paper",
        credentials="{}",
        supported_asset_types=["options"],
        pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()
    await db_session.commit()

    class FakeAdapter:
        def supports_multileg_orders(self, legs):
            return True

        def compose_symbol(self, leg):
            return f"SPY{leg.strike:.0f}"

        def submit_multileg_order(self, legs, order_type, limit_price):
            return MultilegOrderResult(
                broker_order_id="parent-1",
                legs=[
                    MultilegLegResult(
                        index=0,
                        status="filled",
                        filled_price=8.30,
                        fees=0.65,
                        broker_order_id="leg-1",
                    ),
                    MultilegLegResult(
                        index=1,
                        status="filled",
                        filled_price=4.20,
                        fees=0.65,
                        broker_order_id="leg-2",
                    ),
                ],
                atomic=True,
            )

        def close(self):
            pass

    async def fake_adapter_for_account(acct):
        return FakeAdapter()

    monkeypatch.setattr(
        accounts_routes, "_adapter_for_account", fake_adapter_for_account
    )

    body = {
        "legs": [
            {
                "symbol": "SPY",
                "asset_type": "options",
                "side": "buy",
                "quantity": 1,
                "expiry": "2026-06-20",
                "strike": 560.0,
                "right": "call",
            },
            {
                "symbol": "SPY",
                "asset_type": "options",
                "side": "sell",
                "quantity": 1,
                "expiry": "2026-06-20",
                "strike": 570.0,
                "right": "call",
            },
        ],
        "order_type": "limit",
        "limit_price": 4.0,
        "strategy_type": "vertical_bull_call",
    }
    r = await client.post(f"/api/accounts/{account.id}/positions/open", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["atomic"] is True
    assert data["broker_order_id"] == "parent-1"
    assert data["partial_fill"] is False
    assert data["position_id"] is not None
