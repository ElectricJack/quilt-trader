"""Tests for the /report and /equity endpoints."""
from datetime import datetime, timezone
import pytest


@pytest.mark.asyncio
async def test_get_report_returns_all_payload_fields(client, db_session):
    from coordinator.database.models import Algorithm, BacktestRun
    algo = Algorithm(name="t", repo_url="https://github.com/x/y", install_status="installed")
    db_session.add(algo); await db_session.flush()
    run = BacktestRun(
        algorithm_id=algo.id,
        date_range_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        date_range_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        initial_cash=100.0,
        config_overrides={"x": 1},
        key_metrics={"strategy": {"sharpe_ratio": 1.4}, "benchmark": {"sharpe_ratio": 0.7}},
        equity_curve=[{"timestamp": "2024-01-01T00:00:00", "portfolio_value": 100.0, "cash": 100.0}],
        benchmark_equity_curve=[{"timestamp": "2024-01-01T00:00:00", "value": 100.0}],
        drawdown_curve=[{"timestamp": "2024-01-01T00:00:00", "drawdown_pct": 0.0}],
        rolling_metrics={"window_days": 90, "points": []},
        monthly_returns_matrix={"years": [2024], "cells": []},
        eoy_returns=[{"year": 2024, "strategy_pct": 0.0, "benchmark_pct": 0.0, "multiplier": None, "won": False}],
        drawdown_periods=[],
    )
    db_session.add(run); await db_session.commit()

    resp = await client.get(f"/api/backtest-runs/{run.id}/report")
    assert resp.status_code == 200
    body = resp.json()
    for key in (
        "id", "status", "config_overrides", "key_metrics", "equity_curve",
        "benchmark_equity_curve", "drawdown_curve", "rolling_metrics",
        "monthly_returns_matrix", "eoy_returns", "drawdown_periods",
    ):
        assert key in body, f"missing key: {key}"


@pytest.mark.asyncio
async def test_get_report_404_for_missing_run(client):
    resp = await client.get("/api/backtest-runs/nope/report")
    assert resp.status_code == 404


import pyarrow as pa
import pyarrow.parquet as pq
import pandas as pd
from pathlib import Path


@pytest.mark.asyncio
async def test_equity_endpoint_returns_window_at_requested_resolution(
    client, db_session, tmp_path, monkeypatch,
):
    from coordinator.database.models import Algorithm, BacktestRun
    monkeypatch.chdir(tmp_path)
    algo = Algorithm(name="t", repo_url="https://github.com/x/y", install_status="installed")
    db_session.add(algo); await db_session.flush()
    run = BacktestRun(
        algorithm_id=algo.id,
        date_range_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        date_range_end=datetime(2024, 1, 31, tzinfo=timezone.utc),
        initial_cash=100.0,
    )
    db_session.add(run); await db_session.commit()

    run_dir = tmp_path / "data" / "backtests" / run.id
    run_dir.mkdir(parents=True)
    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-02", periods=10, freq="D"),
        "portfolio_value": [100.0 + i for i in range(10)],
        "cash": [100.0] * 10,
    })
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), run_dir / "equity_1day.parquet")

    resp = await client.get(
        f"/api/backtest-runs/{run.id}/equity"
        "?from=2024-01-04T00:00:00&to=2024-01-07T00:00:00&resolution=1day"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"] == "1day"
    # 4 days inclusive
    assert len(body["items"]) == 4
