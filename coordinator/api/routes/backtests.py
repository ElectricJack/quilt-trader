from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from coordinator.api.dependencies import get_db
from coordinator.database.models import BacktestComparison

router = APIRouter(prefix="/api/backtests", tags=["backtests"])

class ComparisonCreate(BaseModel):
    instance_id: str
    algorithm_id: str
    time_range_start: str
    time_range_end: str
    total_ticks: int
    matching_ticks: int
    match_percentage: float
    divergences: Optional[list[dict]] = None
    summary: Optional[str] = None

def _to_response(c: BacktestComparison) -> dict:
    return {
        "id": c.id, "instance_id": c.instance_id, "algorithm_id": c.algorithm_id,
        "time_range_start": c.time_range_start.isoformat() if c.time_range_start else None,
        "time_range_end": c.time_range_end.isoformat() if c.time_range_end else None,
        "total_ticks": c.total_ticks, "matching_ticks": c.matching_ticks,
        "match_percentage": c.match_percentage, "divergences": c.divergences,
        "summary": c.summary,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }

@router.get("")
async def list_comparisons(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BacktestComparison).order_by(desc(BacktestComparison.created_at)))
    return [_to_response(c) for c in result.scalars().all()]

@router.post("", status_code=201)
async def create_comparison(body: ComparisonCreate, db: AsyncSession = Depends(get_db)):
    from datetime import datetime
    comp = BacktestComparison(
        instance_id=body.instance_id, algorithm_id=body.algorithm_id,
        time_range_start=datetime.fromisoformat(body.time_range_start),
        time_range_end=datetime.fromisoformat(body.time_range_end),
        total_ticks=body.total_ticks, matching_ticks=body.matching_ticks,
        match_percentage=body.match_percentage, divergences=body.divergences,
        summary=body.summary,
    )
    db.add(comp)
    await db.flush()
    return _to_response(comp)

@router.get("/{comparison_id}")
async def get_comparison(comparison_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BacktestComparison).where(BacktestComparison.id == comparison_id))
    comp = result.scalar_one_or_none()
    if comp is None:
        raise HTTPException(status_code=404, detail="Comparison not found")
    return _to_response(comp)
