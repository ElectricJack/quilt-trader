import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_409_on_duplicate(client: AsyncClient):
    r = await client.post(
        "/api/live-subscriptions",
        json={"broker": "alpaca", "symbol": "QQQ"},
    )
    assert r.status_code == 201, r.text
    r2 = await client.post(
        "/api/live-subscriptions",
        json={"broker": "alpaca", "symbol": "QQQ"},
    )
    assert r2.status_code == 409, r2.text


@pytest.mark.asyncio
async def test_validate_retention_must_be_multiple_of_24(client: AsyncClient):
    r = await client.post(
        "/api/live-subscriptions",
        json={"broker": "alpaca", "symbol": "AAPL", "tick_retention_hours": 36},
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
