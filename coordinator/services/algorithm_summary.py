"""Aggregate per-algorithm rollup for list/hub pages."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.database.models import (
    Account,
    Algorithm,
    AlgorithmInstance,
    AlgorithmRun,
    BacktestRun,
    OptimizationSession,
)
from coordinator.api.serialization import to_iso_utc


def _downsample(curve: list[dict], target: int = 60) -> list[float]:
    if not curve:
        return []
    points = [float(p.get("equity", 0.0)) for p in curve]
    if len(points) <= target:
        return points
    step = len(points) / target
    return [points[int(i * step)] for i in range(target)]


def _pick_active_instance(
    pairs: list[tuple[AlgorithmInstance, str | None]],
) -> tuple[AlgorithmInstance | None, str | None]:
    """Pick a running instance, preferring a live broker account over paper.

    Returns (instance, environment) or (None, None) when no instance is
    currently running.
    """
    for env in ("live", "paper"):
        for inst, inst_env in pairs:
            if (inst.status or "").lower() == "running" and (inst_env or "").lower() == env:
                return inst, env
    return None, None


class AlgorithmSummaryService:
    """Produces a rollup dict for an algorithm.

    Pure aggregation — caller is responsible for caching.
    """

    async def build_for(self, algorithm_id: str, db: AsyncSession) -> dict[str, Any]:
        instance_rows = (
            await db.execute(
                select(AlgorithmInstance, Account.environment)
                .join(Account, AlgorithmInstance.account_id == Account.id, isouter=True)
                .where(AlgorithmInstance.algorithm_id == algorithm_id)
            )
        ).all()
        instance_pairs: list[tuple[AlgorithmInstance, str | None]] = [
            (row[0], row[1]) for row in instance_rows
        ]
        instances = [inst for inst, _ in instance_pairs]

        backtests_count = (
            await db.execute(
                select(func.count(BacktestRun.id)).where(
                    BacktestRun.algorithm_id == algorithm_id
                )
            )
        ).scalar_one()

        sessions_count = (
            await db.execute(
                select(func.count(OptimizationSession.id)).where(
                    OptimizationSession.algorithm_id == algorithm_id
                )
            )
        ).scalar_one()

        active, active_env = _pick_active_instance(instance_pairs)
        if active is None:
            status = "idle"
            status_source = None
        else:
            status = active_env or "idle"
            status_source = active.id

        latest_run = (
            await db.execute(
                select(AlgorithmRun)
                .join(AlgorithmInstance, AlgorithmRun.instance_id == AlgorithmInstance.id)
                .where(AlgorithmInstance.algorithm_id == algorithm_id)
                .order_by(desc(AlgorithmRun.run_number))
                .limit(1)
            )
        ).scalar_one_or_none()

        latest_bt = (
            await db.execute(
                select(BacktestRun)
                .where(BacktestRun.algorithm_id == algorithm_id)
                .where(BacktestRun.status == "completed")
                .order_by(desc(BacktestRun.created_at))
                .limit(1)
            )
        ).scalar_one_or_none()

        headline_sharpe: float | None = None
        headline_sharpe_source: str | None = None
        if active is not None and active.lifetime_metrics:
            live_sharpe = active.lifetime_metrics.get("sharpe_30d")
            if live_sharpe is not None:
                headline_sharpe = float(live_sharpe)
                headline_sharpe_source = "live_30d"
        if headline_sharpe is None and latest_bt is not None and latest_bt.sharpe_ratio is not None:
            headline_sharpe = float(latest_bt.sharpe_ratio)
            headline_sharpe_source = "last_backtest"

        sparkline: list[float] = []
        sparkline_source: str | None = None
        if latest_run is not None and latest_run.equity_curve:
            sparkline = _downsample(latest_run.equity_curve, target=60)
            sparkline_source = "live"
        elif latest_bt is not None and latest_bt.equity_curve:
            sparkline = _downsample(latest_bt.equity_curve, target=60)
            sparkline_source = "backtest"

        run_ts = None
        if latest_run is not None:
            run_ts = latest_run.stopped_at or latest_run.started_at or latest_run.created_at

        last_activity_dt: datetime | None = None
        for candidate in (
            run_ts,
            latest_bt.created_at if latest_bt else None,
        ):
            if candidate and (last_activity_dt is None or candidate > last_activity_dt):
                last_activity_dt = candidate
        latest_session = (
            await db.execute(
                select(OptimizationSession)
                .where(OptimizationSession.algorithm_id == algorithm_id)
                .order_by(desc(OptimizationSession.created_at))
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest_session and (
            last_activity_dt is None or latest_session.created_at > last_activity_dt
        ):
            last_activity_dt = latest_session.created_at

        return {
            "status": status,
            "status_source": status_source,
            "headline_sharpe": headline_sharpe,
            "headline_sharpe_source": headline_sharpe_source,
            "equity_sparkline": sparkline,
            "equity_sparkline_source": sparkline_source,
            "counts": {
                "deployments": len(instances),
                "backtests": int(backtests_count),
                "research_sessions": int(sessions_count),
            },
            "last_activity_at": to_iso_utc(last_activity_dt) if last_activity_dt else None,
        }
