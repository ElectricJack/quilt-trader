import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from coordinator.main import create_app


@pytest_asyncio.fixture
async def app():
    app = create_app(database_url="sqlite+aiosqlite://", encryption_key="test-key-32-bytes-long!!!!!!!!")
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestDataAvailableEndpoint:
    @pytest.mark.asyncio
    async def test_list_available_data(self, client):
        resp = await client.get("/api/data/available")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestDownloadEndpoints:
    @pytest.mark.asyncio
    async def test_list_downloads_empty(self, client):
        resp = await client.get("/api/data/downloads")
        assert resp.status_code in (200, 503)

    @pytest.mark.asyncio
    async def test_get_download_not_found(self, client):
        resp = await client.get("/api/data/downloads/nonexistent")
        assert resp.status_code in (404, 503)
