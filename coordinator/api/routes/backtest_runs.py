from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db, get_container
from coordinator.api.routes.data import _provider_availability
from coordinator.api.serialization import to_iso_utc
from coordinator.database.models import BacktestRun, Algorithm, ParameterSet

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/backtest-runs", tags=["backtest-runs"])


class TradingFeeIn(BaseModel):
    flat_fee: float = 0.0
    percent_fee: float = 0.0
    maker: bool = True
    taker: bool = True


class SlippageModelIn(BaseModel):
    market_bps: float = 5.0
    limit_bps: float = 0.0
    use_bar_range: bool = False
    volume_impact_bps_per_pct: float = 0.0


class BacktestRunCreate(BaseModel):
    algorithm_id: str
    date_range_start: datetime
    date_range_end: datetime
    initial_cash: float = 100_000.0
    config_overrides: Optional[dict] = None
    parameter_set_id: Optional[str] = None
    buy_trading_fees: Optional[list[TradingFeeIn]] = None
    sell_trading_fees: Optional[list[TradingFeeIn]] = None
    slippage_model: Optional[SlippageModelIn] = None
    benchmark_symbol: Optional[str] = None
    benchmark_source: Optional[str] = None


def _to_response(r: BacktestRun) -> dict:
    return {
        "id": r.id, "algorithm_id": r.algorithm_id, "parameter_set_id": r.parameter_set_id, "status": r.status,
        "date_range_start": to_iso_utc(r.date_range_start),
        "date_range_end": to_iso_utc(r.date_range_end),
        "initial_cash": r.initial_cash,
        "config_overrides": r.config_overrides,
        "buy_trading_fees": r.buy_trading_fees, "sell_trading_fees": r.sell_trading_fees,
        "slippage_model": r.slippage_model,
        "benchmark_symbol": r.benchmark_symbol, "benchmark_source": r.benchmark_source,
        "progress_message": r.progress_message, "progress_pct": r.progress_pct,
        "error_message": r.error_message,
        "total_return": r.total_return, "cagr": r.cagr, "volatility": r.volatility,
        "sharpe_ratio": r.sharpe_ratio, "sortino_ratio": r.sortino_ratio,
        "calmar_ratio": r.calmar_ratio, "max_drawdown": r.max_drawdown,
        "max_drawdown_date": to_iso_utc(r.max_drawdown_date),
        "romad": r.romad, "total_fees_paid": r.total_fees_paid,
        "total_slippage_dollars": r.total_slippage_dollars,
        "trade_count": r.trade_count, "win_rate": r.win_rate,
        "profit_factor": r.profit_factor, "avg_win": r.avg_win, "avg_loss": r.avg_loss,
        "expectancy": r.expectancy,
        "longest_drawdown_days": r.longest_drawdown_days,
        "longest_winning_streak": r.longest_winning_streak,
        "longest_losing_streak": r.longest_losing_streak,
        "drawdown_periods": r.drawdown_periods,
        "started_at": to_iso_utc(r.started_at),
        "completed_at": to_iso_utc(r.completed_at),
        "created_at": to_iso_utc(r.created_at),
    }


def _dispatch_runner(container, run_id: str):
    """Spawn the runner as a background asyncio task."""
    runner = container.backtest_runner
    asyncio.create_task(runner.run(run_id))


@router.post("", status_code=201)
async def create_run(body: BacktestRunCreate, db: AsyncSession = Depends(get_db)):
    algo = (await db.execute(select(Algorithm).where(Algorithm.id == body.algorithm_id))).scalar_one_or_none()
    if algo is None:
        raise HTTPException(404, detail=f"Algorithm not found: {body.algorithm_id}")

    config_overrides = body.config_overrides
    parameter_set_id = body.parameter_set_id
    if parameter_set_id is not None:
        ps = (await db.execute(
            select(ParameterSet).where(
                ParameterSet.algorithm_id == algo.id,
                ParameterSet.id == parameter_set_id,
            )
        )).scalar_one_or_none()
        if ps is None:
            raise HTTPException(404, detail="Parameter set not found")
        config_overrides = ps.config_values

    # Validate benchmark_source against current provider availability (I17).
    if body.benchmark_source:
        matrix = await _provider_availability(db)
        entry = next((p for p in matrix if p["name"] == body.benchmark_source), None)
        if entry is None or not entry["available"]:
            reason = (entry or {}).get("reason") or "provider not registered"
            raise HTTPException(
                422,
                detail=f"benchmark_source {body.benchmark_source!r} is not available: {reason}",
            )

    run = BacktestRun(
        algorithm_id=body.algorithm_id,
        date_range_start=body.date_range_start,
        date_range_end=body.date_range_end,
        initial_cash=body.initial_cash,
        config_overrides=config_overrides,
        parameter_set_id=parameter_set_id,
        buy_trading_fees=[f.model_dump() for f in body.buy_trading_fees] if body.buy_trading_fees else None,
        sell_trading_fees=[f.model_dump() for f in body.sell_trading_fees] if body.sell_trading_fees else None,
        slippage_model=body.slippage_model.model_dump() if body.slippage_model else None,
        benchmark_symbol=body.benchmark_symbol,
        benchmark_source=body.benchmark_source,
    )
    db.add(run)
    await db.flush()
    run_id = run.id
    response = _to_response(run)
    # Commit BEFORE dispatching the background runner so its session can
    # read the row. get_db's auto-commit fires after the handler returns,
    # but the background task may start before that.
    await db.commit()
    _dispatch_runner(get_container(), run_id)
    return response


@router.get("")
async def list_runs(
    algorithm_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    q = select(BacktestRun)
    if algorithm_id:
        q = q.where(BacktestRun.algorithm_id == algorithm_id)
    q = q.order_by(desc(BacktestRun.created_at)).limit(limit).offset(offset)
    rows = (await db.execute(q)).scalars().all()
    return [_to_response(r) for r in rows]


@router.get("/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, detail="Backtest run not found")
    return _to_response(r)


@router.get("/{run_id}/report")
async def get_report(run_id: str, db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, detail="Backtest run not found")
    return {
        "id": r.id,
        "algorithm_id": r.algorithm_id,
        "status": r.status,
        "date_range_start": to_iso_utc(r.date_range_start),
        "date_range_end": to_iso_utc(r.date_range_end),
        "initial_cash": r.initial_cash,
        "config_overrides": r.config_overrides,
        "benchmark_symbol": r.benchmark_symbol,
        "benchmark_source": r.benchmark_source,
        "progress_message": r.progress_message,
        "progress_pct": r.progress_pct,
        "key_metrics": r.key_metrics,
        "equity_curve": r.equity_curve,
        "benchmark_equity_curve": r.benchmark_equity_curve,
        "drawdown_curve": r.drawdown_curve,
        "rolling_metrics": r.rolling_metrics,
        "monthly_returns_matrix": r.monthly_returns_matrix,
        "eoy_returns": r.eoy_returns,
        "drawdown_periods": r.drawdown_periods,
    }


@router.get("/{run_id}/trades")
async def get_trades(
    run_id: str,
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    r = (await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, detail="Backtest run not found")
    # New runs stream trades to data/backtests/{id}/trades.parquet via the
    # writer thread; fall back to the legacy r.trades JSON column for runs
    # completed before that pipeline shipped.
    parquet_path = Path("data/backtests") / run_id / "trades.parquet"
    if parquet_path.exists():
        import pyarrow.parquet as _pq
        import json as _json
        df = _pq.read_table(parquet_path).to_pandas()
        total = int(len(df))
        sliced = df.iloc[offset:offset + limit]
        items = []
        for _, row in sliced.iterrows():
            ts = row["timestamp"]
            try:
                fb = _json.loads(row["fee_breakdown"]) if row.get("fee_breakdown") else {}
            except Exception:
                fb = {}
            items.append({
                "timestamp": to_iso_utc(ts) if hasattr(ts, "isoformat") else str(ts),
                "symbol": row["symbol"],
                "asset_type": row.get("asset_type"),
                "side": row["side"],
                "quantity": float(row["quantity"]),
                "requested_price": (None if pd.isna(row.get("requested_price")) else float(row["requested_price"])),
                "fill_price": (None if pd.isna(row.get("fill_price")) else float(row["fill_price"])),
                "slippage_dollars": (None if pd.isna(row.get("slippage_dollars")) else float(row["slippage_dollars"])),
                "slippage_bps_applied": (None if pd.isna(row.get("slippage_bps_applied")) else float(row["slippage_bps_applied"])),
                "fees": (None if pd.isna(row.get("fees")) else float(row["fees"])),
                "fee_breakdown": fb,
                "signal_id": row.get("signal_id"),
                "realized_pnl": (None if pd.isna(row.get("realized_pnl")) else float(row["realized_pnl"])),
            })
        return {"total": total, "items": items}
    # Legacy fallback
    trades = r.trades or []
    return {"total": len(trades), "items": trades[offset:offset + limit]}


@router.delete("/{run_id}", status_code=204)
async def delete_run(run_id: str, db: AsyncSession = Depends(get_db)):
    import shutil
    r = (await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, detail="Backtest run not found")
    container = get_container()
    if hasattr(container, "cancel_backtest"):
        container.cancel_backtest(run_id)
    # Remove the entire run output directory
    run_dir = Path("data/backtests") / run_id
    try:
        shutil.rmtree(run_dir, ignore_errors=True)
    except Exception:
        logger.exception("Failed to remove run dir %s", run_dir)
    await db.delete(r)


@router.get("/{run_id}/equity")
async def get_equity_window(
    run_id: str,
    from_: datetime = Query(..., alias="from"),
    to: datetime = Query(...),
    resolution: str = Query("auto", pattern="^(1min|1hour|1day|auto)$"),
    db: AsyncSession = Depends(get_db),
):
    r = (await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, detail="Backtest run not found")

    run_dir = Path("data/backtests") / run_id
    chosen = resolution
    if resolution == "auto":
        # Pick the highest resolution available that produces ≤5000 points in the window.
        days = max(1, (to - from_).days)
        if days < 3 and (run_dir / "equity_1min.parquet").exists():
            chosen = "1min"
        elif days < 60 and (run_dir / "equity_1hour.parquet").exists():
            chosen = "1hour"
        else:
            chosen = "1day"

    pq_path = run_dir / f"equity_{chosen}.parquet"
    if not pq_path.exists():
        raise HTTPException(404, detail=f"No {chosen} parquet for this run")
    import pyarrow.parquet as _pq
    df = _pq.read_table(pq_path).to_pandas()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if df["timestamp"].dt.tz is not None:
        df["timestamp"] = df["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None)
    from_naive = pd.Timestamp(from_).tz_localize(None) if pd.Timestamp(from_).tz is not None else pd.Timestamp(from_)
    to_naive = pd.Timestamp(to).tz_localize(None) if pd.Timestamp(to).tz is not None else pd.Timestamp(to)
    mask = (df["timestamp"] >= from_naive) & (df["timestamp"] <= to_naive)
    sliced = df.loc[mask]
    return {
        "resolution": chosen,
        "items": [
            {"ts": to_iso_utc(row["timestamp"]), "portfolio_value": float(row["portfolio_value"]), "cash": float(row.get("cash", 0.0))}
            for _, row in sliced.iterrows()
        ],
    }
