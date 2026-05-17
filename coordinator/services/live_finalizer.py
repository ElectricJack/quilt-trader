"""Periodic finalizer for live deployments.

Every interval_seconds:
  1. Iterate all deployments with status == "running".
  2. Flush LiveSampleSink buffers.
  3. For each deployment, read per-run parquets in chronological order,
     stitch with NaN gap rows between consecutive runs, resample daily,
     compute the report payload using existing backtest_finalizer helpers,
     and upsert AlgorithmDeploymentReport.

Reuses backtest_finalizer helpers so live and backtest reports share shape.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import pyarrow.parquet as pq
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from coordinator.database.models import (
    AlgorithmDeploymentReport, AlgorithmInstance, AlgorithmRun,
)
from coordinator.services import backtest_finalizer as bf
from coordinator.services.live_sample_sink import LiveSampleSink

logger = logging.getLogger(__name__)


class LiveFinalizer:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession],
        sink: LiveSampleSink, base_dir: Path,
        interval_seconds: int = 15,
    ) -> None:
        self._sf = session_factory
        self._sink = sink
        self._base = Path(base_dir)
        self._interval = interval_seconds

    async def run_loop(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception:
                logger.exception("LiveFinalizer tick failed")
            await asyncio.sleep(self._interval)

    async def _tick(self) -> None:
        async with self._sf() as session:
            deps = (await session.execute(
                select(AlgorithmInstance.id).where(AlgorithmInstance.status == "running")
            )).scalars().all()
        await self._sink.flush()
        for did in deps:
            try:
                await self.finalize_one(did)
            except Exception:
                logger.exception("Failed to finalize deployment %s", did)

    async def finalize_one(self, deployment_id: str) -> None:
        await self._sink.flush()
        async with self._sf() as session:
            runs = (await session.execute(
                select(AlgorithmRun)
                .where(AlgorithmRun.instance_id == deployment_id)
                .order_by(AlgorithmRun.run_number.asc())
            )).scalars().all()
            run_meta = [
                {"run_id": r.id, "run_number": r.run_number,
                 "started_at": r.started_at, "stopped_at": r.stopped_at,
                 "status": r.status}
                for r in runs
            ]

        frames: list[pd.DataFrame] = []
        prev: Optional[dict] = None
        for curr in run_meta:
            p = self._base / deployment_id / curr["run_id"] / "equity.parquet"
            if not p.exists():
                continue
            df = pq.read_table(p).to_pandas()
            if prev is not None and prev.get("stopped_at") is not None:
                stopped = prev["stopped_at"]
                started = curr["started_at"]
                if stopped is not None and started is not None:
                    mid = stopped + (started - stopped) / 2
                    ts = pd.Timestamp(mid)
                    if ts.tz is not None:
                        ts = ts.tz_convert("UTC").tz_localize(None)
                    frames.append(pd.DataFrame([{
                        "timestamp": ts,
                        "portfolio_value": float("nan"),
                        "cash": float("nan"),
                    }]))
            frames.append(df)
            prev = curr

        if not frames:
            return
        full = pd.concat(frames, ignore_index=True)
        daily = (
            full.set_index("timestamp").resample("D").last().reset_index()
        )
        if daily["portfolio_value"].dropna().empty:
            return
        daily_for_metrics = daily.dropna(subset=["portfolio_value"]).reset_index(drop=True)
        if daily_for_metrics.empty:
            return

        initial_cash = float(daily_for_metrics.iloc[0]["portfolio_value"])
        key_metrics = bf.build_key_metrics(
            daily_for_metrics, benchmark_daily_df=None, initial_cash=initial_cash,
        )
        equity_curve = [
            {
                "timestamp": ts.isoformat() if pd.notna(ts) else None,
                "portfolio_value": (None if pd.isna(v) else float(v)),
            }
            for ts, v in zip(daily["timestamp"], daily["portfolio_value"])
        ]
        drawdown_curve = bf.build_drawdown_curve(daily_for_metrics)
        monthly_matrix = bf.build_monthly_matrix(daily_for_metrics)
        scalar = key_metrics.get("strategy", {})

        async with self._sf() as session:
            existing = (await session.execute(
                select(AlgorithmDeploymentReport).where(
                    AlgorithmDeploymentReport.deployment_id == deployment_id
                )
            )).scalar_one_or_none()
            if existing is None:
                existing = AlgorithmDeploymentReport(deployment_id=deployment_id)
                session.add(existing)
            existing.generated_at = datetime.now(timezone.utc)
            existing.total_return = scalar.get("total_return")
            existing.cagr = scalar.get("cagr")
            existing.volatility = scalar.get("volatility")
            existing.sharpe_ratio = scalar.get("sharpe_ratio")
            existing.sortino_ratio = scalar.get("sortino_ratio")
            existing.calmar_ratio = scalar.get("calmar_ratio")
            existing.max_drawdown = scalar.get("max_drawdown")
            existing.romad = scalar.get("romad")
            existing.trade_count = scalar.get("trade_count")
            existing.win_rate = scalar.get("win_rate")
            existing.profit_factor = scalar.get("profit_factor")
            existing.avg_win = scalar.get("avg_win")
            existing.avg_loss = scalar.get("avg_loss")
            existing.expectancy = scalar.get("expectancy")
            existing.longest_drawdown_days = scalar.get("longest_drawdown_days")
            existing.equity_curve = equity_curve
            existing.drawdown_curve = drawdown_curve
            existing.key_metrics = key_metrics
            existing.monthly_returns_matrix = monthly_matrix
            existing.runs_index = [
                {
                    "run_id": r["run_id"],
                    "run_number": r["run_number"],
                    "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                    "stopped_at": r["stopped_at"].isoformat() if r["stopped_at"] else None,
                    "status": r["status"],
                }
                for r in run_meta
            ]
            await session.commit()
