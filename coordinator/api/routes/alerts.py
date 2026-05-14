from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.database.models import (
    Event, BacktestComparison, AlgorithmInstance, Algorithm, Worker,
)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


async def _resolve_event(event: Event, db: AsyncSession) -> tuple[str, str | None]:
    """Return (source_name, link_path)."""
    if event.source_type == "instance" and event.source_id:
        result = await db.execute(
            select(Algorithm.name)
            .join(AlgorithmInstance, AlgorithmInstance.algorithm_id == Algorithm.id)
            .where(AlgorithmInstance.id == event.source_id)
        )
        name = result.scalar_one_or_none() or event.source_id
        return name, f"/instances/{event.source_id}"
    if event.source_type == "worker" and event.source_id:
        worker = (await db.execute(
            select(Worker).where(Worker.id == event.source_id)
        )).scalar_one_or_none()
        name = worker.name if worker else event.source_id
        return name, f"/workers/{event.source_id}"
    return event.source_id or "system", None


@router.get("")
async def list_alerts(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=2)

    # Warning/error events from the last 2 days
    events_result = await db.execute(
        select(Event)
        .where(or_(Event.severity == "warning", Event.severity == "error"))
        .where(Event.timestamp >= cutoff)
        .order_by(Event.timestamp.desc())
        .limit(limit)
    )
    events = events_result.scalars().all()

    items = []
    for ev in events:
        source_name, link = await _resolve_event(ev, db)
        items.append({
            "kind": "event",
            "id": ev.id,
            "severity": ev.severity,
            "label": ev.event_type.replace("_", " ").title(),
            "source_name": source_name,
            "timestamp": ev.timestamp.isoformat() if ev.timestamp else None,
            "link_path": link,
            "pill": ev.severity.upper()[:4],
            "pill_color": "err" if ev.severity == "error" else "warn",
        })

    # Backtest divergences with <90% match in the last 2 days
    bt_result = await db.execute(
        select(BacktestComparison, Algorithm.name)
        .join(Algorithm, BacktestComparison.algorithm_id == Algorithm.id)
        .where(BacktestComparison.match_percentage < 90.0)
        .where(BacktestComparison.created_at >= cutoff)
        .order_by(BacktestComparison.created_at.desc())
        .limit(limit)
    )
    for bt, algo_name in bt_result.all():
        items.append({
            "kind": "backtest",
            "id": bt.id,
            "severity": "warning",
            "label": "Backtest divergence",
            "source_name": algo_name,
            "timestamp": bt.created_at.isoformat() if bt.created_at else None,
            "link_path": f"/backtests/{bt.id}",
            "pill": f"{bt.match_percentage:.1f}%",
            "pill_color": "backtest",
        })

    items.sort(key=lambda x: x["timestamp"] or "", reverse=True)
    return {"items": items[:limit]}
