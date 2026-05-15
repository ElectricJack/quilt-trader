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
