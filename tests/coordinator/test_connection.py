import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_engine_connects(db_engine):
    async with db_engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        assert result.scalar() == 1


@pytest.mark.asyncio
async def test_session_works(db_session):
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1
