import asyncio
import os

import pytest_asyncio

os.environ.setdefault("QT_LIVE_FINALIZE_INTERVAL_SECONDS", "999999")
os.environ.setdefault("QT_TICK_COALESCE_WINDOW_MS", "10")
# Keep the scraper catch-up from firing real subprocesses against the live
# packages/ directory when tests boot a coordinator lifespan.
os.environ.setdefault("QT_DISABLE_SCRAPER_CATCHUP", "1")

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
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
async def db_session_factory(tmp_path):
    """Lightweight async session factory backed by an isolated SQLite DB.

    Suitable for unit tests that need real DB interactions without the full
    app lifespan (e.g. QuotaTracker, DatasetJobDispatcher tests).
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf
    await engine.dispose()


@pytest_asyncio.fixture
async def test_app():
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    async with app.router.lifespan_context(app):
        # Allow background tasks (e.g. worker_health_loop) to complete their
        # first iteration before tests start writing to the DB.  Without this,
        # the health-loop's first session runs concurrently with the test's
        # db_session writes on the shared StaticPool connection, which causes
        # SQLite to silently drop some inserts.
        await asyncio.sleep(0.05)
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
