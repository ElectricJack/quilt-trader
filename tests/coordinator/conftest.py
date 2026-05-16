import os

os.environ.setdefault("QT_WORKER_HEALTH_INTERVAL_SECONDS", "999999")
os.environ.setdefault("QT_WORKER_OFFLINE_TIMEOUT_SECONDS", "999999")

import pytest_asyncio

from coordinator.database.connection import create_engine
from coordinator.database.models import Base
from httpx import ASGITransport, AsyncClient
from coordinator.main import create_app
from coordinator.api.dependencies import get_container


@pytest_asyncio.fixture
async def db_engine():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def test_app():
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def db_session(test_app):
    # Use the app's session_factory so writes via `db_session` are
    # visible to API requests made via `client` (both fixtures share
    # the same in-memory DB through the running app's container).
    container = get_container()
    async with container.session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(test_app):
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as c:
        yield c
