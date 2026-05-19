"""Backtest finalizer.

Reads the on-disk parquet pyramid produced by ParquetWriterThread,
computes the report payload (metrics, rolling series, monthly matrix,
EOY, drawdown curve, top-N drawdowns, daily equity mirror), and persists
to the BacktestRun row in a single transaction.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import quantstats as qs
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from coordinator.services.backtest_metrics_qs import (
    compute_all,
)

logger = logging.getLogger(__name__)

ROLLING_WINDOW_DAYS = 90


# ---- Resample / read helpers ----

def resample_to_daily(equity_native_path: Path) -> pd.DataFrame:
    table = pq.read_table(equity_native_path)
    df = table.to_pandas()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if df["timestamp"].dt.tz is not None:
        df["timestamp"] = df["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None)
    df = df.set_index("timestamp")
    daily = df.resample("D").last().dropna()
    daily = daily.reset_index()
    return daily


def write_daily_parquet(daily_df: pd.DataFrame, out_path: Path) -> None:
    import pyarrow as pa
    table = pa.Table.from_pandas(daily_df, preserve_index=False)
    pq.write_table(table, out_path, compression="snappy")


# ---- Compute helpers ----

def _returns_from_pv(daily_df: pd.DataFrame) -> pd.Series:
    s = daily_df.set_index("timestamp")["portfolio_value"].pct_change().fillna(0)
    if hasattr(s.index, "tz") and s.index.tz is not None:
        s.index = s.index.tz_localize(None)
    return s


def build_drawdown_curve(daily_df: pd.DataFrame) -> list[dict]:
    s = _returns_from_pv(daily_df)
    if s.empty:
        return []
    dd = qs.stats.to_drawdown_series(s)
    return [
        {"timestamp": ts.isoformat(), "drawdown_pct": float(v)}
        for ts, v in dd.items()
    ]


def build_monthly_matrix(daily_df: pd.DataFrame) -> dict:
    s = _returns_from_pv(daily_df)
    if s.empty:
        return {"years": [], "cells": []}
    monthly = qs.stats.monthly_returns(s)  # DataFrame: rows=years (str), cols=JAN..DEC + EOY
    years = [int(y) for y in monthly.index.tolist()]
    cells: list[list] = []
    # Enumerate only the 12 month columns (skip "EOY" which is index 13)
    month_cols = [col for col in monthly.columns if col != "EOY"]
    for y in monthly.index:
        for m_idx, col in enumerate(month_cols, start=1):
            v = monthly.loc[y, col]
            if pd.notna(v):
                cells.append([int(y), int(m_idx), float(v)])
    return {"years": years, "cells": cells}


def build_eoy_returns(
    daily_df: pd.DataFrame, benchmark_pv: Optional[pd.Series],
) -> list[dict]:
    pv = daily_df.set_index("timestamp")["portfolio_value"]
    yearly = pv.groupby(pv.index.year).agg(["first", "last"])
    out: list[dict] = []
    for year, row in yearly.iterrows():
        strat_pct = float(row["last"] / row["first"] - 1.0) * 100.0
        bench_pct: Optional[float] = None
        if benchmark_pv is not None and not benchmark_pv.empty:
            yr_mask = (benchmark_pv.index.year == year)
            if yr_mask.any():
                yr_pv = benchmark_pv.loc[yr_mask]
                bench_pct = float(yr_pv.iloc[-1] / yr_pv.iloc[0] - 1.0) * 100.0
        multiplier = (strat_pct / bench_pct) if (bench_pct not in (None, 0.0)) else None
        out.append({
            "year": int(year),
            "strategy_pct": strat_pct,
            "benchmark_pct": bench_pct,
            "multiplier": multiplier,
            "won": (bench_pct is not None and strat_pct > bench_pct),
        })
    return out


def build_rolling_metrics(
    strat_returns: pd.Series,
    bench_returns: Optional[pd.Series],
    window: int = ROLLING_WINDOW_DAYS,
) -> dict:
    if strat_returns.empty:
        return {"window_days": window, "points": []}
    points: list[dict] = []
    rolling_sharpe = qs.stats.rolling_sharpe(strat_returns, rolling_period=window)
    rolling_sortino = qs.stats.rolling_sortino(strat_returns, rolling_period=window)
    rolling_vol = qs.stats.rolling_volatility(strat_returns, rolling_period=window)
    rolling_beta: Optional[pd.Series] = None
    if bench_returns is not None and not bench_returns.empty:
        joined = pd.concat([strat_returns, bench_returns], axis=1, join="inner").dropna()
        joined.columns = ["s", "b"]
        if len(joined) >= window:
            rolling_beta = (
                joined["s"].rolling(window).cov(joined["b"]) /
                joined["b"].rolling(window).var()
            )
    for ts in strat_returns.index:
        pt = {
            "timestamp": ts.isoformat(),
            "sharpe": _safe(rolling_sharpe, ts),
            "sortino": _safe(rolling_sortino, ts),
            "vol": _safe(rolling_vol, ts),
            "beta": _safe(rolling_beta, ts) if rolling_beta is not None else None,
        }
        points.append(pt)
    return {"window_days": window, "points": points}


def _safe(series: Optional[pd.Series], idx) -> Optional[float]:
    if series is None or idx not in series.index:
        return None
    v = series.loc[idx]
    if pd.isna(v) or np.isinf(v):
        return None
    return float(v)


def _add_period_returns(metrics: dict, daily_df: pd.DataFrame) -> dict:
    """Add ytd / 1y / 3y to a metrics dict (computed from the equity curve)."""
    from coordinator.services.backtest_metrics_qs import _period_return
    metrics["ytd"] = _period_return(daily_df, ytd=True)
    metrics["1y"] = _period_return(daily_df, days=365)
    metrics["3y"] = _period_return(daily_df, days=365 * 3, annualize_after_days=365 * 3)
    return metrics


def _add_benchmark_metrics(
    metrics: dict, strat_returns: pd.Series, bench_returns: Optional[pd.Series],
) -> dict:
    """Add beta / alpha / correlation against the benchmark to a metrics dict."""
    if bench_returns is None or bench_returns.empty:
        metrics["beta"] = None
        metrics["alpha"] = None
        metrics["correlation"] = None
        return metrics
    joined = pd.concat([strat_returns, bench_returns], axis=1, join="inner").dropna()
    joined.columns = ["s", "b"]
    if len(joined) < 2:
        metrics["beta"] = None
        metrics["alpha"] = None
        metrics["correlation"] = None
        return metrics
    var_b = float(joined["b"].var())
    cov_sb = float(joined["s"].cov(joined["b"]))
    beta = cov_sb / var_b if var_b > 0 else None
    alpha = float(joined["s"].mean() - (beta or 0.0) * joined["b"].mean()) * 252  # ann.
    corr = float(joined["s"].corr(joined["b"]))
    metrics["beta"] = beta
    metrics["alpha"] = alpha
    metrics["correlation"] = corr if not np.isnan(corr) else None
    return metrics


def build_key_metrics(
    daily_df: pd.DataFrame,
    benchmark_daily_df: Optional[pd.DataFrame],
    initial_cash: float,
    risk_free_rate: float = 0.04,
    trades: Optional[list] = None,
) -> dict:
    strat = compute_all(_with_return(daily_df), trades=trades or [], initial_cash=initial_cash, risk_free_rate=risk_free_rate)
    strat_returns = _returns_from_pv(daily_df)
    bench: dict = {}
    bench_returns: Optional[pd.Series] = None
    if benchmark_daily_df is not None and not benchmark_daily_df.empty:
        bench_pv = benchmark_daily_df.set_index("timestamp")["portfolio_value"]
        bench_init = float(bench_pv.iloc[0])
        bench = compute_all(_with_return(benchmark_daily_df), trades=[], initial_cash=bench_init, risk_free_rate=risk_free_rate)
        bench_returns = _returns_from_pv(benchmark_daily_df)
        _add_period_returns(bench, benchmark_daily_df)
    _add_period_returns(strat, daily_df)
    _add_benchmark_metrics(strat, strat_returns, bench_returns)
    # Drop date objects + drawdown_periods (own column); JSON-safe.
    strat = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in strat.items() if k != "drawdown_periods"}
    bench = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in bench.items() if k != "drawdown_periods"}
    return {"strategy": strat, "benchmark": bench}


def _with_return(daily_df: pd.DataFrame) -> pd.DataFrame:
    df = daily_df.copy().set_index("timestamp")
    df["return"] = df["portfolio_value"].pct_change().fillna(0)
    return df


# ---- Main entrypoint ----

async def finalize_run(
    *, run_id: str, run_dir: Path,
    session_factory: async_sessionmaker[AsyncSession],
    benchmark_bar_df: Optional[pd.DataFrame],
) -> None:
    """Read native parquet, build all report payloads, persist to row."""
    from coordinator.database.models import BacktestRun

    eq_native = run_dir / "equity_native.parquet"
    if not eq_native.exists():
        raise FileNotFoundError(f"Missing native equity parquet at {eq_native}")

    daily_df = resample_to_daily(eq_native)
    write_daily_parquet(daily_df, run_dir / "equity_1day.parquet")

    bench_daily_df: Optional[pd.DataFrame] = None
    if benchmark_bar_df is not None and not benchmark_bar_df.empty:
        bench_pv_normalized = _normalize_benchmark(benchmark_bar_df, daily_df)
        bench_daily_df = pd.DataFrame({
            "timestamp": bench_pv_normalized.index,
            "portfolio_value": bench_pv_normalized.values,
        })
        write_daily_parquet(bench_daily_df, run_dir / "benchmark_1day.parquet")

    # Compute all payloads
    strat_returns = _returns_from_pv(daily_df)
    bench_returns = (
        _returns_from_pv(bench_daily_df) if bench_daily_df is not None else None
    )

    async with session_factory() as session:
        r = (await session.execute(
            select(BacktestRun).where(BacktestRun.id == run_id)
        )).scalar_one()

        r.equity_curve = [
            {"timestamp": ts.isoformat(), "portfolio_value": float(pv), "cash": float(c)}
            for ts, pv, c in zip(daily_df["timestamp"], daily_df["portfolio_value"], daily_df["cash"])
        ]
        r.benchmark_equity_curve = (
            [{"timestamp": ts.isoformat(), "value": float(v)}
             for ts, v in zip(bench_daily_df["timestamp"], bench_daily_df["portfolio_value"])]
            if bench_daily_df is not None else None
        )
        r.drawdown_curve = build_drawdown_curve(daily_df)
        r.monthly_returns_matrix = build_monthly_matrix(daily_df)
        r.eoy_returns = build_eoy_returns(
            daily_df,
            (bench_daily_df.set_index("timestamp")["portfolio_value"] if bench_daily_df is not None else None),
        )
        r.rolling_metrics = build_rolling_metrics(strat_returns, bench_returns)
        # Load trades from parquet so trade_count/win_rate are computed
        trades_path = run_dir / "trades.parquet"
        trades_list: list = []
        if trades_path.exists():
            trades_df = pd.read_parquet(trades_path)
            trades_list = trades_df.to_dict(orient="records")
        r.key_metrics = build_key_metrics(
            daily_df, bench_daily_df, initial_cash=r.initial_cash, trades=trades_list,
        )
        # Top-10 drawdowns
        from coordinator.services.backtest_metrics_qs import top_n_drawdowns
        r.drawdown_periods = top_n_drawdowns(_with_return(daily_df), n=10)

        # Mirror the headline strategy metrics into the flat columns so the
        # /backtests list view (which reads BacktestRunRecord, not /report)
        # shows return/sharpe/trades for new runs.
        km = r.key_metrics["strategy"]
        r.total_return = km.get("total_return")
        r.cagr = km.get("cagr")
        r.volatility = km.get("volatility")
        r.sharpe_ratio = km.get("sharpe_ratio")
        r.sortino_ratio = km.get("sortino_ratio")
        r.calmar_ratio = km.get("calmar_ratio")
        r.max_drawdown = km.get("max_drawdown")
        mdd_date = km.get("max_drawdown_date")
        r.max_drawdown_date = (
            datetime.fromisoformat(mdd_date) if isinstance(mdd_date, str) else mdd_date
        )
        r.romad = km.get("romad")
        r.longest_drawdown_days = km.get("longest_drawdown_days")
        r.trade_count = km.get("trade_count")

        await session.commit()


def _normalize_benchmark(bench_bars: pd.DataFrame, daily_df: pd.DataFrame) -> pd.Series:
    """Normalize benchmark close to start at the strategy's initial portfolio value."""
    df = bench_bars.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if df["timestamp"].dt.tz is not None:
        df["timestamp"] = df["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None)
    df = df.sort_values("timestamp").set_index("timestamp")
    initial = float(daily_df["portfolio_value"].iloc[0])
    first_close = float(df["close"].iloc[0])
    if first_close == 0:
        return pd.Series(dtype=float)
    return (df["close"] / first_close) * initial
