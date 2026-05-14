import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from coordinator.main import create_app
from coordinator.api.routes import live_subscriptions as live_subs_routes


@pytest_asyncio.fixture
async def test_app():
    """Override the default ``test_app`` fixture for this file.

    The live-subscriptions router is not yet mounted by ``create_app`` —
    that wiring lands in S6. Until then, mount it here so the route is
    reachable. Pop the dashboard static-files mount (added last in
    ``create_app``) so ``include_router`` is inserted *before* the
    catch-all, then re-append it to preserve the original ordering.
    """
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    static_mount = None
    for i, route in enumerate(app.routes):
        if getattr(route, "name", "") == "dashboard":
            static_mount = app.routes.pop(i)
            break
    app.include_router(live_subs_routes.router)
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


@pytest.mark.asyncio
async def test_create_and_list_subscription(client: AsyncClient):
    r = await client.post(
        "/api/live-subscriptions",
        json={"broker": "alpaca", "symbol": "SPY", "tick_retention_hours": 24},
    )
    assert r.status_code == 201, r.text
    sub_id = r.json()["id"]
    r2 = await client.get("/api/live-subscriptions")
    assert r2.status_code == 200, r2.text
    items = r2.json()
    assert any(s["id"] == sub_id for s in items)


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
