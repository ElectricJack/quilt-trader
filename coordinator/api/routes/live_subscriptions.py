"""REST API for live market-data subscriptions.

Subscriptions are tracked in two tables:
- LiveSubscription: one row per (broker, symbol) pair.
- SubscriptionConsumer: one row per consumer (manual user OR algorithm deployment).

A subscription is alive as long as at least one consumer row exists; when the
last consumer is released, the LiveSubscription row is deleted.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_container, get_db
from coordinator.api.serialization import to_iso_utc
from coordinator.database.models import LiveSubscription, SubscriptionConsumer

router = APIRouter(prefix="/api/live-subscriptions", tags=["live-subscriptions"])

# Coarse tick-rate estimates per symbol (trades/min) — sharpens once running.
_TICK_RATE_DEFAULTS: dict[str, float] = {
    "SPY": 200.0, "QQQ": 180.0, "IWM": 80.0, "DIA": 30.0,
}
_BYTES_PER_TRADE = 80
_BYTES_PER_QUOTE = 90


class SubscriptionCreate(BaseModel):
    broker: str
    symbol: str
    asset_class: str = "equities"
    tick_retention_hours: int = 168

    @field_validator("tick_retention_hours")
    @classmethod
    def _validate_retention(cls, v: int) -> int:
        if v < 24 or v > 8760 or v % 24 != 0:
            raise ValueError(
                "tick_retention_hours must be a multiple of 24 between 24 and 8760"
            )
        return v

    @field_validator("asset_class")
    @classmethod
    def _validate_asset_class(cls, v: str) -> str:
        if v not in ("equities", "crypto", "options"):
            raise ValueError(f"asset_class must be one of equities, crypto, options; got {v!r}")
        return v


class SubscriptionUpdate(BaseModel):
    tick_retention_hours: Optional[int] = None

    @field_validator("tick_retention_hours")
    @classmethod
    def _validate_retention(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if v < 24 or v > 8760 or v % 24 != 0:
            raise ValueError(
                "tick_retention_hours must be a multiple of 24 between 24 and 8760"
            )
        return v


def _consumer_dict(c: SubscriptionConsumer) -> dict:
    return {
        "id": c.id,
        "consumer_type": c.consumer_type,
        "consumer_id": c.consumer_id,
        "created_at": to_iso_utc(c.created_at),
    }


def _to_response(s: LiveSubscription) -> dict:
    return {
        "id": s.id,
        "broker": s.broker,
        "symbol": s.symbol,
        "asset_class": s.asset_class,
        "status": s.status,
        "last_error": s.last_error,
        "last_tick_at": to_iso_utc(s.last_tick_at),
        "tick_rate_per_min": s.tick_rate_per_min,
        "tick_retention_hours": s.tick_retention_hours,
        "consumers": [_consumer_dict(c) for c in (s.consumers or [])],
    }


def _humanize(b: int) -> str:
    for unit, div in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024), ("B", 1)):
        if b >= div:
            return f"{b / div:.1f} {unit}"
    return "0 B"


@router.get("")
async def list_subs(db: AsyncSession = Depends(get_db)):
    from sqlalchemy.orm import selectinload
    rows = (await db.execute(
        select(LiveSubscription).options(selectinload(LiveSubscription.consumers))
    )).scalars().all()
    return [_to_response(r) for r in rows]


@router.post("", status_code=201)
async def create_sub(body: SubscriptionCreate, db: AsyncSession = Depends(get_db)):
    symbol_upper = body.symbol.upper()
    sub = LiveSubscription(
        broker=body.broker,
        symbol=symbol_upper,
        asset_class=body.asset_class,
        tick_retention_hours=body.tick_retention_hours,
        status="running",
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

    # Manual subscribe = one 'manual' consumer row.
    db.add(SubscriptionConsumer(
        subscription_id=sub.id, consumer_type="manual", consumer_id=None,
    ))
    await db.flush()

    # Register with the in-memory LiveFeedManager and kick off the aggregator task.
    try:
        container = get_container()
    except AssertionError:
        container = None
    if container is not None:
        if container.live_feed_manager is not None:
            container.live_feed_manager.ensure_running(
                body.broker, symbol_upper, "manual"
            )
        if container.live_feed_aggregator is not None:
            await container.live_feed_aggregator.start_subscription(
                body.broker, symbol_upper, body.asset_class,
            )

    await db.refresh(sub, ["consumers"])
    return _to_response(sub)


@router.get("/estimate")
async def estimate(
    broker: str = Query(...),
    symbol: str = Query(...),
    retention_hours: int = Query(168),
    db: AsyncSession = Depends(get_db),
):
    sub = (await db.execute(
        select(LiveSubscription).where(
            LiveSubscription.broker == broker,
            LiveSubscription.symbol == symbol.upper(),
        )
    )).scalar_one_or_none()
    source = "estimated"
    rate = _TICK_RATE_DEFAULTS.get(symbol.upper(), 20.0)
    if sub and sub.tick_rate_per_min:
        rate = sub.tick_rate_per_min
        source = "observed"
    minutes = retention_hours * 60
    projected = int(rate * minutes * (_BYTES_PER_TRADE + _BYTES_PER_QUOTE))
    return {
        "tick_rate_per_min": rate,
        "projected_bytes": projected,
        "projected_human": _humanize(projected),
        "source": source,
    }


@router.get("/{sub_id}")
async def get_sub(sub_id: str, db: AsyncSession = Depends(get_db)):
    from sqlalchemy.orm import selectinload
    sub = (await db.execute(
        select(LiveSubscription)
        .where(LiveSubscription.id == sub_id)
        .options(selectinload(LiveSubscription.consumers))
    )).scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return _to_response(sub)


@router.patch("/{sub_id}")
async def patch_sub(
    sub_id: str, body: SubscriptionUpdate,
    db: AsyncSession = Depends(get_db),
):
    sub = (await db.execute(
        select(LiveSubscription).where(LiveSubscription.id == sub_id)
    )).scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if body.tick_retention_hours is not None:
        sub.tick_retention_hours = body.tick_retention_hours
    await db.flush()
    return _to_response(sub)


@router.post("/{sub_id}/unsubscribe")
async def unsubscribe(sub_id: str, db: AsyncSession = Depends(get_db)):
    """Release the manual consumer for this subscription.

    If no consumers remain, the LiveSubscription row is deleted and the
    broker stream subscribe-set drops the symbol.
    """
    from sqlalchemy.orm import selectinload
    sub = (await db.execute(
        select(LiveSubscription)
        .where(LiveSubscription.id == sub_id)
        .options(selectinload(LiveSubscription.consumers))
    )).scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")

    # Delete the manual consumer row, if any.
    manual = [c for c in (sub.consumers or []) if c.consumer_type == "manual"]
    for c in manual:
        await db.delete(c)
    await db.flush()
    await db.refresh(sub, ["consumers"])

    # Symmetric auto-delete: if no consumers remain, drop the row.
    if not sub.consumers:
        try:
            container = get_container()
        except AssertionError:
            container = None
        if container is not None and container.live_feed_aggregator is not None:
            await container.live_feed_aggregator.stop_subscription(
                sub.broker, sub.symbol,
            )
        if container is not None and container.live_feed_manager is not None:
            container.live_feed_manager.release(sub.broker, sub.symbol, "manual")
        await db.delete(sub)
        await db.flush()
        return {"deleted": True, "id": sub_id}

    return _to_response(sub)


@router.delete("/{sub_id}", status_code=204)
async def delete_sub(sub_id: str, db: AsyncSession = Depends(get_db)):
    from sqlalchemy.orm import selectinload
    sub = (await db.execute(
        select(LiveSubscription)
        .where(LiveSubscription.id == sub_id)
        .options(selectinload(LiveSubscription.consumers))
    )).scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if sub.consumers:
        consumer_summary = ", ".join(
            (c.consumer_type if c.consumer_type == "manual"
             else f"algo:{c.consumer_id}")
            for c in sub.consumers
        )
        raise HTTPException(
            status_code=409,
            detail=f"Subscription still held by {len(sub.consumers)} consumer(s): {consumer_summary}",
        )
    # Stop the aggregator task if still running.
    try:
        container = get_container()
    except AssertionError:
        container = None
    if container is not None and container.live_feed_aggregator is not None:
        await container.live_feed_aggregator.stop_subscription(sub.broker, sub.symbol)

    await db.delete(sub)
