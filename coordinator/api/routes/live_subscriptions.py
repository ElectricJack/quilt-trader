"""REST API for live market-data subscriptions.

Subscriptions are tracked in two tables:
- LiveSubscription: one row per (account_id|provider_type, symbol) pair.
- SubscriptionConsumer: one row per consumer (manual user OR algorithm deployment).

A subscription is alive as long as at least one consumer row exists; when the
last consumer is released, the LiveSubscription row is deleted.

Sources: either an account (account_id set, provider_type null) or a data
provider (provider_type set, account_id null). Exactly one must be set.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_container, get_db
from coordinator.api.serialization import to_iso_utc
from coordinator.database.models import LiveSubscription, SubscriptionConsumer, Account, Setting

router = APIRouter(prefix="/api/live-subscriptions", tags=["live-subscriptions"])

# Coarse tick-rate estimates per symbol (trades/min) — sharpens once running.
_TICK_RATE_DEFAULTS: dict[str, float] = {
    "SPY": 200.0, "QQQ": 180.0, "IWM": 80.0, "DIA": 30.0,
}
_BYTES_PER_TRADE = 80
_BYTES_PER_QUOTE = 90

_VALID_PROVIDERS = ("polygon", "thetadata", "coinbase")


class SubscriptionCreate(BaseModel):
    # Exactly one of account_id or provider_type must be set.
    account_id: Optional[str] = None
    provider_type: Optional[str] = None  # "polygon" | "thetadata"
    symbol: str
    asset_class: str = "equities"
    tick_retention_hours: int = 168

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "SubscriptionCreate":
        has_account = bool(self.account_id)
        has_provider = bool(self.provider_type)
        if has_account and has_provider:
            raise ValueError("Provide either account_id or provider_type, not both")
        if not has_account and not has_provider:
            raise ValueError("One of account_id or provider_type is required")
        return self

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

    @field_validator("provider_type")
    @classmethod
    def _validate_provider_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _VALID_PROVIDERS:
            raise ValueError(f"provider_type must be one of {_VALID_PROVIDERS}; got {v!r}")
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


def _consumer_dict(c: SubscriptionConsumer, algo_index: dict[str, dict]) -> dict:
    """Serialize a consumer; if it's an algo consumer, augment with the
    algorithm's id + name (via the algo_index map keyed on deployment_id)."""
    out = {
        "id": c.id,
        "consumer_type": c.consumer_type,
        "consumer_id": c.consumer_id,
        "created_at": to_iso_utc(c.created_at),
        "algorithm_id": None,
        "algorithm_name": None,
    }
    if c.consumer_type == "algo" and c.consumer_id in algo_index:
        out["algorithm_id"] = algo_index[c.consumer_id]["algorithm_id"]
        out["algorithm_name"] = algo_index[c.consumer_id]["algorithm_name"]
    return out


def _to_response(s: LiveSubscription, algo_index: dict[str, dict]) -> dict:
    return {
        "id": s.id,
        "account_id": s.account_id,
        "account_name": s.account.name if s.account else None,
        "provider_type": s.provider_type,
        "broker": s.broker,
        "symbol": s.symbol,
        "asset_class": s.asset_class,
        "status": s.status,
        "last_error": s.last_error,
        "last_tick_at": to_iso_utc(s.last_tick_at),
        "tick_rate_per_min": s.tick_rate_per_min,
        "tick_retention_hours": s.tick_retention_hours,
        "consumers": [_consumer_dict(c, algo_index) for c in (s.consumers or [])],
    }


def _humanize(b: int) -> str:
    for unit, div in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024), ("B", 1)):
        if b >= div:
            return f"{b / div:.1f} {unit}"
    return "0 B"


async def _build_algo_index(
    db: AsyncSession, deployment_ids: list[str],
) -> dict[str, dict]:
    if not deployment_ids:
        return {}
    from coordinator.database.models import AlgorithmInstance, Algorithm
    rows = (await db.execute(
        select(AlgorithmInstance.id, Algorithm.id, Algorithm.name)
        .join(Algorithm, AlgorithmInstance.algorithm_id == Algorithm.id)
        .where(AlgorithmInstance.id.in_(deployment_ids))
    )).all()
    return {
        inst_id: {"algorithm_id": algo_id, "algorithm_name": algo_name}
        for inst_id, algo_id, algo_name in rows
    }


async def _check_provider_credentials(db: AsyncSession, provider_type: str) -> None:
    """Raise 422 if the required Settings keys are not configured."""
    if provider_type == "coinbase":
        # No credentials needed — public market data is free and keyless
        return

    async def _get(key: str) -> Optional[str]:
        row = (await db.execute(
            select(Setting).where(Setting.key == key)
        )).scalar_one_or_none()
        return row.value if row and row.value else None

    if provider_type == "polygon":
        if not await _get("polygon_api_key"):
            raise HTTPException(
                status_code=422,
                detail="Polygon API key not configured in Settings",
            )
    elif provider_type == "thetadata":
        missing = []
        if not await _get("theta_data_username"):
            missing.append("theta_data_username")
        if not await _get("theta_data_password"):
            missing.append("theta_data_password")
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"ThetaData credentials not configured in Settings: {', '.join(missing)}",
            )


@router.get("")
async def list_subs(db: AsyncSession = Depends(get_db)):
    from sqlalchemy.orm import selectinload
    rows = (await db.execute(
        select(LiveSubscription)
        .options(
            selectinload(LiveSubscription.consumers),
            selectinload(LiveSubscription.account),
        )
    )).scalars().all()
    deployment_ids = [
        c.consumer_id for r in rows for c in (r.consumers or [])
        if c.consumer_type == "algo" and c.consumer_id
    ]
    algo_index = await _build_algo_index(db, deployment_ids)
    return [_to_response(r, algo_index) for r in rows]


@router.post("", status_code=201)
async def create_sub(body: SubscriptionCreate, db: AsyncSession = Depends(get_db)):
    symbol_upper = body.symbol.upper()

    if body.account_id:
        # Account-based subscription (existing behaviour).
        account = (await db.execute(
            select(Account).where(Account.id == body.account_id)
        )).scalar_one_or_none()
        if account is None:
            raise HTTPException(status_code=404, detail=f"Account {body.account_id} not found")
        if body.asset_class not in (account.supported_asset_types or []):
            raise HTTPException(
                status_code=422,
                detail=f"Account does not support asset_class {body.asset_class!r}",
            )
        broker_type = account.broker_type
        account_id = account.id
        provider_type = None
    else:
        # Provider-based subscription.
        assert body.provider_type is not None  # guaranteed by model validator
        await _check_provider_credentials(db, body.provider_type)
        broker_type = body.provider_type
        account_id = None
        provider_type = body.provider_type

    # Route-level duplicate check (DB constraint was dropped to support nullable account_id).
    existing_stmt = select(LiveSubscription).where(
        LiveSubscription.symbol == symbol_upper,
    )
    if account_id is not None:
        existing_stmt = existing_stmt.where(
            LiveSubscription.account_id == account_id,
        )
    else:
        existing_stmt = existing_stmt.where(
            LiveSubscription.provider_type == provider_type,
            LiveSubscription.account_id.is_(None),
        )
    existing = (await db.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None:
        source_label = account.name if account_id else provider_type  # type: ignore[possibly-undefined]
        raise HTTPException(
            status_code=409,
            detail=f"Subscription already exists for {source_label}/{symbol_upper}",
        )

    sub = LiveSubscription(
        account_id=account_id,
        provider_type=provider_type,
        broker=broker_type,
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
        source_label = account.name if account_id else provider_type  # type: ignore[possibly-undefined]
        raise HTTPException(
            status_code=409,
            detail=f"Subscription already exists for {source_label}/{symbol_upper}",
        )
    db.add(SubscriptionConsumer(
        subscription_id=sub.id, consumer_type="manual", consumer_id=None,
    ))
    sub_id = sub.id
    # Commit before calling the aggregator so its own sessions (which share
    # the same StaticPool connection in tests) can read the committed rows.
    await db.commit()

    try:
        container = get_container()
    except AssertionError:
        container = None
    if container is not None:
        if container.live_feed_manager is not None:
            container.live_feed_manager.ensure_running(
                broker_type, symbol_upper, "manual"
            )
        if container.live_feed_aggregator is not None:
            try:
                await container.live_feed_aggregator.start_subscription(
                    account_id, broker_type, symbol_upper, body.asset_class,
                )
            except Exception:  # noqa: BLE001
                import logging as _logging
                _logging.getLogger(__name__).exception(
                    "aggregator.start_subscription failed for %s/%s",
                    broker_type, symbol_upper,
                )

    from sqlalchemy.orm import selectinload
    sub = (await db.execute(
        select(LiveSubscription)
        .where(LiveSubscription.id == sub_id)
        .options(
            selectinload(LiveSubscription.consumers),
            selectinload(LiveSubscription.account),
        )
    )).scalar_one()
    return _to_response(sub, await _build_algo_index(db, []))


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
        .options(
            selectinload(LiveSubscription.consumers),
            selectinload(LiveSubscription.account),
        )
    )).scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    deployment_ids = [c.consumer_id for c in sub.consumers
                      if c.consumer_type == "algo" and c.consumer_id]
    algo_index = await _build_algo_index(db, deployment_ids)
    return _to_response(sub, algo_index)


@router.patch("/{sub_id}")
async def patch_sub(
    sub_id: str, body: SubscriptionUpdate,
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy.orm import selectinload
    sub = (await db.execute(
        select(LiveSubscription)
        .where(LiveSubscription.id == sub_id)
        .options(
            selectinload(LiveSubscription.consumers),
            selectinload(LiveSubscription.account),
        )
    )).scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if body.tick_retention_hours is not None:
        sub.tick_retention_hours = body.tick_retention_hours
    await db.flush()
    deployment_ids = [c.consumer_id for c in sub.consumers
                      if c.consumer_type == "algo" and c.consumer_id]
    algo_index = await _build_algo_index(db, deployment_ids)
    return _to_response(sub, algo_index)


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
        .options(
            selectinload(LiveSubscription.consumers),
            selectinload(LiveSubscription.account),
        )
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
            try:
                await container.live_feed_aggregator.stop_subscription(
                    sub.account_id, sub.symbol,
                )
            except Exception:  # noqa: BLE001
                pass
        if container is not None and container.live_feed_manager is not None:
            container.live_feed_manager.release(sub.broker, sub.symbol, "manual")
        await db.delete(sub)
        await db.flush()
        return {"deleted": True, "id": sub_id}

    deployment_ids = [c.consumer_id for c in sub.consumers
                      if c.consumer_type == "algo" and c.consumer_id]
    algo_index = await _build_algo_index(db, deployment_ids)
    return _to_response(sub, algo_index)


@router.delete("/{sub_id}", status_code=204)
async def delete_sub(sub_id: str, db: AsyncSession = Depends(get_db)):
    from sqlalchemy.orm import selectinload
    sub = (await db.execute(
        select(LiveSubscription)
        .where(LiveSubscription.id == sub_id)
        .options(
            selectinload(LiveSubscription.consumers),
            selectinload(LiveSubscription.account),
        )
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
    try:
        container = get_container()
    except AssertionError:
        container = None
    if container is not None and container.live_feed_aggregator is not None:
        try:
            await container.live_feed_aggregator.stop_subscription(sub.account_id, sub.symbol)
        except Exception:  # noqa: BLE001
            pass

    await db.delete(sub)
