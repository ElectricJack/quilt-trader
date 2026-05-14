"""REST API for live market-data subscriptions.

Implements Spec B §5: list/create/get/patch/unsubscribe/delete +
storage estimator. The router itself is not yet mounted in
``coordinator/main.py`` — that wiring is owned by work unit S6.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.database.models import LiveSubscription

router = APIRouter(prefix="/api/live-subscriptions", tags=["live-subscriptions"])

# Coarse tick-rate estimates per symbol (trades/min) — sharpens once running.
_TICK_RATE_DEFAULTS: dict[str, float] = {
    "SPY": 200.0,
    "QQQ": 180.0,
    "IWM": 80.0,
    "DIA": 30.0,
}
_BYTES_PER_TRADE = 80
_BYTES_PER_QUOTE = 90


class SubscriptionCreate(BaseModel):
    broker: str
    symbol: str
    tick_retention_hours: int = 24

    @field_validator("tick_retention_hours")
    @classmethod
    def _validate_retention(cls, v: int) -> int:
        if v < 24 or v > 720 or v % 24 != 0:
            raise ValueError(
                "tick_retention_hours must be a multiple of 24 between 24 and 720"
            )
        return v


class SubscriptionUpdate(BaseModel):
    tick_retention_hours: Optional[int] = None

    @field_validator("tick_retention_hours")
    @classmethod
    def _validate_retention(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if v < 24 or v > 720 or v % 24 != 0:
            raise ValueError(
                "tick_retention_hours must be a multiple of 24 between 24 and 720"
            )
        return v


def _to_response(s: LiveSubscription) -> dict:
    return {
        "id": s.id,
        "broker": s.broker,
        "symbol": s.symbol,
        "status": s.status,
        "last_error": s.last_error,
        "last_tick_at": s.last_tick_at.isoformat() if s.last_tick_at else None,
        "tick_rate_per_min": s.tick_rate_per_min,
        "tick_retention_hours": s.tick_retention_hours,
        "dependent_count": s.dependent_count,
    }


def _humanize(b: int) -> str:
    for unit, div in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024), ("B", 1)):
        if b >= div:
            return f"{b / div:.1f} {unit}"
    return "0 B"


@router.get("")
async def list_subs(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(LiveSubscription))).scalars().all()
    return [_to_response(r) for r in rows]


@router.post("", status_code=201)
async def create_sub(body: SubscriptionCreate, db: AsyncSession = Depends(get_db)):
    sub = LiveSubscription(
        broker=body.broker,
        symbol=body.symbol.upper(),
        tick_retention_hours=body.tick_retention_hours,
        status="stopped",
        dependent_count=0,
    )
    db.add(sub)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Subscription already exists for {body.broker}/{body.symbol}",
        )
    return _to_response(sub)


@router.get("/estimate")
async def estimate(
    broker: str = Query(...),
    symbol: str = Query(...),
    retention_hours: int = Query(24),
    db: AsyncSession = Depends(get_db),
):
    sub = (
        await db.execute(
            select(LiveSubscription).where(
                LiveSubscription.broker == broker,
                LiveSubscription.symbol == symbol.upper(),
            )
        )
    ).scalar_one_or_none()
    source = "estimated"
    rate = _TICK_RATE_DEFAULTS.get(symbol.upper(), 20.0)
    if sub and sub.tick_rate_per_min:
        rate = sub.tick_rate_per_min
        source = "observed"
    minutes = retention_hours * 60
    # ~1 quote per trade as a coarse 1:1 estimate.
    projected = int(rate * minutes * (_BYTES_PER_TRADE + _BYTES_PER_QUOTE))
    return {
        "tick_rate_per_min": rate,
        "projected_bytes": projected,
        "projected_human": _humanize(projected),
        "source": source,
    }


@router.get("/{sub_id}")
async def get_sub(sub_id: str, db: AsyncSession = Depends(get_db)):
    sub = (
        await db.execute(
            select(LiveSubscription).where(LiveSubscription.id == sub_id)
        )
    ).scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return _to_response(sub)


@router.patch("/{sub_id}")
async def patch_sub(
    sub_id: str,
    body: SubscriptionUpdate,
    db: AsyncSession = Depends(get_db),
):
    sub = (
        await db.execute(
            select(LiveSubscription).where(LiveSubscription.id == sub_id)
        )
    ).scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if body.tick_retention_hours is not None:
        sub.tick_retention_hours = body.tick_retention_hours
    await db.flush()
    return _to_response(sub)


@router.post("/{sub_id}/unsubscribe")
async def unsubscribe(sub_id: str, db: AsyncSession = Depends(get_db)):
    sub = (
        await db.execute(
            select(LiveSubscription).where(LiveSubscription.id == sub_id)
        )
    ).scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    # Decrement only the manual dependent — implementation in I1 wires the manager;
    # for now we just decrement the counter to signal the manual release.
    sub.dependent_count = max(0, sub.dependent_count - 1)
    await db.flush()
    return _to_response(sub)


@router.delete("/{sub_id}", status_code=204)
async def delete_sub(sub_id: str, db: AsyncSession = Depends(get_db)):
    sub = (
        await db.execute(
            select(LiveSubscription).where(LiveSubscription.id == sub_id)
        )
    ).scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if sub.dependent_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Subscription has {sub.dependent_count} active dependents",
        )
    await db.delete(sub)
    # ticks directory cleanup is done by the aggregator's retention sweeper
    # to avoid filesystem ownership in this route handler.
