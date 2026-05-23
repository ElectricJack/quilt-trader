"""Tests that manually closing a position owned by a running algorithm
sets state_stale and sends a position_closed WebSocket message."""

import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock

from coordinator.database.models import (
    Account,
    Algorithm,
    AlgorithmInstance,
    Position,
    Worker,
)


@pytest.mark.asyncio
async def test_close_sets_state_stale_and_notifies_worker(
    client: AsyncClient, db_session, monkeypatch
):
    """When a position with owner_instance_id is closed, the owning
    AlgorithmInstance should have state_stale=True and a position_closed
    message should be sent to the worker WebSocket."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes
    from coordinator.api import websocket as ws_module

    # -- Set up database rows --
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

    worker = Worker(
        name="w1",
        tailscale_ip="1.1.1.1",
        status="online",
        max_algorithms=5,
    )
    db_session.add(worker)
    await db_session.flush()

    algo = Algorithm(
        repo_url="https://github.com/test/algo",
        name="test-algo",
        install_status="installed",
    )
    db_session.add(algo)
    await db_session.flush()

    instance = AlgorithmInstance(
        algorithm_id=algo.id,
        account_id=account.id,
        worker_id=worker.id,
        status="running",
        state_stale=False,
    )
    db_session.add(instance)
    await db_session.flush()

    pos = Position(
        account_id=account.id,
        strategy_type="single",
        owner_instance_id=instance.id,
        legs=[
            {
                "symbol": "AAPL",
                "asset_type": "equities",
                "side": "buy",
                "quantity": 10,
                "avg_price": 150.0,
            },
        ],
        status="open",
        net_cost=1500.0,
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()

    pos_id = pos.id
    instance_id = instance.id
    worker_id = worker.id

    # -- Fake broker adapter --
    class FakeAdapter:
        def supports_multileg_orders(self, legs):
            return False

        def compose_symbol(self, leg):
            return leg.symbol

        def submit_order(self, symbol, side, quantity, order_type="market",
                         limit_price=None, stop_price=None, asset_type="equities"):
            return OrderResult(
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type=order_type,
                filled_price=155.0,
                fees=0.50,
                broker_order_id="ord-notify",
            )

        def close(self):
            pass

    async def fake_adapter(acct):
        return FakeAdapter()

    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter)

    # -- Fake WebSocket for the worker --
    fake_ws = AsyncMock()
    ws_module.manager.worker_connections[worker_id] = fake_ws

    try:
        r = await client.post(
            f"/api/accounts/{account.id}/positions/{pos_id}/close",
            json={},
        )
        assert r.status_code == 200, r.text

        # Expire cached state so we re-read from the DB
        db_session.expire_all()
        from sqlalchemy import select

        inst_row = (
            await db_session.execute(
                select(AlgorithmInstance).where(AlgorithmInstance.id == instance_id)
            )
        ).scalar_one()
        assert inst_row.state_stale is True, "state_stale should be True after manual close"

        # Verify WebSocket message was sent
        fake_ws.send_json.assert_called_once()
        msg = fake_ws.send_json.call_args[0][0]
        assert msg["type"] == "position_closed"
        assert msg["instance_id"] == instance_id
        assert msg["position_id"] == pos_id
        assert msg["symbol"] == "AAPL"
        assert msg["reason"] == "manual_close"
    finally:
        ws_module.manager.worker_connections.pop(worker_id, None)


@pytest.mark.asyncio
async def test_close_without_owner_does_not_set_stale(
    client: AsyncClient, db_session, monkeypatch
):
    """Closing a position with no owner_instance_id should not affect any instance."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="B",
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
        owner_instance_id=None,
        legs=[
            {
                "symbol": "MSFT",
                "asset_type": "equities",
                "side": "buy",
                "quantity": 5,
                "avg_price": 400.0,
            },
        ],
        status="open",
        net_cost=2000.0,
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()

    pos_id = pos.id

    class FakeAdapter:
        def supports_multileg_orders(self, legs):
            return False

        def compose_symbol(self, leg):
            return leg.symbol

        def submit_order(self, symbol, side, quantity, order_type="market",
                         limit_price=None, stop_price=None, asset_type="equities"):
            return OrderResult(
                symbol=symbol, side=side, quantity=quantity,
                order_type=order_type, filled_price=405.0, fees=0.25,
                broker_order_id="ord-no-owner",
            )

        def close(self):
            pass

    async def fake_adapter(acct):
        return FakeAdapter()

    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter)

    r = await client.post(
        f"/api/accounts/{account.id}/positions/{pos_id}/close",
        json={},
    )
    assert r.status_code == 200, r.text
    # No assertion on state_stale — just confirm no crash


@pytest.mark.asyncio
async def test_close_stopped_instance_not_stale(
    client: AsyncClient, db_session, monkeypatch
):
    """Closing a position owned by a stopped instance should not set state_stale."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="C",
        broker_type="alpaca",
        environment="paper",
        credentials="{}",
        supported_asset_types=["equities"],
        pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    worker = Worker(
        name="w2", tailscale_ip="2.2.2.2", status="online", max_algorithms=5,
    )
    db_session.add(worker)
    await db_session.flush()

    algo = Algorithm(
        repo_url="https://github.com/test/algo2",
        name="stopped-algo",
        install_status="installed",
    )
    db_session.add(algo)
    await db_session.flush()

    instance = AlgorithmInstance(
        algorithm_id=algo.id,
        account_id=account.id,
        worker_id=worker.id,
        status="stopped",
        state_stale=False,
    )
    db_session.add(instance)
    await db_session.flush()

    pos = Position(
        account_id=account.id,
        strategy_type="single",
        owner_instance_id=instance.id,
        legs=[
            {
                "symbol": "GOOG",
                "asset_type": "equities",
                "side": "buy",
                "quantity": 3,
                "avg_price": 175.0,
            },
        ],
        status="open",
        net_cost=525.0,
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()

    pos_id = pos.id
    instance_id = instance.id

    class FakeAdapter:
        def supports_multileg_orders(self, legs):
            return False

        def compose_symbol(self, leg):
            return leg.symbol

        def submit_order(self, symbol, side, quantity, order_type="market",
                         limit_price=None, stop_price=None, asset_type="equities"):
            return OrderResult(
                symbol=symbol, side=side, quantity=quantity,
                order_type=order_type, filled_price=180.0, fees=0.10,
                broker_order_id="ord-stopped",
            )

        def close(self):
            pass

    async def fake_adapter(acct):
        return FakeAdapter()

    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter)

    r = await client.post(
        f"/api/accounts/{account.id}/positions/{pos_id}/close",
        json={},
    )
    assert r.status_code == 200, r.text

    from sqlalchemy import select

    inst_row = (
        await db_session.execute(
            select(AlgorithmInstance).where(AlgorithmInstance.id == instance_id)
        )
    ).scalar_one()
    assert inst_row.state_stale is False, "state_stale should remain False for stopped instance"
