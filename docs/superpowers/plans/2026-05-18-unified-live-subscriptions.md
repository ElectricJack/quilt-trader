# Unified Live Subscriptions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single subscription registry that auto-creates feeds when algorithms deploy, tracks consumer identity, multiplexes symbols onto one broker WS, routes crypto correctly, retains 1-min bars forever, and surfaces stream-disconnect state to the dashboard.

**Architecture:** New `subscription_consumers` table (one row per consumer = manual user or algorithm deployment). `dependent_count` int column dropped. `LiveSubscription` gets `asset_class`. `Algorithm.data_dependencies` renamed/reshaped to `Algorithm.assets`. The lifecycle hook auto-creates the `LiveSubscription` and consumer row when a deployment starts; the symmetric auto-delete rule removes the row when the consumer count hits zero. The aggregator opens one stream per `(broker, asset_class)` and packs many symbols onto it; bars where `vol==0 AND high==low` are dropped; stream disconnect/reconnect emits `worker_activity`. Higher timeframes (5m/15m/1h/1d) are computed lazily from the 1-min parquet.

**Tech Stack:** FastAPI + async SQLAlchemy + Alembic + pandas/pyarrow on the backend; React + react-query on the frontend. Alpaca uses `alpaca-py` (`StockDataStream` for equities, `CryptoDataStream` for crypto); Tradier uses long-poll HTTP.

**Spec:** `docs/superpowers/specs/2026-05-18-unified-live-subscriptions-design.md` (commit `18d316e`).

**Deferred (do not include in this plan — already in `docs/superpowers/backlog.md`):**
- Eager precompute of 5m/15m/1h/1d bars (lazy is sufficient for v1).
- Options chain bulk subscription (one manifest entry → all contracts).
- Force-delete a subscription (admin override).
- Migrating existing tick parquet files to the new daily-partitioned layout.
- Per-tier broker cap discovery.

---

## File Structure

**Backend — modified:**
- `coordinator/database/models.py` — add `SubscriptionConsumer` class; `LiveSubscription` gains `asset_class`, drops `dependent_count`; `Algorithm.data_dependencies` → `Algorithm.assets`.
- `coordinator/api/routes/live_subscriptions.py` — routes consume the consumers table instead of `dependent_count`.
- `coordinator/services/lifecycle.py` — `pre_start_checks` auto-creates the LiveSubscription + algo consumer row; `post_stop_actions` deletes the algo consumer row and auto-deletes the subscription when count hits zero. Reads from `algorithm.assets` instead of `data_dependencies`.
- `coordinator/services/live_feed_aggregator.py` — ghost-bar filter at bar emit; one stream per `(broker, asset_class)` instead of per `(broker, symbol)`; stream disconnect/reconnect emits `worker_activity`.
- `worker/alpaca_adapter.py` — `start_market_data_stream` accepts `asset_class` kwarg and routes equities → `StockDataStream` / crypto → `CryptoDataStream`.
- `worker/tradier_adapter.py` — `start_market_data_stream` signature gains `asset_class` (rejects `crypto` with a clear error).
- `worker/broker_adapter.py` — abstract `start_market_data_stream` adds the `asset_class` kwarg.
- `coordinator/services/data_service.py` — `aggregate_to_timeframe(symbol, timeframe)` helper for lazy 5m/15m/1h/1d.
- `packages/quilt-trader-test-algo/quilt.yaml` (the simple-ma-crossover manifest) — `requirements.data_dependencies` → top-level `assets`.

**Backend — new:**
- `coordinator/database/migrations/versions/*_unified_live_subscriptions.py` — single Alembic revision covering all schema changes + data backfill.

**Frontend — modified:**
- `dashboard/src/api/client.ts` — `LiveSubscription` interface gains `asset_class`, `consumers: SubscriptionConsumer[]`; `createLiveSubscription` body gains `asset_class`.
- `dashboard/src/api/hooks.ts` — no signature changes (consumers come through in the type).
- `dashboard/src/components/LiveSubscriptionsSection.tsx` — render consumer rows under each subscription; show "last tick at" badge; asset-class selector in the Subscribe form.

**Tests — new + modified:**
- `tests/coordinator/test_subscription_consumers.py` — new.
- `tests/coordinator/services/test_lifecycle_auto_subscribe.py` — major rewrite for new consumer model.
- `tests/coordinator/services/test_live_feed_aggregator.py` — fixtures updated to use consumer rows; new tests for ghost-bar + multi-symbol packing.
- `tests/worker/test_alpaca_adapter.py` — new tests for crypto routing.
- `tests/coordinator/services/test_data_service_aggregate.py` — new for lazy aggregation.
- `dashboard/src/components/LiveSubscriptionsSection.test.tsx` — new (the component currently has no tests).

---

## Task 1: Schema — add `subscription_consumers` table + `asset_class` column + backfill

**Files:**
- Create: `coordinator/database/migrations/versions/0001_unified_live_subscriptions.py` (Alembic auto-names with hash — use `alembic revision --autogenerate -m "unified live subscriptions"` then edit; final filename will be `<hash>_unified_live_subscriptions.py`)
- Modify: `coordinator/database/models.py`
- Test: `tests/coordinator/test_subscription_consumers.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/test_subscription_consumers.py`:

```python
import pytest
from sqlalchemy import select

from coordinator.database.models import LiveSubscription, SubscriptionConsumer


@pytest.mark.asyncio
async def test_subscription_consumer_basic_lifecycle(db_session):
    """A subscription can have multiple consumers; the relationship cascades on
    subscription delete."""
    sub = LiveSubscription(
        broker="alpaca", symbol="SPY", status="running",
        asset_class="equities", tick_retention_hours=168,
    )
    db_session.add(sub)
    await db_session.flush()

    manual = SubscriptionConsumer(
        subscription_id=sub.id, consumer_type="manual", consumer_id=None,
    )
    algo = SubscriptionConsumer(
        subscription_id=sub.id, consumer_type="algo", consumer_id="deployment-abc",
    )
    db_session.add_all([manual, algo])
    await db_session.commit()

    consumers = (await db_session.execute(
        select(SubscriptionConsumer).where(
            SubscriptionConsumer.subscription_id == sub.id
        )
    )).scalars().all()
    assert len(consumers) == 2

    # Deleting the subscription cascade-deletes its consumer rows.
    await db_session.delete(sub)
    await db_session.commit()
    remaining = (await db_session.execute(
        select(SubscriptionConsumer).where(
            SubscriptionConsumer.subscription_id == sub.id
        )
    )).scalars().all()
    assert remaining == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/coordinator/test_subscription_consumers.py -v`
Expected: FAIL — `ImportError` because `SubscriptionConsumer` doesn't exist yet and `LiveSubscription` doesn't have `asset_class`.

- [ ] **Step 3: Update the model**

In `coordinator/database/models.py`, find the `LiveSubscription` class (around line 326). Replace it with:

```python
class LiveSubscription(Base):
    __tablename__ = "live_subscriptions"
    __table_args__ = (
        UniqueConstraint("broker", "symbol", name="uq_live_subscription_broker_symbol"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    broker: Mapped[str] = mapped_column(String, nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    asset_class: Mapped[str] = mapped_column(String, nullable=False, default="equities")
    status: Mapped[str] = mapped_column(String, nullable=False, default="stopped")
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_tick_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    tick_rate_per_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tick_retention_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=168)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    consumers: Mapped[list["SubscriptionConsumer"]] = relationship(
        back_populates="subscription",
        cascade="all, delete-orphan",
    )
```

Then add a new class after `LiveSubscription`:

```python
class SubscriptionConsumer(Base):
    __tablename__ = "subscription_consumers"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id", "consumer_type", "consumer_id",
            name="uq_subscription_consumer",
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    subscription_id: Mapped[str] = mapped_column(
        String, ForeignKey("live_subscriptions.id", ondelete="CASCADE"), nullable=False,
    )
    consumer_type: Mapped[str] = mapped_column(String, nullable=False)  # 'manual' | 'algo'
    consumer_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    subscription: Mapped["LiveSubscription"] = relationship(back_populates="consumers")
```

Note: `dependent_count` was removed from `LiveSubscription`. The default `tick_retention_hours` is now 168 (1 week) per spec.

- [ ] **Step 4: Write the Alembic migration**

Generate scaffold: `python3 -m alembic -c alembic.ini revision --autogenerate -m "unified live subscriptions"`

This creates `coordinator/database/migrations/versions/<hash>_unified_live_subscriptions.py`. Replace its body with:

```python
"""unified live subscriptions

Revision ID: <generated>
Revises: <prev>
Create Date: <generated>

- Adds asset_class column to live_subscriptions (default 'equities').
- Adds subscription_consumers table.
- Backfills one 'manual' consumer row per existing live_subscription with
  dependent_count >= 1 (existing rows under today's behavior are user-initiated).
- Drops dependent_count from live_subscriptions.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, set by alembic
revision = "<keep autogenerated>"
down_revision = "<keep autogenerated>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add asset_class column.
    with op.batch_alter_table("live_subscriptions") as batch_op:
        batch_op.add_column(
            sa.Column("asset_class", sa.String(), nullable=False, server_default="equities"),
        )

    # 2. Create subscription_consumers.
    op.create_table(
        "subscription_consumers",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "subscription_id",
            sa.String(),
            sa.ForeignKey("live_subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("consumer_type", sa.String(), nullable=False),
        sa.Column("consumer_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "subscription_id", "consumer_type", "consumer_id",
            name="uq_subscription_consumer",
        ),
    )

    # 3. Backfill — every existing live_subscription with dependent_count >= 1 gets one 'manual' consumer row.
    conn = op.get_bind()
    import uuid
    from datetime import datetime, timezone
    rows = conn.execute(sa.text(
        "SELECT id FROM live_subscriptions WHERE dependent_count >= 1"
    )).fetchall()
    for r in rows:
        conn.execute(sa.text(
            "INSERT INTO subscription_consumers "
            "(id, subscription_id, consumer_type, consumer_id, created_at) "
            "VALUES (:id, :sid, 'manual', NULL, :now)"
        ), {"id": str(uuid.uuid4()), "sid": r[0], "now": datetime.now(timezone.utc)})

    # 4. Drop dependent_count.
    with op.batch_alter_table("live_subscriptions") as batch_op:
        batch_op.drop_column("dependent_count")


def downgrade() -> None:
    # Forward-only migration per spec; no rollback path implemented.
    raise NotImplementedError("downgrade not implemented")
```

- [ ] **Step 5: Run the migration**

Run: `python3 -m alembic -c alembic.ini upgrade head`
Expected: clean apply. Verify:
```bash
sqlite3 data/quilt_trader.db "PRAGMA table_info(live_subscriptions);" | grep -E "asset_class|dependent_count"
```
Expected output: one line with `asset_class`, zero lines with `dependent_count`.

- [ ] **Step 6: Run the test**

Run: `pytest tests/coordinator/test_subscription_consumers.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add coordinator/database/models.py coordinator/database/migrations/versions/*_unified_live_subscriptions.py tests/coordinator/test_subscription_consumers.py
git commit -m "feat(coord): subscription_consumers table + asset_class column"
```

---

## Task 2: Rename `Algorithm.data_dependencies` → `Algorithm.assets`

**Files:**
- Modify: `coordinator/database/models.py`
- Modify: `coordinator/database/migrations/versions/<hash>_unified_live_subscriptions.py` (extend the upgrade with the column rename)
- Test: `tests/coordinator/test_algorithm_assets.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/test_algorithm_assets.py`:

```python
import pytest

from coordinator.database.models import Algorithm


@pytest.mark.asyncio
async def test_algorithm_assets_field_exists_and_stores_list(db_session):
    """The algorithms table has an `assets` column holding the new
    {broker, symbol, asset_class} list format."""
    algo = Algorithm(
        repo_url="https://example.com/algo.git",
        name="test-algo",
        assets=[
            {"broker": "alpaca", "symbol": "SPY", "asset_class": "equities"},
            {"broker": "alpaca", "symbol": "BTCUSD", "asset_class": "crypto"},
        ],
    )
    db_session.add(algo)
    await db_session.commit()
    await db_session.refresh(algo)
    assert len(algo.assets) == 2
    assert algo.assets[0]["broker"] == "alpaca"
    assert algo.assets[1]["asset_class"] == "crypto"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/coordinator/test_algorithm_assets.py -v`
Expected: FAIL — `Algorithm` has no `assets` attribute (it has `data_dependencies`).

- [ ] **Step 3: Update the model**

In `coordinator/database/models.py`, find the `Algorithm` class (around line 57). Replace the `data_dependencies` line with:

```python
    assets: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
```

- [ ] **Step 4: Extend the Alembic migration**

Open `coordinator/database/migrations/versions/<hash>_unified_live_subscriptions.py` from Task 1. Inside `upgrade()`, before the final `# 4. Drop dependent_count` block, add:

```python
    # 5. Rename algorithms.data_dependencies -> algorithms.assets. Reset content
    # to NULL — existing rows have the old {symbol, timeframe, source} shape that
    # doesn't map cleanly to {broker, symbol, asset_class}; the user re-installs
    # affected algorithms after the migration to populate with new-format data.
    with op.batch_alter_table("algorithms") as batch_op:
        batch_op.alter_column("data_dependencies", new_column_name="assets")
    op.execute("UPDATE algorithms SET assets = NULL")
```

- [ ] **Step 5: Drop the existing DB so the migration re-runs from scratch (development only)**

WARNING: this drops local data. The user is sitting next to this — they should confirm. For now we operate on the dev DB.

Run:
```bash
rm -f data/quilt_trader.db
python3 -m alembic -c alembic.ini upgrade head
```
Expected: clean apply, no errors.

- [ ] **Step 6: Run the test**

Run: `pytest tests/coordinator/test_algorithm_assets.py -v`
Expected: PASS.

Also run the Task 1 test to make sure nothing regressed:
Run: `pytest tests/coordinator/test_subscription_consumers.py tests/coordinator/test_algorithm_assets.py -v`
Expected: both PASS.

- [ ] **Step 7: Commit**

```bash
git add coordinator/database/models.py coordinator/database/migrations/versions/*_unified_live_subscriptions.py tests/coordinator/test_algorithm_assets.py
git commit -m "feat(coord): rename Algorithm.data_dependencies -> Algorithm.assets"
```

---

## Task 3: Update the live-subscriptions routes to use the consumers table

**Files:**
- Modify: `coordinator/api/routes/live_subscriptions.py`
- Test: `tests/coordinator/test_live_subscriptions_routes.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/coordinator/test_live_subscriptions_routes.py`:

```python
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from coordinator.database.models import LiveSubscription, SubscriptionConsumer


@pytest.mark.asyncio
async def test_create_subscription_inserts_manual_consumer(client: AsyncClient, db_session):
    body = {"broker": "alpaca", "symbol": "SPY",
            "asset_class": "equities", "tick_retention_hours": 168}
    r = await client.post("/api/live-subscriptions", json=body)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["asset_class"] == "equities"
    assert len(data["consumers"]) == 1
    assert data["consumers"][0]["consumer_type"] == "manual"

    # DB has matching consumer row.
    consumers = (await db_session.execute(
        select(SubscriptionConsumer).where(SubscriptionConsumer.subscription_id == data["id"])
    )).scalars().all()
    assert len(consumers) == 1


@pytest.mark.asyncio
async def test_unsubscribe_deletes_manual_consumer_and_auto_deletes_sub(
    client: AsyncClient, db_session,
):
    """Manual unsubscribe with no other consumers: row goes away."""
    body = {"broker": "alpaca", "symbol": "QQQ", "asset_class": "equities"}
    r = await client.post("/api/live-subscriptions", json=body)
    sub_id = r.json()["id"]

    r = await client.post(f"/api/live-subscriptions/{sub_id}/unsubscribe")
    assert r.status_code == 200, r.text

    row = (await db_session.execute(
        select(LiveSubscription).where(LiveSubscription.id == sub_id)
    )).scalar_one_or_none()
    assert row is None, "subscription should be auto-deleted when consumer count hits 0"


@pytest.mark.asyncio
async def test_delete_refuses_when_consumers_exist(client: AsyncClient, db_session):
    body = {"broker": "alpaca", "symbol": "AAPL", "asset_class": "equities"}
    r = await client.post("/api/live-subscriptions", json=body)
    sub_id = r.json()["id"]

    # Add an algo consumer alongside the manual one.
    db_session.add(SubscriptionConsumer(
        subscription_id=sub_id, consumer_type="algo", consumer_id="deployment-X",
    ))
    await db_session.commit()

    r = await client.delete(f"/api/live-subscriptions/{sub_id}")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "algo" in detail or "consumer" in detail


@pytest.mark.asyncio
async def test_list_subscriptions_includes_consumers(client: AsyncClient, db_session):
    body = {"broker": "alpaca", "symbol": "NVDA", "asset_class": "equities"}
    r = await client.post("/api/live-subscriptions", json=body)
    sub_id = r.json()["id"]

    r = await client.get("/api/live-subscriptions")
    rows = r.json()
    matching = [r for r in rows if r["id"] == sub_id]
    assert len(matching) == 1
    assert "consumers" in matching[0]
    assert len(matching[0]["consumers"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/coordinator/test_live_subscriptions_routes.py -v`
Expected: FAIL — the routes still reference `dependent_count` and don't take `asset_class`.

- [ ] **Step 3: Rewrite the routes**

Open `coordinator/api/routes/live_subscriptions.py`. Replace the entire file with:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/coordinator/test_live_subscriptions_routes.py -v`
Expected: all 4 PASS.

Also run: `pytest tests/coordinator/test_subscription_consumers.py tests/coordinator/test_algorithm_assets.py -v`
Expected: still green from prior tasks.

- [ ] **Step 5: Commit**

```bash
git add coordinator/api/routes/live_subscriptions.py tests/coordinator/test_live_subscriptions_routes.py
git commit -m "feat(coord): live-subscription routes use consumers table"
```

---

## Task 4: Auto-subscribe + auto-release in lifecycle

**Files:**
- Modify: `coordinator/services/lifecycle.py`
- Test: `tests/coordinator/services/test_lifecycle_auto_subscribe.py` (rewrite)

- [ ] **Step 1: Write the failing tests**

Replace the entire contents of `tests/coordinator/services/test_lifecycle_auto_subscribe.py` with:

```python
import pytest
from sqlalchemy import select

from coordinator.database.connection import create_engine, create_session_factory
from coordinator.database.models import (
    Account, Algorithm, AlgorithmInstance, Base,
    LiveSubscription, SubscriptionConsumer, Worker,
)
from coordinator.services.lifecycle import LifecycleService
from coordinator.services.live_feed_manager import LiveFeedManager
from coordinator.services.scraper_manager import ScraperManager


@pytest.mark.asyncio
async def test_pre_start_creates_live_subscription_and_consumer():
    """Algorithm with assets in its manifest: auto-creates subscription + algo consumer."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(engine)

    async with sf() as session:
        worker = Worker(name="W", status="online")
        acct = Account(name="A", broker_type="alpaca", credentials="{}",
                       supported_asset_types=["equities", "crypto"])
        algo = Algorithm(
            repo_url="x", name="multi",
            assets=[
                {"broker": "alpaca", "symbol": "SPY", "asset_class": "equities"},
                {"broker": "alpaca", "symbol": "BTCUSD", "asset_class": "crypto"},
            ],
        )
        session.add_all([worker, acct, algo])
        await session.flush()
        inst = AlgorithmInstance(
            algorithm_id=algo.id, account_id=acct.id, worker_id=worker.id,
            status="stopped",
        )
        session.add(inst)
        await session.commit()
        inst_id = inst.id

    service = LifecycleService(
        scraper_manager=ScraperManager(),
        live_feed_manager=LiveFeedManager(),
        session_factory=sf,
    )

    async with sf() as session:
        algo = (await session.execute(select(Algorithm))).scalar_one()
        acct = (await session.execute(select(Account))).scalar_one()
        inst = (await session.execute(select(AlgorithmInstance))).scalar_one()
        await service.pre_start_checks(acct, algo, inst)

    async with sf() as session:
        subs = (await session.execute(select(LiveSubscription))).scalars().all()
        assert len(subs) == 2
        symbols = sorted(s.symbol for s in subs)
        assert symbols == ["BTCUSD", "SPY"]
        for s in subs:
            consumers = (await session.execute(
                select(SubscriptionConsumer).where(
                    SubscriptionConsumer.subscription_id == s.id
                )
            )).scalars().all()
            assert len(consumers) == 1
            assert consumers[0].consumer_type == "algo"
            assert consumers[0].consumer_id == inst_id

    await engine.dispose()


@pytest.mark.asyncio
async def test_post_stop_deletes_algo_consumer_and_auto_deletes_orphan_subscription():
    """Post-stop releases the algo consumer; if no other consumers, the
    subscription row is auto-deleted."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(engine)

    async with sf() as session:
        worker = Worker(name="W", status="online")
        acct = Account(name="A", broker_type="alpaca", credentials="{}",
                       supported_asset_types=["equities"])
        algo = Algorithm(
            repo_url="x", name="single",
            assets=[{"broker": "alpaca", "symbol": "SPY", "asset_class": "equities"}],
        )
        session.add_all([worker, acct, algo])
        await session.flush()
        inst = AlgorithmInstance(
            algorithm_id=algo.id, account_id=acct.id, worker_id=worker.id,
            status="stopped",
        )
        session.add(inst)
        await session.commit()

    service = LifecycleService(
        scraper_manager=ScraperManager(),
        live_feed_manager=LiveFeedManager(),
        session_factory=sf,
    )
    async with sf() as session:
        algo = (await session.execute(select(Algorithm))).scalar_one()
        acct = (await session.execute(select(Account))).scalar_one()
        inst = (await session.execute(select(AlgorithmInstance))).scalar_one()
        await service.pre_start_checks(acct, algo, inst)
        await service.post_stop_actions(acct, algo, inst)

    async with sf() as session:
        subs = (await session.execute(select(LiveSubscription))).scalars().all()
        assert subs == [], "subscription should be auto-deleted when last consumer is released"

    await engine.dispose()


@pytest.mark.asyncio
async def test_post_stop_preserves_subscription_held_by_manual_consumer():
    """Post-stop deletes only the algo consumer; manual consumer keeps the sub alive."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(engine)

    async with sf() as session:
        worker = Worker(name="W", status="online")
        acct = Account(name="A", broker_type="alpaca", credentials="{}",
                       supported_asset_types=["equities"])
        algo = Algorithm(
            repo_url="x", name="single",
            assets=[{"broker": "alpaca", "symbol": "SPY", "asset_class": "equities"}],
        )
        session.add_all([worker, acct, algo])
        await session.flush()
        inst = AlgorithmInstance(
            algorithm_id=algo.id, account_id=acct.id, worker_id=worker.id,
            status="stopped",
        )
        # Pre-existing subscription with a manual consumer.
        sub = LiveSubscription(broker="alpaca", symbol="SPY", asset_class="equities",
                               status="running")
        session.add_all([inst, sub])
        await session.flush()
        session.add(SubscriptionConsumer(
            subscription_id=sub.id, consumer_type="manual", consumer_id=None,
        ))
        await session.commit()

    service = LifecycleService(
        scraper_manager=ScraperManager(),
        live_feed_manager=LiveFeedManager(),
        session_factory=sf,
    )
    async with sf() as session:
        algo = (await session.execute(select(Algorithm))).scalar_one()
        acct = (await session.execute(select(Account))).scalar_one()
        inst = (await session.execute(select(AlgorithmInstance))).scalar_one()
        await service.pre_start_checks(acct, algo, inst)
        await service.post_stop_actions(acct, algo, inst)

    async with sf() as session:
        subs = (await session.execute(select(LiveSubscription))).scalars().all()
        assert len(subs) == 1
        consumers = (await session.execute(
            select(SubscriptionConsumer).where(
                SubscriptionConsumer.subscription_id == subs[0].id
            )
        )).scalars().all()
        assert len(consumers) == 1
        assert consumers[0].consumer_type == "manual"

    await engine.dispose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/coordinator/services/test_lifecycle_auto_subscribe.py -v`
Expected: FAIL — lifecycle still reads `data_dependencies`, uses `dependent_count`, doesn't auto-create.

- [ ] **Step 3: Rewrite the lifecycle methods**

In `coordinator/services/lifecycle.py`, find `_split_data_deps` (around line 35), `pre_start_checks` (around line 95), and `post_stop_actions` (around line 164). Replace them.

Replace `_split_data_deps` with:

```python
def _parse_assets(assets: Any) -> list[dict]:
    """Return list of {broker, symbol, asset_class} dicts. ``assets`` may be
    None or a list of dicts in the new structured format."""
    out: list[dict] = []
    if not assets:
        return out
    for a in assets:
        if not isinstance(a, dict):
            continue
        broker = a.get("broker")
        symbol = a.get("symbol")
        asset_class = a.get("asset_class", "equities")
        if broker and symbol:
            out.append({"broker": broker, "symbol": symbol, "asset_class": asset_class})
    return out
```

Replace the body of `pre_start_checks` (keep its signature) so the relevant section becomes:

```python
async def pre_start_checks(self, account: Any, algorithm: Any, instance: Any) -> None:
    if account.locked_by is not None and account.locked_by != instance.id:
        raise CompatibilityError(f"Account is locked by instance {account.locked_by}")
    result = self.check_compatibility(
        {
            "supported_asset_types": account.supported_asset_types or [],
            "options_level": account.options_level,
            "account_features": account.account_features or [],
            "broker_type": account.broker_type,
        },
        {
            "required_asset_types": algorithm.required_asset_types or [],
            "required_options_level": algorithm.required_options_level,
            "required_account_features": algorithm.required_account_features or [],
            "supported_brokers": algorithm.supported_brokers,
        },
    )
    if not result.compatible:
        raise CompatibilityError(
            f"Compatibility check failed: {'; '.join(result.mismatches)}"
        )

    assets = _parse_assets(algorithm.assets)
    if not assets or self._live_feed_manager is None or self._session_factory is None:
        return

    from coordinator.database.models import LiveSubscription, SubscriptionConsumer
    from sqlalchemy.orm import selectinload

    async with self._session_factory() as session:
        for asset in assets:
            broker = asset["broker"]
            symbol = asset["symbol"]
            asset_class = asset["asset_class"]
            sub = (await session.execute(
                select(LiveSubscription)
                .where(LiveSubscription.broker == broker, LiveSubscription.symbol == symbol)
                .options(selectinload(LiveSubscription.consumers))
            )).scalar_one_or_none()
            if sub is None:
                sub = LiveSubscription(
                    broker=broker, symbol=symbol, asset_class=asset_class,
                    status="running",
                )
                session.add(sub)
                await session.flush()
            # Idempotent: if this deployment already has a consumer row, skip.
            already = (await session.execute(
                select(SubscriptionConsumer).where(
                    SubscriptionConsumer.subscription_id == sub.id,
                    SubscriptionConsumer.consumer_type == "algo",
                    SubscriptionConsumer.consumer_id == instance.id,
                )
            )).scalar_one_or_none()
            if already is None:
                session.add(SubscriptionConsumer(
                    subscription_id=sub.id, consumer_type="algo",
                    consumer_id=instance.id,
                ))
            self._live_feed_manager.ensure_running(broker, symbol, instance.id)
        await session.commit()
```

Replace `post_stop_actions` body:

```python
async def post_stop_actions(
    self, account: Any, algorithm: Any, instance: Any
) -> None:
    """Release any algo consumers this instance held. If a subscription has
    no remaining consumers, delete the row (symmetric auto-delete)."""
    assets = _parse_assets(algorithm.assets)
    if not assets or self._live_feed_manager is None or self._session_factory is None:
        return

    from coordinator.database.models import LiveSubscription, SubscriptionConsumer
    from sqlalchemy.orm import selectinload

    async with self._session_factory() as session:
        for asset in assets:
            broker = asset["broker"]
            symbol = asset["symbol"]
            self._live_feed_manager.release(broker, symbol, instance.id)

            sub = (await session.execute(
                select(LiveSubscription)
                .where(LiveSubscription.broker == broker, LiveSubscription.symbol == symbol)
                .options(selectinload(LiveSubscription.consumers))
            )).scalar_one_or_none()
            if sub is None:
                continue
            # Delete this deployment's algo consumer rows.
            for c in list(sub.consumers):
                if c.consumer_type == "algo" and c.consumer_id == instance.id:
                    await session.delete(c)
            await session.flush()
            await session.refresh(sub, ["consumers"])
            # Auto-delete the sub if no consumers remain.
            if not sub.consumers:
                await session.delete(sub)
        await session.commit()
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/coordinator/services/test_lifecycle_auto_subscribe.py -v`
Expected: all 3 PASS.

Also: `pytest tests/coordinator/test_subscription_consumers.py tests/coordinator/test_live_subscriptions_routes.py tests/coordinator/test_algorithm_assets.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/lifecycle.py tests/coordinator/services/test_lifecycle_auto_subscribe.py
git commit -m "feat(coord): auto-subscribe on deploy + symmetric auto-delete"
```

---

## Task 5: Ghost-bar filter in aggregator

**Files:**
- Modify: `coordinator/services/live_feed_aggregator.py`
- Test: `tests/coordinator/services/test_live_feed_aggregator_ghost_bars.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/services/test_live_feed_aggregator_ghost_bars.py`:

```python
from datetime import datetime, timezone
from coordinator.services.live_feed_aggregator import _BarBuilder


def test_take_closed_skips_bar_with_zero_volume_and_no_range():
    """A bar where vol==0 AND high==low is quote-only / no-activity noise."""
    bb = _BarBuilder()
    bb.minute_start = datetime(2026, 5, 18, 14, 30, tzinfo=timezone.utc)
    bb.open_ = 500.0
    bb.high = 500.0
    bb.low = 500.0
    bb.close = 500.0
    bb.volume = 0.0
    later = datetime(2026, 5, 18, 14, 31, tzinfo=timezone.utc)
    row = bb.take_closed(later)
    assert row is None, "ghost bar (vol=0, high==low) must be suppressed"


def test_take_closed_keeps_bar_with_real_trade():
    bb = _BarBuilder()
    bb.minute_start = datetime(2026, 5, 18, 14, 30, tzinfo=timezone.utc)
    bb.open_ = 500.0
    bb.high = 500.5
    bb.low = 499.5
    bb.close = 500.25
    bb.volume = 100.0
    later = datetime(2026, 5, 18, 14, 31, tzinfo=timezone.utc)
    row = bb.take_closed(later)
    assert row is not None
    assert row["volume"] == 100.0
    assert row["close"] == 500.25


def test_take_closed_keeps_bar_with_volume_but_flat_price():
    """A real trade where every fill happened to be at the same price still
    matters — keep it. Only vol==0 AND high==low are ghost."""
    bb = _BarBuilder()
    bb.minute_start = datetime(2026, 5, 18, 14, 30, tzinfo=timezone.utc)
    bb.open_ = 500.0
    bb.high = 500.0
    bb.low = 500.0
    bb.close = 500.0
    bb.volume = 50.0
    later = datetime(2026, 5, 18, 14, 31, tzinfo=timezone.utc)
    row = bb.take_closed(later)
    assert row is not None
    assert row["volume"] == 50.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/coordinator/services/test_live_feed_aggregator_ghost_bars.py -v`
Expected: `test_take_closed_skips_bar_with_zero_volume_and_no_range` FAILS — current `take_closed` returns the row regardless.

- [ ] **Step 3: Add the ghost-bar filter**

In `coordinator/services/live_feed_aggregator.py`, find `_BarBuilder.take_closed` (around line 96). Replace the body:

```python
def take_closed(self, now_minute: datetime) -> Optional[dict]:
    """If a bar exists for a strictly-earlier minute, return its row.

    Returns None for ghost bars (vol==0 AND high==low) — these come from
    quote-only events with no actual trades and pollute the data view.
    """
    if self.minute_start is None or self.minute_start >= now_minute:
        return None
    is_ghost = (
        (self.volume or 0) == 0
        and self.high is not None
        and self.low is not None
        and self.high == self.low
    )
    if is_ghost:
        # Reset state but emit nothing.
        self.minute_start = None
        self.open_ = None
        self.high = None
        self.low = None
        self.close = None
        self.volume = 0.0
        return None
    row = {
        "timestamp": self.minute_start,
        "open": self.open_,
        "high": self.high,
        "low": self.low,
        "close": self.close,
        "volume": self.volume,
    }
    self.minute_start = None
    self.open_ = None
    self.high = None
    self.low = None
    self.close = None
    self.volume = 0.0
    return row
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/coordinator/services/test_live_feed_aggregator_ghost_bars.py -v`
Expected: all 3 PASS.

Also run the existing aggregator tests to make sure nothing regressed:
Run: `pytest tests/coordinator/services/test_live_feed_aggregator.py -v`
Expected: all green (any test that depended on ghost bars being emitted needs updating; if you find one, fix the fixture so the bar has a real trade).

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/live_feed_aggregator.py tests/coordinator/services/test_live_feed_aggregator_ghost_bars.py
git commit -m "feat(coord): skip ghost bars (vol=0, high==low) at aggregator"
```

---

## Task 6: Alpaca crypto routing

**Files:**
- Modify: `worker/broker_adapter.py` (abstract signature)
- Modify: `worker/alpaca_adapter.py`
- Modify: `worker/tradier_adapter.py`
- Test: `tests/worker/test_alpaca_adapter_crypto.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/worker/test_alpaca_adapter_crypto.py`:

```python
from unittest.mock import patch, MagicMock

from worker.alpaca_adapter import AlpacaAdapter


def test_start_market_data_stream_uses_stock_data_stream_for_equities():
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    captured = {}

    class FakeStockStream:
        def __init__(self, api_key, secret_key):
            captured["class"] = "stock"
        def subscribe_trades(self, h, *symbols):
            captured["symbols"] = list(symbols)
        def subscribe_quotes(self, h, *symbols): pass
        def run(self): pass
        def stop(self): pass

    with patch("alpaca.data.live.StockDataStream", FakeStockStream):
        adapter.start_market_data_stream(
            symbols=["SPY"], on_trade=lambda t: None, on_quote=lambda q: None,
            asset_class="equities",
        )
    assert captured["class"] == "stock"
    assert captured["symbols"] == ["SPY"]


def test_start_market_data_stream_uses_crypto_data_stream_for_crypto():
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    captured = {}

    class FakeCryptoStream:
        def __init__(self, api_key, secret_key):
            captured["class"] = "crypto"
        def subscribe_trades(self, h, *symbols):
            captured["symbols"] = list(symbols)
        def subscribe_quotes(self, h, *symbols): pass
        def run(self): pass
        def stop(self): pass

    with patch("alpaca.data.live.CryptoDataStream", FakeCryptoStream):
        adapter.start_market_data_stream(
            symbols=["BTC/USD"], on_trade=lambda t: None, on_quote=lambda q: None,
            asset_class="crypto",
        )
    assert captured["class"] == "crypto"
    assert captured["symbols"] == ["BTC/USD"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/worker/test_alpaca_adapter_crypto.py -v`
Expected: FAIL — `start_market_data_stream` doesn't take `asset_class`.

- [ ] **Step 3: Update the abstract base + adapters**

In `worker/broker_adapter.py`, find the abstract `start_market_data_stream` method and add the `asset_class` kwarg:

```python
    @abstractmethod
    def start_market_data_stream(
        self,
        symbols: list[str],
        on_trade,
        on_quote,
        asset_class: str = "equities",
    ) -> "MarketDataStreamHandle":
        ...
```

If `MockBrokerAdapter` overrides this method, add the same kwarg (default-only, body unchanged).

In `worker/alpaca_adapter.py`, replace `start_market_data_stream` (around line 354) with:

```python
def start_market_data_stream(
    self,
    symbols: list[str],
    on_trade,
    on_quote,
    asset_class: str = "equities",
) -> "MarketDataStreamHandle":
    from alpaca.data.live import StockDataStream, CryptoDataStream

    stream_cls = CryptoDataStream if asset_class == "crypto" else StockDataStream
    stream = stream_cls(self._api_key, self._secret_key)

    async def _trade_handler(data):
        try:
            tick = {
                "symbol": getattr(data, "symbol", None),
                "timestamp": getattr(data, "timestamp", None) or datetime.now(timezone.utc),
                "price": float(getattr(data, "price", 0.0) or 0.0),
                "size": float(getattr(data, "size", 0.0) or 0.0),
            }
            on_trade(tick)
        except Exception:
            logger.exception("alpaca trade handler error")

    async def _quote_handler(data):
        try:
            tick = {
                "symbol": getattr(data, "symbol", None),
                "timestamp": getattr(data, "timestamp", None) or datetime.now(timezone.utc),
                "bid": float(getattr(data, "bid_price", 0.0) or 0.0),
                "ask": float(getattr(data, "ask_price", 0.0) or 0.0),
                "bid_size": float(getattr(data, "bid_size", 0.0) or 0.0),
                "ask_size": float(getattr(data, "ask_size", 0.0) or 0.0),
            }
            on_quote(tick)
        except Exception:
            logger.exception("alpaca quote handler error")

    stream.subscribe_trades(_trade_handler, *symbols)
    stream.subscribe_quotes(_quote_handler, *symbols)
    return _AlpacaStreamHandle(stream)
```

In `worker/tradier_adapter.py`, find `start_market_data_stream` (around line 334) and add the `asset_class` kwarg. Reject crypto explicitly:

```python
def start_market_data_stream(
    self,
    symbols: list[str],
    on_trade,
    on_quote,
    asset_class: str = "equities",
) -> "MarketDataStreamHandle":
    if asset_class == "crypto":
        raise ValueError("Tradier does not support crypto streaming")
    stream_base = _LIVE_BASE
    return _TradierStreamHandle(
        stream_base=stream_base,
        access_token=self._access_token,
        symbols=symbols,
        on_trade=on_trade,
        on_quote=on_quote,
    )
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/worker/test_alpaca_adapter_crypto.py tests/worker/test_tradier_adapter.py -v`
Expected: alpaca crypto tests PASS, no Tradier regression.

- [ ] **Step 5: Commit**

```bash
git add worker/broker_adapter.py worker/alpaca_adapter.py worker/tradier_adapter.py tests/worker/test_alpaca_adapter_crypto.py
git commit -m "feat(adapter): route alpaca crypto via CryptoDataStream"
```

---

## Task 7: Aggregator opens one stream per (broker, asset_class), routes asset_class

**Files:**
- Modify: `coordinator/services/live_feed_aggregator.py`
- Test: `tests/coordinator/services/test_live_feed_aggregator_routing.py` (create)

This task makes the aggregator pass `asset_class` to the broker adapter when starting a stream. Multi-symbol packing onto one connection is added in Task 8 — here we just propagate the asset_class through.

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/services/test_live_feed_aggregator_routing.py`:

```python
import pytest
from unittest.mock import MagicMock

from coordinator.services.live_feed_aggregator import LiveFeedAggregator


@pytest.mark.asyncio
async def test_aggregator_passes_asset_class_to_adapter(tmp_path, monkeypatch):
    """When start_subscription is called with asset_class='crypto', the
    broker adapter's start_market_data_stream receives asset_class='crypto'."""
    captured = {}

    class FakeHandle:
        def close(self): pass

    class FakeAdapter:
        def start_market_data_stream(self, symbols, on_trade, on_quote, asset_class="equities"):
            captured["symbols"] = symbols
            captured["asset_class"] = asset_class
            return FakeHandle()
        def close(self): pass

    # The aggregator's existing tests show how it loads accounts/adapters via
    # the live_feed_account.<broker> setting. For this focused test, monkeypatch
    # the adapter factory.
    from coordinator.services import live_feed_aggregator as mod

    async def fake_adapter_for_broker(broker):
        return FakeAdapter()

    agg = LiveFeedAggregator(session_factory=None, encryption=None)
    monkeypatch.setattr(agg, "_adapter_for_broker", fake_adapter_for_broker)
    # Path setup: writes go under tmp_path.
    monkeypatch.setattr(agg, "_ticks_dir",
                        lambda b, s: tmp_path / b / s / "ticks")

    await agg.start_subscription("alpaca", "BTCUSD", "crypto")

    assert captured["symbols"] == ["BTCUSD"]
    assert captured["asset_class"] == "crypto"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/test_live_feed_aggregator_routing.py -v`
Expected: FAIL — `start_subscription` is currently a two-arg method `(broker, symbol)`.

- [ ] **Step 3: Update `start_subscription` to accept asset_class**

In `coordinator/services/live_feed_aggregator.py`, find `start_subscription` (likely near other `async def` methods). Add the `asset_class` parameter and thread it through to the adapter call. The call site that does:

```python
state.handle = adapter.start_market_data_stream(
    [symbol], _on_trade, _on_quote
)
```

becomes:

```python
state.handle = adapter.start_market_data_stream(
    [symbol], _on_trade, _on_quote, asset_class=asset_class,
)
```

If `start_subscription` doesn't currently take asset_class, change its signature to:

```python
async def start_subscription(self, broker: str, symbol: str, asset_class: str = "equities") -> None:
    ...
```

Look at every existing call site of `start_subscription` and update it to pass the LiveSubscription's `asset_class`. The route handler in `live_subscriptions.py` was already updated in Task 3 to pass `asset_class`. The aggregator may also self-start subscriptions from a DB sweep — find and update that too (search for `start_subscription(`).

- [ ] **Step 4: Run the test**

Run: `pytest tests/coordinator/services/test_live_feed_aggregator_routing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/live_feed_aggregator.py tests/coordinator/services/test_live_feed_aggregator_routing.py
git commit -m "feat(coord): aggregator threads asset_class to broker adapter"
```

---

## Task 8: Multi-symbol packing — one stream per (broker, asset_class) covers many symbols

**Files:**
- Modify: `coordinator/services/live_feed_aggregator.py`
- Test: `tests/coordinator/services/test_live_feed_aggregator_packing.py` (create)

This is the bigger streaming refactor: today the aggregator opens one stream handle per `(broker, symbol)`. After this task, it opens one stream handle per `(broker, asset_class)` and adds/removes symbols from that stream's subscribe set as subscriptions come and go, up to a `MAX_SYMBOLS_PER_STREAM` cap.

- [ ] **Step 1: Write the failing tests**

Create `tests/coordinator/services/test_live_feed_aggregator_packing.py`:

```python
import pytest
from unittest.mock import MagicMock

from coordinator.services.live_feed_aggregator import LiveFeedAggregator


@pytest.mark.asyncio
async def test_two_equities_subscriptions_share_one_stream(tmp_path, monkeypatch):
    """Subscribing to two equities on alpaca opens exactly one stream
    handle whose symbol set contains both."""
    handles_opened = []
    symbol_sets: list[set[str]] = []

    class FakeHandle:
        def __init__(self):
            self.symbols: set[str] = set()
            handles_opened.append(self)
            symbol_sets.append(self.symbols)
        def add_symbols(self, syms):
            self.symbols.update(syms)
        def remove_symbols(self, syms):
            self.symbols.difference_update(syms)
        def close(self): pass

    class FakeAdapter:
        def start_market_data_stream(self, symbols, on_trade, on_quote, asset_class="equities"):
            h = FakeHandle()
            h.add_symbols(symbols)
            return h
        def close(self): pass

    agg = LiveFeedAggregator(session_factory=None, encryption=None)
    async def fake_adapter_for_broker(broker):
        return FakeAdapter()
    monkeypatch.setattr(agg, "_adapter_for_broker", fake_adapter_for_broker)
    monkeypatch.setattr(agg, "_ticks_dir",
                        lambda b, s: tmp_path / b / s / "ticks")

    await agg.start_subscription("alpaca", "SPY", "equities")
    await agg.start_subscription("alpaca", "QQQ", "equities")
    assert len(handles_opened) == 1, "second equity subscription should reuse the stream"
    assert handles_opened[0].symbols == {"SPY", "QQQ"}


@pytest.mark.asyncio
async def test_equities_and_crypto_open_separate_streams(tmp_path, monkeypatch):
    handles_opened = []

    class FakeHandle:
        def __init__(self):
            self.symbols: set[str] = set()
            handles_opened.append(self)
        def add_symbols(self, syms):
            self.symbols.update(syms)
        def remove_symbols(self, syms):
            self.symbols.difference_update(syms)
        def close(self): pass

    class FakeAdapter:
        def start_market_data_stream(self, symbols, on_trade, on_quote, asset_class="equities"):
            h = FakeHandle()
            h.add_symbols(symbols)
            return h
        def close(self): pass

    agg = LiveFeedAggregator(session_factory=None, encryption=None)
    async def fake_adapter_for_broker(broker):
        return FakeAdapter()
    monkeypatch.setattr(agg, "_adapter_for_broker", fake_adapter_for_broker)
    monkeypatch.setattr(agg, "_ticks_dir",
                        lambda b, s: tmp_path / b / s / "ticks")

    await agg.start_subscription("alpaca", "SPY", "equities")
    await agg.start_subscription("alpaca", "BTCUSD", "crypto")
    assert len(handles_opened) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/coordinator/services/test_live_feed_aggregator_packing.py -v`
Expected: FAIL — current implementation opens one handle per symbol.

- [ ] **Step 3: Refactor the aggregator**

This is the largest single change in the plan. The aggregator currently maintains `_states: dict[(broker, symbol), _SubState]`. We add a parallel `_streams: dict[(broker, asset_class), _StreamConn]` where each `_StreamConn` owns one broker stream handle and a set of symbols.

In `coordinator/services/live_feed_aggregator.py`, add a new dataclass near the top (after `_SubState`):

```python
@dataclass
class _StreamConn:
    """One broker stream connection that can carry many symbols."""
    handle: Optional[object] = None
    symbols: set[str] = field(default_factory=set)
```

Add a class-level constant for the broker cap (we hardcode 30 for Alpaca per the spec; Tradier defaults conservatively):

```python
_MAX_SYMBOLS_PER_STREAM = {
    ("alpaca", "equities"): 30,
    ("alpaca", "crypto"): 30,
    ("tradier", "equities"): 100,
}
```

In the `LiveFeedAggregator.__init__`, add:

```python
self._streams: dict[tuple[str, str], _StreamConn] = {}
```

Replace `start_subscription` body so that instead of calling `adapter.start_market_data_stream` per symbol, it routes through the stream connection:

```python
async def start_subscription(self, broker: str, symbol: str, asset_class: str = "equities") -> None:
    """Ensure a stream connection exists for (broker, asset_class), then add
    this symbol to its subscribe set."""
    key = (broker, asset_class)
    conn = self._streams.get(key)
    if conn is None:
        conn = _StreamConn()
        self._streams[key] = conn
        adapter = await self._adapter_for_broker(broker)
        cap = _MAX_SYMBOLS_PER_STREAM.get(key, 30)
        # Start with this one symbol; we add more via add_symbols below.
        conn.handle = adapter.start_market_data_stream(
            [symbol], self._make_on_trade(broker), self._make_on_quote(broker),
            asset_class=asset_class,
        )
        conn.symbols.add(symbol)
    else:
        cap = _MAX_SYMBOLS_PER_STREAM.get(key, 30)
        if len(conn.symbols) >= cap:
            raise RuntimeError(
                f"broker stream cap reached for {broker}/{asset_class}: "
                f"{cap} symbols. Stop a subscription before adding more."
            )
        if symbol not in conn.symbols:
            # Most stream handles expose add_symbols; if not, fall back to restart.
            add = getattr(conn.handle, "add_symbols", None)
            if callable(add):
                add([symbol])
            else:
                # Restart-from-scratch fallback.
                old = conn.handle
                conn.symbols.add(symbol)
                adapter = await self._adapter_for_broker(broker)
                conn.handle = adapter.start_market_data_stream(
                    list(conn.symbols), self._make_on_trade(broker),
                    self._make_on_quote(broker), asset_class=asset_class,
                )
                try:
                    old.close()
                except Exception:
                    pass
            conn.symbols.add(symbol)

    # The per-symbol _SubState bookkeeping continues as before for bar/tick buffering.
    if (broker, symbol) not in self._states:
        self._states[(broker, symbol)] = _SubState()
```

Add a corresponding `stop_subscription` change:

```python
async def stop_subscription(self, broker: str, symbol: str) -> None:
    """Remove this symbol from the broker stream's subscribe set. If the
    stream's symbol set becomes empty, close the connection."""
    state = self._states.pop((broker, symbol), None)
    if state is not None and state.handle is not None:
        # Legacy per-symbol handle (pre-packing) — close it directly.
        try:
            state.handle.close()
        except Exception:
            pass

    # Find the (broker, asset_class) stream holding this symbol and remove it.
    for key, conn in list(self._streams.items()):
        if symbol in conn.symbols:
            conn.symbols.discard(symbol)
            remove = getattr(conn.handle, "remove_symbols", None)
            if callable(remove):
                remove([symbol])
            if not conn.symbols:
                try:
                    if conn.handle is not None:
                        conn.handle.close()
                except Exception:
                    pass
                del self._streams[key]
            return
```

Add the `_make_on_trade` / `_make_on_quote` helpers that produce per-broker callbacks (routing trades/quotes back to the right `_SubState` by symbol):

```python
def _make_on_trade(self, broker: str):
    def _on_trade(tick: dict) -> None:
        symbol = tick.get("symbol")
        state = self._states.get((broker, symbol))
        if state is None:
            return
        with state.lock:
            state.trades.append(tick)
            state.last_tick_at = tick.get("timestamp")
            state.bar.add(tick["timestamp"], tick["price"], tick["size"])
    return _on_trade

def _make_on_quote(self, broker: str):
    def _on_quote(tick: dict) -> None:
        symbol = tick.get("symbol")
        state = self._states.get((broker, symbol))
        if state is None:
            return
        with state.lock:
            state.quotes.append(tick)
    return _on_quote
```

(If the existing code already has these as closures inside `_run` per `_SubState`, hoist them to the class level. The existing inline closures need to be removed so the new dispatch-by-symbol works.)

- [ ] **Step 4: Run the new tests**

Run: `pytest tests/coordinator/services/test_live_feed_aggregator_packing.py tests/coordinator/services/test_live_feed_aggregator_routing.py -v`
Expected: all PASS.

Also run the existing aggregator tests:
Run: `pytest tests/coordinator/services/test_live_feed_aggregator.py -v`
Expected: green. (Some existing tests may need fixture updates to use the new pattern; if a test fails because it asserted "one handle per symbol", update it to assert "one handle per asset_class with that symbol in its set".)

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/live_feed_aggregator.py tests/coordinator/services/test_live_feed_aggregator_packing.py
git commit -m "feat(coord): multi-symbol stream packing per (broker, asset_class)"
```

---

## Task 9: Stream disconnect / reconnect emits worker_activity

**Files:**
- Modify: `coordinator/services/live_feed_aggregator.py`
- Test: `tests/coordinator/services/test_live_feed_aggregator_disconnect_events.py` (create)

When a stream connection drops or reconnects, we emit a `WorkerActivity` row so the dashboard's activity stream shows what happened. The stream-level reconnect logic (Tradier specifically) is already in place from `6c0f92c`; this task surfaces it.

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/services/test_live_feed_aggregator_disconnect_events.py`:

```python
import pytest
from sqlalchemy import select

from coordinator.database.connection import create_engine, create_session_factory
from coordinator.database.models import Base, WorkerActivity, Worker
from coordinator.services.live_feed_aggregator import LiveFeedAggregator


@pytest.mark.asyncio
async def test_emit_stream_disconnect_inserts_worker_activity_row():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(engine)

    # A worker row is required (worker_id FK).
    async with sf() as session:
        w = Worker(name="coord", status="online")
        session.add(w)
        await session.commit()
        worker_id = w.id

    agg = LiveFeedAggregator(session_factory=sf, encryption=None)
    agg._coord_worker_id = worker_id  # injected by the lifespan in prod

    await agg._emit_stream_event(
        broker="tradier", asset_class="equities", symbols=["SPY", "QQQ"],
        event_type="stream_disconnect", reason="connection reset",
    )

    async with sf() as session:
        rows = (await session.execute(select(WorkerActivity))).scalars().all()
        assert len(rows) == 1
        assert rows[0].event_type == "stream_disconnect"
        assert rows[0].severity == "warn"
        assert "SPY" in rows[0].payload["symbols"]

    await engine.dispose()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/test_live_feed_aggregator_disconnect_events.py -v`
Expected: FAIL — `_emit_stream_event` doesn't exist yet.

- [ ] **Step 3: Add `_emit_stream_event`**

In `coordinator/services/live_feed_aggregator.py`, add a method on the aggregator:

```python
async def _emit_stream_event(
    self, broker: str, asset_class: str, symbols: list[str],
    event_type: str, reason: str = "",
) -> None:
    """Insert a worker_activity row describing a stream disconnect/reconnect.

    event_type='stream_disconnect' → severity='warn'.
    event_type='stream_reconnect'  → severity='info'.
    """
    if self._session_factory is None or self._coord_worker_id is None:
        return
    severity = "warn" if event_type == "stream_disconnect" else "info"
    from coordinator.database.models import WorkerActivity
    async with self._session_factory() as session:
        session.add(WorkerActivity(
            worker_id=self._coord_worker_id,
            kind="event",
            event_type=event_type,
            severity=severity,
            payload={
                "broker": broker,
                "asset_class": asset_class,
                "symbols": list(symbols),
                "reason": reason,
            },
        ))
        await session.commit()
```

Add `self._coord_worker_id: Optional[str] = None` to `__init__`. The coord's lifespan should set it after worker registration — for the test we inject it directly.

To actually trigger this from broker stream events, the broker adapter would need to call back into the aggregator on disconnect. For v1, a clean integration point is: each broker stream handle accepts a `on_disconnect`/`on_reconnect` callback alongside `on_trade`/`on_quote`. Plumb these through later — for now, the helper exists and tests confirm it works. **Mark this as a known integration gap** to wire after the aggregator refactor in Task 8 settles in production.

For the immediate end-to-end behavior, modify `_make_on_trade`/`_make_on_quote` (Task 8) so they also touch `LiveSubscription.last_tick_at` periodically (debounced) — and the existing "stale tick" check in the aggregator (if present) can fire `_emit_stream_event` when a stream goes quiet beyond a threshold.

Concretely, add a sweep loop method:

```python
async def _stale_stream_sweep(self) -> None:
    """Background task: every 30s, check if any stream has had no tick for
    > 60s during expected hours. If so, emit a stream_disconnect event."""
    import asyncio
    from datetime import datetime, timezone, timedelta
    while not self._stop.is_set():
        await asyncio.sleep(30.0)
        now = datetime.now(timezone.utc)
        for key, conn in list(self._streams.items()):
            broker, asset_class = key
            # Pick any state under this connection to read last_tick_at.
            relevant_states = [
                self._states.get((broker, sym)) for sym in conn.symbols
            ]
            last = max(
                (s.last_tick_at for s in relevant_states if s and s.last_tick_at),
                default=None,
            )
            if last is None or now - last > timedelta(seconds=60):
                # Already-emitted suppression: simple flag on the conn.
                already = getattr(conn, "_disconnect_emitted", False)
                if not already:
                    await self._emit_stream_event(
                        broker=broker, asset_class=asset_class,
                        symbols=sorted(conn.symbols),
                        event_type="stream_disconnect",
                        reason=f"no tick for > 60s (last={last})",
                    )
                    conn._disconnect_emitted = True
            else:
                if getattr(conn, "_disconnect_emitted", False):
                    await self._emit_stream_event(
                        broker=broker, asset_class=asset_class,
                        symbols=sorted(conn.symbols),
                        event_type="stream_reconnect",
                        reason="ticks resumed",
                    )
                    conn._disconnect_emitted = False
```

Kick this off from `LiveFeedAggregator.start()` (or wherever the existing background tasks are kicked off):

```python
self._sweep_task = asyncio.create_task(self._stale_stream_sweep())
```

And cancel it in `stop()`.

- [ ] **Step 4: Run the test**

Run: `pytest tests/coordinator/services/test_live_feed_aggregator_disconnect_events.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/live_feed_aggregator.py tests/coordinator/services/test_live_feed_aggregator_disconnect_events.py
git commit -m "feat(coord): emit stream_disconnect/reconnect worker_activity events"
```

---

## Task 10: Lazy 5m/15m/1h/1d aggregation on read

**Files:**
- Modify: `coordinator/services/data_service.py`
- Test: `tests/coordinator/services/test_data_service_aggregate.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/services/test_data_service_aggregate.py`:

```python
import pandas as pd
import pytest

from coordinator.services.data_service import DataService


def _fixture_1min_bars():
    """6 minutes of 1m bars: easy to validate 5m aggregation."""
    return pd.DataFrame([
        {"timestamp": pd.Timestamp("2026-05-18 14:30:00", tz="UTC"),
         "open": 500.0, "high": 501.0, "low": 499.5, "close": 500.5, "volume": 100},
        {"timestamp": pd.Timestamp("2026-05-18 14:31:00", tz="UTC"),
         "open": 500.5, "high": 502.0, "low": 500.0, "close": 501.5, "volume": 200},
        {"timestamp": pd.Timestamp("2026-05-18 14:32:00", tz="UTC"),
         "open": 501.5, "high": 502.5, "low": 501.0, "close": 502.0, "volume": 150},
        {"timestamp": pd.Timestamp("2026-05-18 14:33:00", tz="UTC"),
         "open": 502.0, "high": 503.0, "low": 501.5, "close": 502.5, "volume": 175},
        {"timestamp": pd.Timestamp("2026-05-18 14:34:00", tz="UTC"),
         "open": 502.5, "high": 503.5, "low": 502.0, "close": 503.0, "volume": 125},
        {"timestamp": pd.Timestamp("2026-05-18 14:35:00", tz="UTC"),
         "open": 503.0, "high": 504.0, "low": 502.5, "close": 503.5, "volume": 100},
    ])


def test_aggregate_1min_to_5min_produces_correct_ohlcv():
    df = _fixture_1min_bars()
    out = DataService.aggregate_bars(df, "5min")
    # 6 minutes of input @ 14:30..14:35 → two 5-min buckets: 14:30 (5 bars) + 14:35 (1 bar).
    assert len(out) == 2
    first = out.iloc[0]
    assert first["open"] == 500.0          # first bar's open
    assert first["high"] == 503.5          # max of the 5 bars (14:34: 503.5)
    assert first["low"] == 499.5           # min of the 5 bars (14:30: 499.5)
    assert first["close"] == 503.0         # last bar's close in bucket (14:34: 503.0)
    assert first["volume"] == 100 + 200 + 150 + 175 + 125

    second = out.iloc[1]
    assert second["open"] == 503.0
    assert second["close"] == 503.5
    assert second["volume"] == 100


def test_aggregate_passthroughs_1min():
    df = _fixture_1min_bars()
    out = DataService.aggregate_bars(df, "1min")
    pd.testing.assert_frame_equal(out.reset_index(drop=True), df.reset_index(drop=True))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/test_data_service_aggregate.py -v`
Expected: FAIL — `DataService.aggregate_bars` doesn't exist.

- [ ] **Step 3: Add the aggregator**

In `coordinator/services/data_service.py`, add a static method on `DataService`:

```python
@staticmethod
def aggregate_bars(df_1min: pd.DataFrame, target: str) -> pd.DataFrame:
    """Lazily aggregate 1-min OHLCV bars to a higher timeframe.

    target: "1min" (pass-through) | "5min" | "15min" | "1h" | "1d".
    df_1min must have columns: timestamp, open, high, low, close, volume.
    Returns the aggregated DataFrame, same column shape.
    """
    if target == "1min":
        return df_1min
    pandas_rule = {"5min": "5min", "15min": "15min", "1h": "1h", "1d": "1D"}.get(target)
    if pandas_rule is None:
        raise ValueError(f"unsupported target timeframe: {target!r}")
    df = df_1min.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp")
    out = df.resample(pandas_rule, label="left", closed="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open"]).reset_index()
    return out
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/coordinator/services/test_data_service_aggregate.py -v`
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/data_service.py tests/coordinator/services/test_data_service_aggregate.py
git commit -m "feat(coord): DataService.aggregate_bars for lazy 5m/15m/1h/1d"
```

---

## Task 11: Migrate simple-ma-crossover manifest

**Files:**
- Modify: `packages/quilt-trader-test-algo/quilt.yaml`

- [ ] **Step 1: Update the manifest**

Open `packages/quilt-trader-test-algo/quilt.yaml`. Replace the `requirements:` block with the new format:

```yaml
name: simple-ma-crossover
type: algorithm
version: 1.0.0
description: A tiny test algorithm for quilt-trader — buys SPY on a fast-SMA over slow-SMA crossover, sells on the reverse. Suitable for smoke-testing install-from-URL, algorithm instance lifecycle, and basic trade execution.
entry_point: algorithm.py
class_name: MaCrossoverAlgorithm

requirements:
  asset_types:
    - equities

assets:
  - broker: alpaca
    symbol: SPY
    asset_class: equities

config:
  parameters:
    - name: symbol
      type: string
      default: SPY
      description: Underlying to trade.
    - name: fast_window
      type: int
      default: 10
      description: Fast SMA window in bars.
    - name: slow_window
      type: int
      default: 30
      description: Slow SMA window in bars. Must be greater than fast_window.
    - name: target_allocation_pct
      type: float
      default: 0.95
      description: |
        Fraction of buying power to deploy per entry. Default 0.95 leaves a
        5% buffer for slippage and fees so market fills (next-bar open) don't
        trip the framework's insufficient-buying-power rejection.
```

- [ ] **Step 2: Verify the manifest parses**

If there's a manifest parser somewhere (look for `parse_quilt_yaml` or similar in `worker/` or `coordinator/`), run a quick smoke test:

```bash
python3 -c "
import yaml
with open('packages/quilt-trader-test-algo/quilt.yaml') as f:
    m = yaml.safe_load(f)
assert m['assets'][0] == {'broker': 'alpaca', 'symbol': 'SPY', 'asset_class': 'equities'}
print('manifest OK')
"
```
Expected: `manifest OK`.

- [ ] **Step 3: Commit**

```bash
git add packages/quilt-trader-test-algo/quilt.yaml
git commit -m "feat(packages): simple-ma-crossover uses new assets manifest"
```

---

## Task 12: Frontend — surface `consumers`, `asset_class`, "last tick at"

**Files:**
- Modify: `dashboard/src/api/client.ts`
- Modify: `dashboard/src/components/LiveSubscriptionsSection.tsx`
- Test: `dashboard/src/components/LiveSubscriptionsSection.test.tsx` (create)

- [ ] **Step 1: Update the TypeScript types**

In `dashboard/src/api/client.ts`, replace the `LiveSubscription` interface (around line 827) with:

```typescript
export interface SubscriptionConsumer {
  id: string;
  consumer_type: "manual" | "algo";
  consumer_id: string | null;
  created_at: string | null;
}

export interface LiveSubscription {
  id: string;
  broker: string;
  symbol: string;
  asset_class: string;
  tick_retention_hours: number;
  tick_rate_per_min: number | null;
  status: string;
  created_at: string | null;
  last_tick_at: string | null;
  error_message: string | null;
  consumers: SubscriptionConsumer[];
}
```

And update `createLiveSubscription` to require `asset_class`:

```typescript
createLiveSubscription(body: {
  broker: string;
  symbol: string;
  asset_class: string;
  tick_retention_hours?: number;
}): Promise<LiveSubscription> {
  return request<LiveSubscription>("/api/live-subscriptions", {
    method: "POST",
    body: JSON.stringify(body),
  });
},
```

The `unsubscribeLiveSubscription` return shape changes — on auto-delete the backend returns `{deleted: true, id}` instead of the subscription. Update its type to a union:

```typescript
unsubscribeLiveSubscription(id: string): Promise<LiveSubscription | { deleted: true; id: string }> {
  return request<LiveSubscription | { deleted: true; id: string }>(
    `/api/live-subscriptions/${encodeURIComponent(id)}/unsubscribe`,
    { method: "POST" }
  );
},
```

- [ ] **Step 2: Update the component**

Replace `dashboard/src/components/LiveSubscriptionsSection.tsx` with:

```typescript
import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import {
  useLiveSubscriptions,
  useCreateLiveSubscription,
  useDeleteLiveSubscription,
  useUnsubscribeLiveSubscription,
  useLiveSubStorageEstimate,
} from "../api/hooks";
import { useUIStore } from "../stores/ui";

type Broker = "alpaca" | "tradier";
type AssetClass = "equities" | "crypto" | "options";

function formatBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function timeSince(iso: string | null): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60_000) return `${Math.floor(ms / 1000)}s ago`;
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`;
  return `${Math.floor(ms / 3_600_000)}h ago`;
}

export function LiveSubscriptionsSection() {
  const { data: subs = [], isLoading } = useLiveSubscriptions();
  const create = useCreateLiveSubscription();
  const del = useDeleteLiveSubscription();
  const unsub = useUnsubscribeLiveSubscription();
  const addAlert = useUIStore((s) => s.addAlert);

  const [adding, setAdding] = useState(false);
  const [broker, setBroker] = useState<Broker>("alpaca");
  const [assetClass, setAssetClass] = useState<AssetClass>("equities");
  const [symbol, setSymbol] = useState("");
  const [retention, setRetention] = useState(168);

  const trimmedSymbol = symbol.trim();
  const { data: estimate } = useLiveSubStorageEstimate(
    adding && trimmedSymbol ? broker : null,
    adding && trimmedSymbol ? trimmedSymbol : null,
    retention
  );

  async function handleAdd() {
    if (!trimmedSymbol) return;
    try {
      await create.mutateAsync({
        broker, symbol: trimmedSymbol, asset_class: assetClass,
        tick_retention_hours: retention,
      });
      addAlert({
        message: `Subscribed to ${broker}_live:${trimmedSymbol}.`,
        severity: "success",
      });
      setAdding(false);
      setSymbol("");
    } catch (e) {
      addAlert({
        message: `Subscribe failed: ${(e as Error).message}`,
        severity: "error",
      });
    }
  }

  async function handleDelete(id: string, label: string) {
    try {
      const after = await unsub.mutateAsync(id);
      if ("deleted" in after && after.deleted) {
        addAlert({ message: `Unsubscribed from ${label}.`, severity: "success" });
      } else {
        const remaining = (after as { consumers: { consumer_type: string }[] }).consumers
          .filter((c) => c.consumer_type === "algo").length;
        addAlert({
          message: `Unsubscribed from ${label}; ${remaining} algorithm consumer(s) still holding the feed.`,
          severity: "info",
        });
      }
    } catch (e) {
      addAlert({
        message: `Unsubscribe failed: ${(e as Error).message}`,
        severity: "error",
      });
    }
  }

  return (
    <section>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-400 uppercase">
          Live Subscriptions{" "}
          {subs.length > 0 && (
            <span className="font-normal text-gray-500">({subs.length})</span>
          )}
        </h2>
        <button
          onClick={() => setAdding(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 transition-colors"
        >
          <Plus size={14} /> Subscribe
        </button>
      </div>

      {isLoading ? (
        <p className="text-gray-400 text-sm">Loading…</p>
      ) : subs.length === 0 ? (
        <p className="text-gray-500 text-sm">No live subscriptions.</p>
      ) : (
        <div className="space-y-2">
          {subs.map((s) => {
            const label = `${s.broker}_live:${s.symbol}`;
            const stale = !s.last_tick_at ||
              (Date.now() - new Date(s.last_tick_at).getTime()) > 60_000;
            return (
              <div
                key={s.id}
                className="bg-gray-900 border border-gray-800 rounded px-3 py-2"
              >
                <div className="flex items-center justify-between text-sm">
                  <div className="flex items-center gap-3 flex-wrap min-w-0">
                    <span className="text-indigo-400 font-mono">{s.broker}_live</span>
                    <span className="font-mono text-gray-200">{s.symbol}</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded border bg-gray-800 text-gray-400 border-gray-700">
                      {s.asset_class}
                    </span>
                    {s.tick_rate_per_min != null && (
                      <span className="text-xs text-gray-500">
                        ~{Math.round(s.tick_rate_per_min)}/min
                      </span>
                    )}
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded border ${
                        stale
                          ? "bg-red-900/40 text-red-300 border-red-800"
                          : "bg-green-900/40 text-green-300 border-green-800"
                      }`}
                    >
                      last tick: {timeSince(s.last_tick_at)}
                    </span>
                  </div>
                  <button
                    onClick={() => handleDelete(s.id, label)}
                    className="text-gray-400 hover:text-red-400 transition-colors"
                    title="Unsubscribe"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
                {s.consumers.length > 0 && (
                  <div className="mt-1.5 text-xs text-gray-500">
                    Consumers:{" "}
                    {s.consumers.map((c, i) => (
                      <span key={c.id}>
                        {i > 0 && ", "}
                        {c.consumer_type === "manual"
                          ? "manual"
                          : `algo: ${c.consumer_id?.slice(0, 8) ?? "?"}`}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {adding && (
        <div className="mt-3 bg-gray-900 border border-gray-700 rounded p-3 flex gap-2 items-end flex-wrap">
          <label className="flex flex-col gap-1 text-xs text-gray-400">
            <span>Broker</span>
            <select
              value={broker}
              onChange={(e) => setBroker(e.target.value as Broker)}
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
            >
              <option value="alpaca">alpaca</option>
              <option value="tradier">tradier</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-gray-400">
            <span>Asset class</span>
            <select
              value={assetClass}
              onChange={(e) => setAssetClass(e.target.value as AssetClass)}
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
            >
              <option value="equities">equities</option>
              <option value="crypto">crypto</option>
              <option value="options">options</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-gray-400">
            <span>Symbol</span>
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              placeholder="SPY"
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 w-28 font-mono"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-gray-400">
            <span>Tick retention</span>
            <select
              value={retention}
              onChange={(e) => setRetention(Number(e.target.value))}
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
            >
              <option value={24}>24h</option>
              <option value={168}>7d</option>
              <option value={720}>30d</option>
              <option value={8760}>1y</option>
            </select>
          </label>
          {estimate && (
            <span className="text-xs text-gray-500 self-center ml-2">
              ~{formatBytes(estimate.projected_bytes)}
            </span>
          )}
          <button
            onClick={handleAdd}
            disabled={!trimmedSymbol || create.isPending}
            className="px-3 py-1.5 rounded text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 transition-colors"
          >
            Add
          </button>
          <button
            onClick={() => { setAdding(false); setSymbol(""); }}
            className="px-3 py-1.5 rounded text-sm text-gray-300 bg-gray-700 hover:bg-gray-600 transition-colors"
          >
            Cancel
          </button>
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 3: Add a smoke test**

Create `dashboard/src/components/LiveSubscriptionsSection.test.tsx`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("../api/hooks", () => ({
  useLiveSubscriptions: () => ({
    data: [
      {
        id: "sub-1",
        broker: "alpaca",
        symbol: "SPY",
        asset_class: "equities",
        tick_retention_hours: 168,
        tick_rate_per_min: 200,
        status: "running",
        created_at: null,
        last_tick_at: new Date().toISOString(),
        error_message: null,
        consumers: [
          { id: "c1", consumer_type: "manual", consumer_id: null, created_at: null },
          { id: "c2", consumer_type: "algo", consumer_id: "deployment-abc12345", created_at: null },
        ],
      },
    ],
    isLoading: false,
  }),
  useCreateLiveSubscription: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteLiveSubscription: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUnsubscribeLiveSubscription: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useLiveSubStorageEstimate: () => ({ data: null }),
}));

vi.mock("../stores/ui", () => ({
  useUIStore: () => vi.fn(),
}));

import { LiveSubscriptionsSection } from "./LiveSubscriptionsSection";

function renderIt() {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <LiveSubscriptionsSection />
    </QueryClientProvider>
  );
}

describe("LiveSubscriptionsSection", () => {
  it("renders subscription with asset_class and consumer list", () => {
    renderIt();
    expect(screen.getByText("SPY")).toBeInTheDocument();
    expect(screen.getByText("equities")).toBeInTheDocument();
    expect(screen.getByText(/manual/)).toBeInTheDocument();
    expect(screen.getByText(/algo: deployme/)).toBeInTheDocument();
  });

  it("shows green 'last tick' badge for fresh subscription", () => {
    renderIt();
    expect(screen.getByText(/last tick:/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 4: Type-check + run tests**

Run: `cd dashboard && npx tsc --noEmit`
Expected: clean.

Run: `cd dashboard && npx vitest run src/components/LiveSubscriptionsSection.test.tsx`
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/api/client.ts dashboard/src/components/LiveSubscriptionsSection.tsx dashboard/src/components/LiveSubscriptionsSection.test.tsx
git commit -m "feat(dashboard): surface consumers + asset_class + last-tick freshness"
```

---

## Task 13: Manual smoke test

**Files:** none.

- [ ] **Step 1: Build and restart**

```bash
cd dashboard && npm run build && cd ..
quilt coord restart
```

- [ ] **Step 2: Re-install the test algorithm**

The DB was reset in Task 2. Re-install simple-ma-crossover from the dashboard or via the CLI so the new `assets:` field gets persisted.

- [ ] **Step 3: Deploy + verify auto-subscribe**

1. Deploy simple-ma-crossover on an Alpaca paper account + your Pi worker.
2. Within a few seconds the `/data` page should show one new live subscription: `alpaca_live:SPY` (asset_class=equities), with the deployment as an `algo` consumer in the Consumers row.
3. During market hours, "last tick" should be green and update.

- [ ] **Step 4: Deploy a crypto algorithm**

Create or install an algorithm with `assets: [{broker: alpaca, symbol: BTCUSD, asset_class: crypto}]`. Deploy it. Verify on `/data` that an `alpaca_live:BTCUSD` (crypto) subscription appears and that bars start landing (Alpaca crypto runs 24/7).

- [ ] **Step 5: Stop the deployment**

Stop the algo. The subscription row should disappear from `/data` automatically (no remaining consumers).

- [ ] **Step 6: Manual subscribe + algo deploy on same symbol**

Manually subscribe to `alpaca:SPY`. Deploy simple-ma-crossover. The Consumers list should now show two rows. Stop the deployment — the subscription remains because the manual consumer still holds it. Click Unsubscribe — the row goes away.

- [ ] **Step 7: Force disconnect**

Briefly revoke an API key in your provider's dashboard. Within ~60s the activity stream should show a `stream_disconnect` event with severity=warn. Restore the key and confirm `stream_reconnect` appears.

- [ ] **Step 8: Check parquet for ghost bars**

```bash
python3 -c "
import pandas as pd
df = pd.read_parquet('data/market/alpaca_live/SPY/1min.parquet')
ghosts = df[(df['volume'] == 0) & (df['high'] == df['low'])]
print(f'ghost bars: {len(ghosts)}')
print(f'total bars: {len(df)}')
"
```
Expected: `ghost bars: 0` for any bars written after the deploy.

---

## Self-review

**Spec coverage check:**

| Spec requirement | Implemented in |
|---|---|
| Manifest format `assets:` list with `{broker, symbol, asset_class}` | Tasks 2, 11 |
| `Algorithm.data_dependencies` → `Algorithm.assets` rename | Task 2 |
| `subscription_consumers` table | Task 1 |
| `LiveSubscription.asset_class` column | Task 1 |
| Drop `dependent_count` column | Task 1 |
| Manual subscribe inserts a `manual` consumer | Task 3 |
| Manual unsubscribe deletes the manual consumer, auto-deletes sub on zero | Task 3 |
| DELETE refuses while consumers exist (with consumer summary) | Task 3 |
| List endpoint returns consumers array | Task 3 |
| Auto-subscribe on deploy start (algo consumer row inserted) | Task 4 |
| Auto-release on deploy stop, symmetric auto-delete on zero | Task 4 |
| Crypto routes to Alpaca CryptoDataStream | Task 6 |
| Tradier rejects crypto with clear error | Task 6 |
| `asset_class` plumbed from aggregator → adapter | Task 7 |
| One stream per `(broker, asset_class)` multiplexing N symbols | Task 8 |
| `MAX_SYMBOLS_PER_STREAM` cap with clear error at overflow | Task 8 |
| Ghost-bar filter (vol=0 AND high==low) | Task 5 |
| `stream_disconnect` / `stream_reconnect` worker_activity events | Task 9 |
| Lazy 5m/15m/1h/1d aggregation from 1m parquet | Task 10 |
| simple-ma-crossover manifest migrated | Task 11 |
| Frontend surfaces consumers list | Task 12 |
| Frontend "last tick at" freshness indicator | Task 12 |
| Frontend asset-class selector on Subscribe form | Task 12 |
| Tick retention default 168h | Task 1 (model default) + Task 3 (route default) |

**Known limitations carried into the implementation:**

1. **Disconnect detection is heuristic** (Task 9). The aggregator's stale-tick sweep fires `stream_disconnect` when no tick has arrived in 60s. During market hours for SPY this is fine; for thinly-traded assets it could false-fire. A proper integration is to have the broker stream handle invoke an `on_disconnect` callback directly when its TCP socket closes — this is a follow-up that should be added to the backlog if it bites.

2. **Multi-symbol packing fallback** (Task 8). When a broker stream handle doesn't expose `add_symbols` / `remove_symbols`, the code falls back to closing the existing stream and opening a new one with the full updated symbol set. This is correct but causes a brief data gap. Both `_AlpacaStreamHandle` and `_TradierStreamHandle` should expose `add_symbols` / `remove_symbols` (a follow-up to backlog).

3. **The migration drops local data** (Task 2 step 5). The user is sitting next to this and re-installs simple-ma-crossover after; if running in a different environment, design a smoother migration path.

**Out-of-scope follow-ups to add to `docs/superpowers/backlog.md`:**
- Per-stream `on_disconnect` callback wired directly into the broker handles (replaces the stale-tick heuristic).
- `add_symbols` / `remove_symbols` on `_AlpacaStreamHandle` and `_TradierStreamHandle` (avoid restart-from-scratch on multi-symbol updates).
- Live-data dependency on Algorithm.assets shape: validate at install time that each entry has the required keys.
