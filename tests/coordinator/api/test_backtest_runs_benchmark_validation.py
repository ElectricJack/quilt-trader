"""Tests for benchmark_source validation in POST /api/backtest-runs (Task A2)."""
import pytest
from httpx import ASGITransport, AsyncClient

from coordinator.database.models import Algorithm


_RUN_PAYLOAD = {
    "algorithm_id": "algo-bv-1",
    "date_range_start": "2024-01-01T00:00:00Z",
    "date_range_end": "2024-06-01T00:00:00Z",
    "initial_cash": 100000.0,
}


@pytest.fixture
def no_dispatch(monkeypatch):
    import coordinator.api.routes.backtest_runs as br_mod
    monkeypatch.setattr(br_mod, "_dispatch_runner", lambda container, run_id: None)


async def _seed_algo(test_app):
    from coordinator.api.dependencies import get_container
    container = get_container()
    async with container.session_factory() as s:
        s.add(Algorithm(
            id="algo-bv-1",
            repo_url="https://github.com/x/y",
            name="y",
        ))
        await s.commit()


@pytest.mark.asyncio
async def test_create_run_rejects_unavailable_benchmark_source(test_app, no_dispatch):
    """POST with benchmark_source='theta' (no credentials) returns 422."""
    await _seed_algo(test_app)

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        r = await ac.post("/api/backtest-runs", json={
            **_RUN_PAYLOAD,
            "benchmark_source": "theta",
        })

    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "theta" in detail


@pytest.mark.asyncio
async def test_create_run_accepts_available_benchmark_source(test_app, no_dispatch):
    """POST with benchmark_source='yfinance' (always available) returns 201."""
    await _seed_algo(test_app)

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        r = await ac.post("/api/backtest-runs", json={
            **_RUN_PAYLOAD,
            "benchmark_source": "yfinance",
        })

    assert r.status_code == 201


@pytest.mark.asyncio
async def test_create_run_no_benchmark_is_unaffected(test_app, no_dispatch):
    """POST without benchmark_source returns 201 (validation is not triggered)."""
    await _seed_algo(test_app)

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        r = await ac.post("/api/backtest-runs", json=_RUN_PAYLOAD)

    assert r.status_code == 201
