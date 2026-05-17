"""GET /api/diagnostics — runtime status surface for `quilt doctor`."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_container, get_db
from coordinator.database.models import (
    AlgorithmDeploymentReport, AlgorithmInstance, LiveSubscription, Worker,
)

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.get("")
async def diagnostics(db: AsyncSession = Depends(get_db)) -> dict:
    container = get_container()
    checks: list[dict] = []

    # 1. live_feed_aggregator
    aggregator = getattr(container, "live_feed_aggregator", None)
    if aggregator is None:
        checks.append({"name": "live_feed_aggregator", "status": "WARN",
                       "message": "not constructed"})
    else:
        bar_subs = getattr(aggregator, "_bar_subscribers", {})
        event_subs = getattr(aggregator, "_event_subscribers", {})
        active_subs = len(bar_subs) + len(event_subs)
        checks.append({"name": "live_feed_aggregator", "status": "PASS",
                       "message": f"{active_subs} active subscriber targets"})

    # 2. tick_scheduler
    scheduler = getattr(container, "tick_scheduler", None)
    if scheduler is None:
        checks.append({"name": "tick_scheduler", "status": "WARN",
                       "message": "not constructed"})
    else:
        inst_count = len(getattr(scheduler, "_instances", {}))
        checks.append({"name": "tick_scheduler", "status": "PASS",
                       "message": f"{inst_count} instances scheduled"})

    # 3. live_finalizer: are running deployments getting fresh reports?
    running_ids = (await db.execute(
        select(AlgorithmInstance.id).where(AlgorithmInstance.status == "running")
    )).scalars().all()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    stale: list[str] = []
    for did in running_ids:
        rep = (await db.execute(
            select(AlgorithmDeploymentReport).where(
                AlgorithmDeploymentReport.deployment_id == did
            )
        )).scalar_one_or_none()
        rep_ts = getattr(rep, "generated_at", None) if rep is not None else None
        if rep_ts is None:
            stale.append(did)
            continue
        if rep_ts.tzinfo is None:
            rep_ts = rep_ts.replace(tzinfo=timezone.utc)
        if rep_ts < cutoff:
            stale.append(did)
    if not running_ids:
        checks.append({"name": "live_finalizer", "status": "PASS",
                       "message": "no running deployments"})
    elif stale:
        checks.append({"name": "live_finalizer", "status": "WARN",
                       "message": f"{len(stale)} deployment(s) with stale or missing reports"})
    else:
        checks.append({"name": "live_finalizer", "status": "PASS",
                       "message": f"all {len(running_ids)} running deployments fresh"})

    # 4. workers
    online_count = (await db.execute(
        select(func.count(Worker.id)).where(Worker.status == "online")
    )).scalar_one()
    total_count = (await db.execute(
        select(func.count(Worker.id))
    )).scalar_one()
    if total_count == 0:
        checks.append({"name": "workers", "status": "WARN",
                       "message": "no workers registered"})
    else:
        checks.append({"name": "workers", "status": "PASS",
                       "message": f"{online_count}/{total_count} online"})

    # 5. live_subscriptions
    running_subs = (await db.execute(
        select(func.count(LiveSubscription.id)).where(LiveSubscription.status == "running")
    )).scalar_one()
    checks.append({"name": "live_subscriptions", "status": "PASS",
                   "message": f"{running_subs} subscriptions running"})

    ok = all(c["status"] == "PASS" for c in checks)
    return {"ok": ok, "checks": checks}
