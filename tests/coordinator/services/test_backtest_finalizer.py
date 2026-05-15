"""Tests for backtest_finalizer."""
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from coordinator.services.backtest_finalizer import (
    resample_to_daily, build_eoy_returns, build_monthly_matrix, finalize_run,
)


def _write_native_equity(path: Path, days: int = 252, start_value: float = 100.0):
    """Write a fake daily equity_native.parquet with `days` rows."""
    idx = pd.date_range("2023-01-02", periods=days, freq="D")
    values = [start_value * (1 + 0.001 * i) for i in range(days)]
    df = pd.DataFrame({
        "timestamp": idx,
        "portfolio_value": values,
        "cash": values,
    })
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, path)


def test_resample_to_daily_keeps_last_value_per_day(tmp_path):
    p = tmp_path / "eq.parquet"
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2024-01-02 09:30", "2024-01-02 16:00",
                                     "2024-01-03 09:30", "2024-01-03 16:00"]),
        "portfolio_value": [100.0, 101.0, 102.0, 103.0],
        "cash": [100.0, 101.0, 102.0, 103.0],
    })
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), p)
    daily = resample_to_daily(p)
    assert len(daily) == 2
    assert daily.iloc[0]["portfolio_value"] == 101.0
    assert daily.iloc[1]["portfolio_value"] == 103.0


def test_build_eoy_returns_per_year():
    idx = pd.date_range("2022-01-03", "2024-12-31", freq="D")
    pv = [100.0 * (1 + 0.0005 * i) for i in range(len(idx))]
    df = pd.DataFrame({"timestamp": idx, "portfolio_value": pv})
    bench = pd.Series([100.0 * (1 + 0.0003 * i) for i in range(len(idx))], index=idx)
    eoy = build_eoy_returns(df, bench)
    years = {row["year"] for row in eoy}
    assert years == {2022, 2023, 2024}


def test_build_monthly_matrix_shape():
    idx = pd.date_range("2023-01-01", "2024-12-31", freq="D")
    pv = [100.0 * (1 + 0.0005 * i) for i in range(len(idx))]
    df = pd.DataFrame({"timestamp": idx, "portfolio_value": pv})
    matrix = build_monthly_matrix(df)
    assert sorted(matrix["years"]) == [2023, 2024]
    assert all(len(c) == 3 for c in matrix["cells"])  # [year, month, ret_pct]


@pytest.mark.asyncio
async def test_finalize_run_populates_all_columns(tmp_path, test_app, db_session):
    """End-to-end: write native parquet, run finalizer, check row fields."""
    from coordinator.database.models import Algorithm, BacktestRun
    algo = Algorithm(name="t", repo_url="https://github.com/x/y", install_status="installed")
    db_session.add(algo); await db_session.flush()
    run = BacktestRun(
        algorithm_id=algo.id,
        date_range_start=datetime(2023, 1, 2, tzinfo=timezone.utc),
        date_range_end=datetime(2023, 12, 29, tzinfo=timezone.utc),
        initial_cash=100.0,
    )
    db_session.add(run); await db_session.commit()

    run_dir = tmp_path / run.id
    run_dir.mkdir()
    _write_native_equity(run_dir / "equity_native.parquet", days=252)
    # Empty trades file
    pq.write_table(pa.table({
        "timestamp": pa.array([], type=pa.timestamp("ns")),
        "symbol": pa.array([], type=pa.string()),
        "side": pa.array([], type=pa.string()),
        "quantity": pa.array([], type=pa.float64()),
        "realized_pnl": pa.array([], type=pa.float64()),
    }), run_dir / "trades.parquet")

    from coordinator.api.dependencies import get_container
    container = get_container()

    await finalize_run(
        run_id=run.id, run_dir=run_dir, session_factory=container.session_factory,
        benchmark_bar_df=None,
    )

    from sqlalchemy import select
    refreshed = (await db_session.execute(
        select(BacktestRun).where(BacktestRun.id == run.id)
    )).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.key_metrics is not None
    assert "strategy" in refreshed.key_metrics
    assert refreshed.equity_curve is not None
    assert len(refreshed.equity_curve) > 0
    assert refreshed.monthly_returns_matrix is not None
    assert refreshed.drawdown_curve is not None
    assert refreshed.eoy_returns is not None
