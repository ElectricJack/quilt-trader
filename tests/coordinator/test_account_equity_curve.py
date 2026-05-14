"""Tests for /api/accounts/{id}/equity-curve."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


async def _seed_account(client):
    resp = await client.post("/api/accounts", json={
        "name": "Curve Test",
        "broker_type": "alpaca",
        "environment": "paper",
        "credentials": {"api_key": "k", "secret_key": "s"},
        "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    return resp.json()["id"]


class _LiveAdapter:
    def __init__(self, portfolio_value: float):
        self._pv = portfolio_value

    def get_account_info(self):
        return {"cash": 0.0, "portfolio_value": self._pv, "buying_power": 0.0}

    def close(self):
        pass


@pytest.mark.asyncio
async def test_equity_curve_no_snapshots_back_calcs_from_live(client, db_session):
    from coordinator.database.models import AccountCashFlow

    account_id = await _seed_account(client)

    # Two deposits and one withdrawal in the last 60 days.
    now = datetime.now(timezone.utc)
    db_session.add_all([
        AccountCashFlow(account_id=account_id, type="deposit", amount=10000.0,
                        timestamp=now - timedelta(days=60)),
        AccountCashFlow(account_id=account_id, type="dividend", amount=50.0,
                        timestamp=now - timedelta(days=30)),
        AccountCashFlow(account_id=account_id, type="withdrawal", amount=-500.0,
                        timestamp=now - timedelta(days=10)),
    ])
    await db_session.commit()

    # Broker says portfolio is now worth $11,000.
    with patch("worker.adapter_factory.make_broker_adapter", return_value=_LiveAdapter(11000.0)):
        resp = await client.get(f"/api/accounts/{account_id}/equity-curve")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]

    # Should have 1 live + 3 estimated points, sorted ascending.
    assert len(items) == 4
    sources = [p["source"] for p in items]
    assert sources.count("live") == 1
    assert sources.count("estimated") == 3
    # Latest point is the live one at current value.
    assert items[-1]["source"] == "live"
    assert items[-1]["value"] == 11000.0

    # Walking backward: before the -500 withdrawal at -10d, value was 11000 - (-500) = 11500.
    # Before the +50 dividend at -30d, value was 11500 - 50 = 11450.
    # Before the +10000 deposit at -60d, value was 11450 - 10000 = 1450.
    by_ts = {p["timestamp"]: p["value"] for p in items}
    values_only_estimated = sorted([p["value"] for p in items if p["source"] == "estimated"])
    assert 1450.0 in values_only_estimated
    assert 11450.0 in values_only_estimated
    assert 11500.0 in values_only_estimated


@pytest.mark.asyncio
async def test_equity_curve_with_snapshots_anchors_at_them(client, db_session):
    from coordinator.database.models import AccountCashFlow, AccountSnapshot

    account_id = await _seed_account(client)
    now = datetime.now(timezone.utc)

    db_session.add_all([
        AccountSnapshot(account_id=account_id,
                        timestamp=now - timedelta(days=60),
                        total_value=10000.0, cash=10000.0, positions_value=0.0,
                        source="seed"),
        AccountSnapshot(account_id=account_id,
                        timestamp=now - timedelta(days=30),
                        total_value=12000.0, cash=2000.0, positions_value=10000.0,
                        source="broker_sync"),
        AccountSnapshot(account_id=account_id,
                        timestamp=now - timedelta(days=5),
                        total_value=14000.0, cash=1000.0, positions_value=13000.0,
                        source="broker_sync"),
        # A dividend between snapshots 2 and 3.
        AccountCashFlow(account_id=account_id, type="dividend", amount=100.0,
                        timestamp=now - timedelta(days=20)),
    ])
    await db_session.commit()

    resp = await client.get(f"/api/accounts/{account_id}/equity-curve")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]

    snap_pts = [p for p in items if p["source"] == "snapshot"]
    assert len(snap_pts) == 3
    snap_values = [p["value"] for p in snap_pts]
    assert snap_values == [10000.0, 12000.0, 14000.0]

    # The intermediate dividend point is estimated and forward-walked from snap 2 (12000 + 100 = 12100).
    est_pts = [p for p in items if p["source"] == "estimated"]
    assert any(p["value"] == 12100.0 for p in est_pts)


@pytest.mark.asyncio
async def test_equity_curve_404_unknown_account(client):
    resp = await client.get("/api/accounts/no-such-id/equity-curve")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_equity_curve_no_data_no_snapshots(client):
    """Empty account with no cash flows: returns a single live anchor."""
    account_id = await _seed_account(client)
    with patch("worker.adapter_factory.make_broker_adapter", return_value=_LiveAdapter(5000.0)):
        resp = await client.get(f"/api/accounts/{account_id}/equity-curve")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["source"] == "live"
    assert items[0]["value"] == 5000.0
