from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db, get_container
from coordinator.database.models import BacktestRun, Algorithm

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
    buy_trading_fees: Optional[list[TradingFeeIn]] = None
    sell_trading_fees: Optional[list[TradingFeeIn]] = None
    slippage_model: Optional[SlippageModelIn] = None
    benchmark_symbol: Optional[str] = None
    benchmark_source: Optional[str] = None


def _to_response(r: BacktestRun) -> dict:
    return {
        "id": r.id, "algorithm_id": r.algorithm_id, "status": r.status,
        "date_range_start": r.date_range_start.isoformat() if r.date_range_start else None,
        "date_range_end": r.date_range_end.isoformat() if r.date_range_end else None,
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
        "max_drawdown_date": r.max_drawdown_date.isoformat() if r.max_drawdown_date else None,
        "romad": r.romad, "total_fees_paid": r.total_fees_paid,
        "total_slippage_dollars": r.total_slippage_dollars,
        "trade_count": r.trade_count, "win_rate": r.win_rate,
        "profit_factor": r.profit_factor, "avg_win": r.avg_win, "avg_loss": r.avg_loss,
        "expectancy": r.expectancy,
        "longest_drawdown_days": r.longest_drawdown_days,
        "longest_winning_streak": r.longest_winning_streak,
        "longest_losing_streak": r.longest_losing_streak,
        "drawdown_periods": r.drawdown_periods,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
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
    run = BacktestRun(
        algorithm_id=body.algorithm_id,
        date_range_start=body.date_range_start,
        date_range_end=body.date_range_end,
        initial_cash=body.initial_cash,
        config_overrides=body.config_overrides,
        buy_trading_fees=[f.model_dump() for f in body.buy_trading_fees] if body.buy_trading_fees else None,
        sell_trading_fees=[f.model_dump() for f in body.sell_trading_fees] if body.sell_trading_fees else None,
        slippage_model=body.slippage_model.model_dump() if body.slippage_model else None,
        benchmark_symbol=body.benchmark_symbol,
        benchmark_source=body.benchmark_source,
    )
    db.add(run)
    await db.flush()
    _dispatch_runner(get_container(), run.id)
    return _to_response(run)


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
        "date_range_start": r.date_range_start.isoformat() if r.date_range_start else None,
        "date_range_end": r.date_range_end.isoformat() if r.date_range_end else None,
        "initial_cash": r.initial_cash,
        "config_overrides": r.config_overrides,
        "benchmark_symbol": r.benchmark_symbol,
        "benchmark_source": r.benchmark_source,
        "key_metrics": r.key_metrics,
        "equity_curve": r.equity_curve,
        "benchmark_equity_curve": r.benchmark_equity_curve,
        "drawdown_curve": r.drawdown_curve,
        "rolling_metrics": r.rolling_metrics,
        "monthly_returns_matrix": r.monthly_returns_matrix,
        "eoy_returns": r.eoy_returns,
        "drawdown_periods": r.drawdown_periods,
    }


@router.get("/{run_id}/equity-curve")
async def get_equity_curve(run_id: str, db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, detail="Backtest run not found")

    benchmark: list[dict] = []
    # Compute the benchmark curve on demand from the cached parquet so a stale
    # equity_curve never blocks the page. Normalize to initial_cash so the
    # benchmark and strategy share the same starting value.
    # NOTE: use `is not None` for initial_cash because 0 is a valid (if odd)
    # input and we still want to compute the benchmark curve in that case.
    if (
        r.benchmark_symbol
        and r.benchmark_source
        and r.equity_curve
        and r.initial_cash is not None
        and r.initial_cash > 0
    ):
        try:
            import pandas as pd
            container = get_container()
            ds = container.data_service
            df = ds.load_market_data(r.benchmark_source, r.benchmark_symbol, "1day")
            if df is not None and not df.empty:
                ts = pd.to_datetime(df["timestamp"])
                if hasattr(ts.dt, "tz") and ts.dt.tz is not None:
                    ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
                df = df.copy()
                df["_ts"] = ts
                start = pd.Timestamp(r.date_range_start)
                if start.tz is not None:
                    start = start.tz_convert("UTC").tz_localize(None)
                end = pd.Timestamp(r.date_range_end)
                if end.tz is not None:
                    end = end.tz_convert("UTC").tz_localize(None)
                df = df[(df["_ts"] >= start) & (df["_ts"] <= end)].reset_index(drop=True)
                if not df.empty:
                    first_close = float(df["close"].iloc[0])
                    if first_close > 0:
                        for _, row in df.iterrows():
                            value = (float(row["close"]) / first_close) * r.initial_cash
                            benchmark.append({
                                "timestamp": row["_ts"].isoformat(),
                                "value": value,
                            })
        except Exception:
            logger.exception("Failed to compute benchmark curve for run %s", run_id)
            benchmark = []

    return {"items": r.equity_curve or [], "benchmark": benchmark}


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
    trades = r.trades or []
    return {"total": len(trades), "items": trades[offset:offset + limit]}


@router.get("/{run_id}/tearsheet")
async def get_tearsheet(run_id: str, db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one_or_none()
    if r is None or not r.tearsheet_path or not Path(r.tearsheet_path).exists():
        raise HTTPException(404, detail="Tearsheet not available")
    return FileResponse(
        r.tearsheet_path,
        media_type="text/html",
        filename=f"backtest_{run_id}_tearsheet.html",
    )


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
