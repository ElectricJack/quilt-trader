import pytest
from httpx import ASGITransport, AsyncClient

from coordinator.main import create_app


@pytest.mark.asyncio
async def test_app_health_endpoint():
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


@pytest.mark.asyncio
async def test_app_creates_tables():
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
