from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.api.serialization import to_iso_utc
from coordinator.database.models import AlgorithmRun

router = APIRouter(tags=["runs"])


class RunCreate(BaseModel):
    starting_equity: Optional[float] = None


def _to_response(run: AlgorithmRun) -> dict:
    return {
        "id": run.id,
        "instance_id": run.instance_id,
        "run_number": run.run_number,
        "status": run.status,
        "started_at": to_iso_utc(run.started_at),
        "stopped_at": to_iso_utc(run.stopped_at),
        "starting_equity": run.starting_equity,
        "ending_equity": run.ending_equity,
        "net_pnl": run.net_pnl,
        "unrealized_pnl": run.unrealized_pnl,
        "total_fees": run.total_fees,
        "total_slippage": run.total_slippage,
        "trade_count": run.trade_count,
        "metrics": run.metrics,
        "equity_curve": run.equity_curve,
    }


@router.get("/api/instances/{instance_id}/runs")
async def list_runs(instance_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlgorithmRun)
        .where(AlgorithmRun.instance_id == instance_id)
        .order_by(AlgorithmRun.run_number.desc())
    )
    return [_to_response(r) for r in result.scalars().all()]


@router.post("/api/instances/{instance_id}/runs", status_code=201)
async def create_run(instance_id: str, body: RunCreate, db: AsyncSession = Depends(get_db)):
    count_result = await db.execute(
        select(func.count(AlgorithmRun.id)).where(AlgorithmRun.instance_id == instance_id)
    )
    next_num = (count_result.scalar() or 0) + 1
    run = AlgorithmRun(
        instance_id=instance_id,
        run_number=next_num,
        status="running",
        starting_equity=body.starting_equity,
    )
    db.add(run)
    await db.flush()
    return _to_response(run)


@router.get("/api/runs/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AlgorithmRun).where(AlgorithmRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return _to_response(run)
