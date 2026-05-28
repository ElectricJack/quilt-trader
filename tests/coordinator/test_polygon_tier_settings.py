"""Tests for polygon paid-tier rate-limit settings."""
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_get_settings_includes_polygon_tier_keys(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        r = await client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert "polygon_min_request_interval_s" in body
    assert "polygon_concurrency" in body
    # Defaults are None until the user sets them
    assert body["polygon_min_request_interval_s"] is None
    assert body["polygon_concurrency"] is None


@pytest.mark.asyncio
async def test_set_polygon_tier_round_trip(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        r = await client.put("/api/settings/polygon-tier", json={
            "min_request_interval_s": 0.6,
            "concurrency": 10,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["polygon_min_request_interval_s"] == "0.6"
        assert body["polygon_concurrency"] == "10"


@pytest.mark.asyncio
async def test_set_polygon_tier_rejects_negative_interval(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        r = await client.put("/api/settings/polygon-tier", json={"min_request_interval_s": -1.0})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_set_polygon_tier_rejects_zero_concurrency(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        r = await client.put("/api/settings/polygon-tier", json={"concurrency": 0})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_delete_polygon_tier_clears_both(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        # Set first
        await client.put("/api/settings/polygon-tier", json={
            "min_request_interval_s": 0.6, "concurrency": 10,
        })
        # Then delete
        r = await client.delete("/api/settings/polygon-tier")
        assert r.status_code == 200
        body = r.json()
        assert body["polygon_min_request_interval_s"] is None
        assert body["polygon_concurrency"] is None
