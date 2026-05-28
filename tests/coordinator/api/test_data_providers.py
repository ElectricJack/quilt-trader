"""Tests for GET /api/data/providers — availability matrix endpoint."""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from coordinator.database.models import Account, Setting


@pytest.mark.asyncio
async def test_providers_yfinance_always_available(test_app):
    """yfinance has no credential requirements — always available."""
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        r = await ac.get("/api/data/providers")
    assert r.status_code == 200
    body = r.json()
    by_name = {p["name"]: p for p in body}
    assert by_name["yfinance"]["available"] is True
    assert by_name["yfinance"]["reason"] is None


@pytest.mark.asyncio
async def test_providers_polygon_requires_key(test_app):
    """polygon requires polygon_api_key Setting — absent by default."""
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        r = await ac.get("/api/data/providers")
    assert r.status_code == 200
    body = r.json()
    by_name = {p["name"]: p for p in body}
    assert by_name["polygon"]["available"] is False
    assert "polygon" in by_name["polygon"]["reason"].lower()


@pytest.mark.asyncio
async def test_providers_alpaca_requires_account(test_app):
    """alpaca becomes available once an Account row with broker_type='alpaca' exists."""
    from coordinator.api.dependencies import get_container
    container = get_container()
    async with container.session_factory() as s:
        s.add(Account(
            name="test-alpaca",
            broker_type="alpaca",
            environment="paper",
            credentials="{}",
            supported_asset_types=["crypto", "equity"],
        ))
        await s.commit()

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        r = await ac.get("/api/data/providers")
    assert r.status_code == 200
    body = r.json()
    by_name = {p["name"]: p for p in body}
    assert by_name["alpaca"]["available"] is True
    assert by_name["alpaca"]["reason"] is None


@pytest.mark.asyncio
async def test_providers_ordering_is_alphabetical(test_app):
    """Response list is sorted alphabetically by name."""
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        r = await ac.get("/api/data/providers")
    assert r.status_code == 200
    body = r.json()
    names = [p["name"] for p in body]
    assert names == sorted(names)
