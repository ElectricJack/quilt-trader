import pytest
from httpx import AsyncClient

from coordinator.database.models import Account, Setting


@pytest.mark.asyncio
async def test_create_422_when_neither_source_provided(client: AsyncClient):
    """Sending neither account_id nor provider_type returns 422."""
    r = await client.post(
        "/api/live-subscriptions",
        json={"symbol": "QQQ"},
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_create_409_on_duplicate_account_sub(client: AsyncClient, db_session):
    """Creating two subscriptions for the same account+symbol returns 409."""
    acct = Account(name="Dup-Test", broker_type="alpaca",
                   credentials="{}", supported_asset_types=["equities"])
    db_session.add(acct)
    await db_session.commit()

    body = {"account_id": acct.id, "symbol": "QQQ", "asset_class": "equities"}
    r = await client.post("/api/live-subscriptions", json=body)
    assert r.status_code == 201, r.text
    r2 = await client.post("/api/live-subscriptions", json=body)
    assert r2.status_code == 409, r2.text


@pytest.mark.asyncio
async def test_validate_retention_must_be_multiple_of_24(client: AsyncClient):
    r = await client.post(
        "/api/live-subscriptions",
        json={"provider_type": "polygon", "symbol": "AAPL", "tick_retention_hours": 36},
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_estimate_endpoint_returns_projected_bytes(client: AsyncClient):
    r = await client.get(
        "/api/live-subscriptions/estimate",
        params={"broker": "alpaca", "symbol": "SPY", "retention_hours": 24},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["projected_bytes"] > 0
    assert body["source"] in ("estimated", "observed")
