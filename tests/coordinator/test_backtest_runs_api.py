import pytest
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_create_backtest_run_starts_task(client, db_session, monkeypatch):
    from coordinator.database.models import Algorithm
    algo = Algorithm(name="t", repo_url="https://e/t", install_status="installed")
    db_session.add(algo); await db_session.commit()

    # Stub runner.run to avoid actually running it
    from coordinator.api.routes import backtest_runs as routes
    async def fake_run(run_id): pass
    monkeypatch.setattr(routes, "_dispatch_runner", lambda app, run_id: None)

    body = {
        "algorithm_id": algo.id,
        "date_range_start": "2024-01-01T00:00:00+00:00",
        "date_range_end": "2024-02-01T00:00:00+00:00",
        "initial_cash": 25_000.0,
        "slippage_model": {"market_bps": 5.0},
        "benchmark_symbol": "SPY",
        "benchmark_source": "polygon",
    }
    r = await client.post("/api/backtest-runs", json=body)
    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "queued"
    assert data["initial_cash"] == 25_000.0
    assert data["algorithm_id"] == algo.id


@pytest.mark.asyncio
async def test_list_backtest_runs(client, db_session):
    r = await client.get("/api/backtest-runs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_get_404(client):
    r = await client.get("/api/backtest-runs/missing")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_run_removes_run_directory(client, db_session, tmp_path, monkeypatch):
    """DELETE should rmtree data/backtests/{run_id}/."""
    from coordinator.database.models import Algorithm, BacktestRun
    monkeypatch.chdir(tmp_path)
    algo = Algorithm(name="t", repo_url="https://github.com/x/y", install_status="installed")
    db_session.add(algo); await db_session.flush()
    run = BacktestRun(
        algorithm_id=algo.id,
        date_range_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        date_range_end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        initial_cash=100.0,
    )
    db_session.add(run); await db_session.commit()

    run_dir = tmp_path / "data" / "backtests" / run.id
    run_dir.mkdir(parents=True)
    (run_dir / "equity_native.parquet").write_bytes(b"stub")

    resp = await client.delete(f"/api/backtest-runs/{run.id}")
    assert resp.status_code == 204
    assert not run_dir.exists()
