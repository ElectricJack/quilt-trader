from coordinator.main import create_app
from coordinator.api.routes import brokers as brokers_routes

# pytest fixture override (place above the test functions)
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def test_app():
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    # The static-files catch-all mount (dashboard) is registered last in
    # create_app. Remove it temporarily so include_router can insert the
    # broker route before the catch-all, then re-append it.
    static_mount = None
    for i, route in enumerate(app.routes):
        if getattr(route, "name", "") == "dashboard":
            static_mount = app.routes.pop(i)
            break
    app.include_router(brokers_routes.router)
    if static_mount is not None:
        app.routes.append(static_mount)
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def client(test_app):
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as c:
        yield c


import pytest


@pytest.mark.asyncio
async def test_get_asset_types_alpaca(client: AsyncClient):
    r = await client.get("/api/brokers/alpaca/asset-types")
    assert r.status_code == 200
    assert r.json() == {"asset_types": ["equities", "options", "crypto"]}


@pytest.mark.asyncio
async def test_get_asset_types_unknown_broker_404(client: AsyncClient):
    r = await client.get("/api/brokers/ibkr/asset-types")
    assert r.status_code == 404
