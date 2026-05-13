import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.database.connection import create_engine, create_session_factory
from coordinator.database.models import Base
from httpx import ASGITransport, AsyncClient
from coordinator.main import create_app


@pytest_asyncio.fixture
async def db_engine():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    session_factory = create_session_factory(db_engine)
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def test_app():
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def client(test_app):
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as c:
        yield c
