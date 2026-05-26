# coordinator/api/routes/data_goals.py
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.api.serialization import to_iso_utc
from coordinator.database.models import DataGoal

router = APIRouter(prefix="/api/data/goals", tags=["data-goals"])


class GoalCreate(BaseModel):
    name: str
    goal_type: str
    config: dict


def _to_response(g: DataGoal) -> dict:
    pct = (g.completed_items / g.total_items * 100) if g.total_items > 0 else 0
    phase = getattr(g, "phase", None) or "discovering"
    discovery_progress = getattr(g, "discovery_progress", None)
    return {
        "id": g.id,
        "name": g.name,
        "goal_type": g.goal_type,
        "config": g.config,
        "status": g.status,
        "phase": phase,
        "discovery_progress": discovery_progress,
        "total_items": g.total_items,
        "completed_items": g.completed_items,
        "failed_items": g.failed_items,
        "progress_pct": round(pct, 1),
        "last_processed_at": to_iso_utc(g.last_processed_at),
        "error_message": g.error_message,
        "created_at": to_iso_utc(g.created_at),
    }


@router.post("", status_code=201)
async def create_goal(body: GoalCreate, db: AsyncSession = Depends(get_db)):
    if body.goal_type not in ("options", "bars"):
        raise HTTPException(400, detail=f"Unknown goal_type: {body.goal_type}")
    goal = DataGoal(
        name=body.name,
        goal_type=body.goal_type,
        config=body.config,
        status="active",
    )
    db.add(goal)
    await db.flush()
    response = _to_response(goal)
    await db.commit()
    return response


@router.get("")
async def list_goals(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DataGoal).order_by(DataGoal.created_at.desc()))
    return [_to_response(g) for g in result.scalars().all()]


@router.get("/{goal_id}")
async def get_goal(goal_id: str, db: AsyncSession = Depends(get_db)):
    g = (await db.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404, detail="Goal not found")
    return _to_response(g)


@router.post("/{goal_id}/pause")
async def pause_goal(goal_id: str, db: AsyncSession = Depends(get_db)):
    g = (await db.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404, detail="Goal not found")
    g.status = "paused"
    await db.commit()
    return _to_response(g)


@router.post("/{goal_id}/resume")
async def resume_goal(goal_id: str, db: AsyncSession = Depends(get_db)):
    g = (await db.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404, detail="Goal not found")
    g.status = "active"
    await db.commit()
    return _to_response(g)


@router.put("/{goal_id}")
async def update_goal(goal_id: str, body: GoalCreate, db: AsyncSession = Depends(get_db)):
    g = (await db.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404, detail="Goal not found")
    g.name = body.name
    g.goal_type = body.goal_type
    g.config = body.config
    g.total_items = 0
    g.completed_items = 0
    g.status = "active"
    await db.commit()
    return _to_response(g)


@router.delete("/{goal_id}", status_code=204)
async def delete_goal(goal_id: str, db: AsyncSession = Depends(get_db)):
    g = (await db.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404, detail="Goal not found")
    await db.delete(g)
    await db.commit()
