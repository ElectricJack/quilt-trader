"""Tests for /api/accounts/{id}/sync and /broker-info."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from coordinator.api.dependencies import get_container
from coordinator.database.models import Account, AccountCashFlow, TradeLog
from sqlalchemy import select
from worker.broker_adapter import BrokerTransaction


async def _seed_account(client, *, broker_type="alpaca", environment="paper"):
    resp = await client.post("/api/accounts", json={
        "name": "Sync Test",
        "broker_type": broker_type,
        "environment": environment,
        "credentials": {"api_key": "k", "secret_key": "s"},
        "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class _FakeAdapter:
    def __init__(self, transactions, info=None, positions=None):
        self._txns = transactions
        self._info = info or {"cash": 5000.0, "portfolio_value": 25000.0, "buying_power": 50000.0}
        self._positions = positions or {}

    def get_transactions(self, since):
        return list(self._txns)

    def get_account_info(self):
        return dict(self._info)

    def get_positions(self):
        return dict(self._positions)

    def close(self):
        pass


@pytest.mark.asyncio
async def test_sync_inserts_trades_and_cash_flows(client, db_session):
    account_id = await _seed_account(client)

    txns = [
        BrokerTransaction(
            broker_id="fill-1",
            type="fill",
            timestamp=datetime(2026, 4, 1, tzinfo=timezone.utc),
            symbol="AAPL",
            side="buy",
            quantity=10.0,
            price=150.0,
            amount=-1500.0,
        ),
        BrokerTransaction(
            broker_id="div-1",
            type="dividend",
            timestamp=datetime(2026, 4, 15, tzinfo=timezone.utc),
            symbol="AAPL",
            amount=12.5,
        ),
        BrokerTransaction(
            broker_id="csd-1",
            type="deposit",
            timestamp=datetime(2026, 3, 1, tzinfo=timezone.utc),
            amount=5000.0,
        ),
    ]
    adapter = _FakeAdapter(txns, positions={"AAPL": {"market_value": 1600.0}})

    with patch("worker.adapter_factory.make_broker_adapter", return_value=adapter):
        resp = await client.post(f"/api/accounts/{account_id}/sync", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["trades_inserted"] == 1
    assert body["cash_flows_inserted"] == 2
    assert body["snapshot"]["total_value"] == 25000.0
    assert body["positions_count"] == 1

    trades = (await db_session.execute(
        select(TradeLog).where(TradeLog.account_id == account_id)
    )).scalars().all()
    assert len(trades) == 1
    assert trades[0].symbol == "AAPL"
    assert trades[0].broker_txn_id == "fill-1"
    assert trades[0].source == "broker_sync"

    flows = (await db_session.execute(
        select(AccountCashFlow).where(AccountCashFlow.account_id == account_id)
    )).scalars().all()
    types = {f.type for f in flows}
    assert types == {"dividend", "deposit"}


@pytest.mark.asyncio
async def test_sync_dedups_repeated_calls(client, db_session):
    account_id = await _seed_account(client)
    txn = BrokerTransaction(
        broker_id="fill-1",
        type="fill",
        timestamp=datetime(2026, 4, 1, tzinfo=timezone.utc),
        symbol="AAPL",
        side="buy",
        quantity=10.0,
        price=150.0,
        amount=-1500.0,
    )
    adapter = _FakeAdapter([txn])

    with patch("worker.adapter_factory.make_broker_adapter", return_value=adapter):
        first = await client.post(f"/api/accounts/{account_id}/sync", json={})
        second = await client.post(f"/api/accounts/{account_id}/sync", json={})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["trades_inserted"] == 1
    assert second.json()["trades_inserted"] == 0  # deduped


@pytest.mark.asyncio
async def test_sync_with_explicit_since(client):
    account_id = await _seed_account(client)
    adapter = _FakeAdapter([])
    with patch("worker.adapter_factory.make_broker_adapter", return_value=adapter):
        resp = await client.post(
            f"/api/accounts/{account_id}/sync",
            json={"since": "2026-01-01T00:00:00Z"},
        )
    assert resp.status_code == 200
    assert resp.json()["since"].startswith("2026-01-01T00:00:00")


@pytest.mark.asyncio
async def test_broker_info_returns_account_and_positions(client):
    account_id = await _seed_account(client)
    adapter = _FakeAdapter(
        [],
        info={"cash": 1000.0, "portfolio_value": 5000.0, "buying_power": 10000.0},
        positions={"AAPL": {
            "symbol": "AAPL", "quantity": 10.0, "side": "long",
            "avg_price": 150.0, "current_price": 200.0,
            "unrealized_pnl": 500.0, "market_value": 2000.0,
        }},
    )
    with patch("worker.adapter_factory.make_broker_adapter", return_value=adapter):
        resp = await client.get(f"/api/accounts/{account_id}/broker-info")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["account_info"]["portfolio_value"] == 5000.0
    assert len(body["positions"]) == 1
    assert body["positions"][0]["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_broker_info_404_for_unknown_account(client):
    resp = await client.get("/api/accounts/no-such-id/broker-info")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_sync_handles_broker_error(client):
    account_id = await _seed_account(client)

    class _Boom:
        def get_transactions(self, since): raise RuntimeError("broker down")
        def get_account_info(self): raise RuntimeError("nope")
        def get_positions(self): return {}
        def close(self): pass

    with patch("worker.adapter_factory.make_broker_adapter", return_value=_Boom()):
        resp = await client.post(f"/api/accounts/{account_id}/sync", json={})
    assert resp.status_code == 502
    assert "broker down" in resp.json()["detail"]
