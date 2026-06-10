import uuid
import pytest
import pytest_asyncio
from datetime import datetime, date, timezone
from httpx import ASGITransport, AsyncClient

from coordinator.database.models import Algorithm, BacktestRun


@pytest_asyncio.fixture
async def test_client(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def db_session_factory(test_app):
    from coordinator.api.dependencies import get_container
    return get_container().session_factory


@pytest.mark.asyncio
async def test_list_algorithms_includes_summary(test_client, db_session_factory):
    aid = f"a-{uuid.uuid4().hex[:6]}"
    async with db_session_factory() as s:
        s.add(Algorithm(
            id=aid, repo_url="https://x.com/r", name="x", source_path="/tmp/x",
            install_status="installed",
        ))
        s.add(BacktestRun(
            id="r-1", algorithm_id=aid, status="completed", sharpe_ratio=1.1,
            date_range_start=date(2025, 1, 1), date_range_end=date(2025, 12, 31),
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        ))
        await s.commit()

    resp = await test_client.get("/api/algorithms")
    assert resp.status_code == 200
    body = resp.json()
    mine = next(a for a in body if a["id"] == aid)
    assert "summary" in mine
    assert mine["summary"]["status"] == "idle"
    assert mine["summary"]["counts"]["backtests"] == 1
    assert mine["summary"]["headline_sharpe"] == pytest.approx(1.1)
    assert mine["summary"]["headline_sharpe_source"] == "last_backtest"


@pytest.mark.asyncio
async def test_get_algorithm_includes_summary(test_client, db_session_factory):
    aid = f"a-{uuid.uuid4().hex[:6]}"
    async with db_session_factory() as s:
        s.add(Algorithm(
            id=aid, repo_url="https://x.com/r", name="x", source_path="/tmp/x",
            install_status="installed",
        ))
        await s.commit()

    resp = await test_client.get(f"/api/algorithms/{aid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == aid
    assert "summary" in body
    assert body["summary"]["status"] == "idle"
