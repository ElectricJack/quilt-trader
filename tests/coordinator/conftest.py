import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.database.connection import create_engine, create_session_factory
from coordinator.database.models import Base


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
