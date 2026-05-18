# Per-Account Live Subscriptions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tie each LiveSubscription to a specific Account so streams can spread across multiple broker accounts (sidestepping Alpaca's per-account free-tier connection cap), with the dashboard surfacing account + algorithm names as links.

**Architecture:** `LiveSubscription` gets a NOT-NULL `account_id` FK; natural key changes from `(broker, symbol)` to `(account_id, symbol)`. The aggregator opens one stream per `(account_id, asset_class)` using that account's own credentials. Manifests' `assets:` entries drop the `broker` field — the deployment's account decides routing. Subscription rows + algo consumers serialize their human-readable names + IDs for the UI.

**Tech Stack:** FastAPI + async SQLAlchemy + Alembic on the backend, React + react-query on the frontend, Alpaca + Tradier broker adapters.

**Spec:** `docs/superpowers/specs/2026-05-18-per-account-live-subscriptions-design.md` (commit `a88f5ff`).

**Deferred (already in `docs/superpowers/backlog.md`, NOT in this plan):**
- Per-stream `on_disconnect` callback wired into broker handles.
- `add_symbols`/`remove_symbols` on stream handles.
- Algorithm install fails opaquely when package dir is orphaned.
- Push updated `quilt.yaml` for simple-ma-crossover to upstream.
- Eager precompute / options chain bulk subscription / cross-account fail-over.
- Fix (A): surface stream auth errors on the subscription row (orthogonal).

---

## File Structure

**Backend — modified:**
- `coordinator/database/models.py` — `LiveSubscription.account_id` FK, drop unique on `(broker, symbol)`, add unique on `(account_id, symbol)`. Same file's `SubscriptionConsumer` stays as-is.
- `coordinator/api/routes/live_subscriptions.py` — request body takes `account_id` (required); responses include `account_id`, `account_name`, and per-`algo` consumer `algorithm_id` + `algorithm_name`.
- `coordinator/services/lifecycle.py` — `pre_start_checks` validates account.supported_asset_types covers the algorithm's asset_class needs, uses `instance.account_id` when creating subs.
- `coordinator/services/live_feed_aggregator.py` — `start_subscription` takes `account_id` and routes per-account; credential resolution reads the named account directly rather than via the `live_feed_account.<broker>` Setting.
- `sdk/manifest.py` — manifest parser still accepts `broker:` in asset entries but strips it (the field is ignored).
- `coordinator/api/routes/algorithms.py` — install endpoints validate each asset entry has `symbol` + `asset_class`. They no longer set `broker` on stored assets.

**Backend — new:**
- `coordinator/database/migrations/versions/*_per_account_live_subscriptions.py` — single Alembic revision.

**Frontend — modified:**
- `dashboard/src/api/client.ts` — `LiveSubscription` interface adds `account_id`, `account_name`. `SubscriptionConsumer` adds `algorithm_id` + `algorithm_name` (nullable for `manual`). `createLiveSubscription` body takes `account_id` instead of `broker`.
- `dashboard/src/components/LiveSubscriptionsSection.tsx` — Subscribe form has an Account selector instead of a Broker selector; rows render `<a href="/accounts/{id}">{name}</a>`; algo consumers link to `/algorithms/{id}` with `{algorithm_name}`.

**Tests — new + modified:**
- `tests/coordinator/test_live_subscriptions_routes.py` — extend with account_id-required, two-account-same-symbol, response-shape tests.
- `tests/coordinator/services/test_lifecycle_auto_subscribe.py` — extend with asset_class incompat fail, two-account-same-algo deployment.
- `tests/coordinator/services/test_live_feed_aggregator_packing.py` — two-account streams open independently.
- `tests/sdk/test_manifest.py` — manifest with broker field ignores it; manifest with `{symbol, asset_class}` parses.
- `dashboard/src/components/LiveSubscriptionsSection.test.tsx` — account link, algo name link.

---

## Task 1: Schema — add `account_id` to `LiveSubscription` + backfill + migration

**Files:**
- Modify: `coordinator/database/models.py`
- Create: `coordinator/database/migrations/versions/<hash>_per_account_live_subscriptions.py`
- Test: `tests/coordinator/test_per_account_live_subscriptions.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/test_per_account_live_subscriptions.py`:

```python
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from coordinator.database.models import Account, LiveSubscription


@pytest.mark.asyncio
async def test_live_subscription_has_account_id_fk(db_session):
    acct = Account(
        name="A", broker_type="alpaca", credentials="{}",
        supported_asset_types=["equities"],
    )
    db_session.add(acct)
    await db_session.flush()

    sub = LiveSubscription(
        account_id=acct.id, broker="alpaca", symbol="SPY",
        asset_class="equities", status="running",
    )
    db_session.add(sub)
    await db_session.commit()

    refetched = (await db_session.execute(
        select(LiveSubscription).where(LiveSubscription.id == sub.id)
    )).scalar_one()
    assert refetched.account_id == acct.id


@pytest.mark.asyncio
async def test_two_accounts_can_subscribe_to_same_symbol(db_session):
    a1 = Account(name="A1", broker_type="alpaca", credentials="{}",
                 supported_asset_types=["equities"])
    a2 = Account(name="A2", broker_type="alpaca", credentials="{}",
                 supported_asset_types=["equities"])
    db_session.add_all([a1, a2])
    await db_session.flush()
    db_session.add(LiveSubscription(
        account_id=a1.id, broker="alpaca", symbol="SPY",
        asset_class="equities", status="running",
    ))
    db_session.add(LiveSubscription(
        account_id=a2.id, broker="alpaca", symbol="SPY",
        asset_class="equities", status="running",
    ))
    await db_session.commit()
    rows = (await db_session.execute(select(LiveSubscription))).scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_duplicate_account_symbol_raises_integrity_error(db_session):
    acct = Account(name="A", broker_type="alpaca", credentials="{}",
                   supported_asset_types=["equities"])
    db_session.add(acct)
    await db_session.flush()
    db_session.add(LiveSubscription(
        account_id=acct.id, broker="alpaca", symbol="SPY",
        asset_class="equities", status="running",
    ))
    await db_session.flush()
    db_session.add(LiveSubscription(
        account_id=acct.id, broker="alpaca", symbol="SPY",
        asset_class="equities", status="running",
    ))
    with pytest.raises(IntegrityError):
        await db_session.commit()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/coordinator/test_per_account_live_subscriptions.py -v`
Expected: FAIL — `LiveSubscription` doesn't have `account_id` yet.

- [ ] **Step 3: Update the model**

In `coordinator/database/models.py`, find the `LiveSubscription` class. Replace its `__table_args__` and add the column:

```python
class LiveSubscription(Base):
    __tablename__ = "live_subscriptions"
    __table_args__ = (
        UniqueConstraint("account_id", "symbol", name="uq_live_subscription_account_symbol"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    account_id: Mapped[str] = mapped_column(
        String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False,
    )
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
    account: Mapped["Account"] = relationship()
```

(The old uniqueness was `("broker", "symbol")`; replaced with `("account_id", "symbol")`. `account` relationship added so the route handlers can `selectinload` it.)

- [ ] **Step 4: Write the Alembic migration**

Generate scaffold: `python3 -m alembic -c alembic.ini revision --autogenerate -m "per account live subscriptions"`

This creates `coordinator/database/migrations/versions/<hash>_per_account_live_subscriptions.py`. Replace its body with:

```python
"""per account live subscriptions

- Adds account_id column to live_subscriptions (FK to accounts, ON DELETE CASCADE).
- Backfills account_id from Setting(live_feed_account.<broker>) if set, else first
  account matching broker_type. Rows with no resolvable account are deleted.
- Drops the (broker, symbol) unique constraint; adds (account_id, symbol) unique.
- Deletes Setting rows with key LIKE 'live_feed_account.%'.
- Strips `broker` field from each entry in algorithms.assets JSON.
"""
from alembic import op
import sqlalchemy as sa
import json


# revision identifiers, set by alembic
revision = "<keep autogenerated>"
down_revision = "<keep autogenerated>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Add account_id column (nullable for backfill).
    with op.batch_alter_table("live_subscriptions") as batch_op:
        batch_op.add_column(sa.Column("account_id", sa.String(), nullable=True))

    # 2. Backfill account_id.
    rows = conn.execute(sa.text(
        "SELECT id, broker FROM live_subscriptions WHERE account_id IS NULL"
    )).fetchall()
    for sub_id, broker in rows:
        setting = conn.execute(sa.text(
            "SELECT value FROM settings WHERE key = :k"
        ), {"k": f"live_feed_account.{broker}"}).fetchone()
        account_id = setting[0] if setting else None
        if account_id is None:
            account_row = conn.execute(sa.text(
                "SELECT id FROM accounts WHERE broker_type = :b LIMIT 1"
            ), {"b": broker}).fetchone()
            account_id = account_row[0] if account_row else None
        if account_id is None:
            # No account → delete this subscription (and its consumers via FK cascade
            # on subscription_consumers).
            conn.execute(sa.text(
                "DELETE FROM live_subscriptions WHERE id = :i"
            ), {"i": sub_id})
        else:
            conn.execute(sa.text(
                "UPDATE live_subscriptions SET account_id = :a WHERE id = :i"
            ), {"a": account_id, "i": sub_id})

    # 3. Make account_id NOT NULL and add FK + unique constraint; drop the old.
    with op.batch_alter_table("live_subscriptions") as batch_op:
        batch_op.alter_column("account_id", nullable=False)
        batch_op.create_foreign_key(
            "fk_live_subscriptions_account_id",
            "accounts", ["account_id"], ["id"], ondelete="CASCADE",
        )
        batch_op.drop_constraint("uq_live_subscription_broker_symbol", type_="unique")
        batch_op.create_unique_constraint(
            "uq_live_subscription_account_symbol", ["account_id", "symbol"],
        )

    # 4. Delete obsolete settings.
    conn.execute(sa.text(
        "DELETE FROM settings WHERE key LIKE 'live_feed_account.%'"
    ))

    # 5. Strip `broker` field from algorithms.assets JSON.
    algo_rows = conn.execute(sa.text(
        "SELECT id, assets FROM algorithms WHERE assets IS NOT NULL"
    )).fetchall()
    for algo_id, assets_json in algo_rows:
        if not assets_json:
            continue
        try:
            assets = json.loads(assets_json) if isinstance(assets_json, str) else assets_json
        except (TypeError, ValueError):
            continue
        if not isinstance(assets, list):
            continue
        changed = False
        for a in assets:
            if isinstance(a, dict) and "broker" in a:
                del a["broker"]
                changed = True
        if changed:
            conn.execute(sa.text(
                "UPDATE algorithms SET assets = :a WHERE id = :i"
            ), {"a": json.dumps(assets), "i": algo_id})


def downgrade() -> None:
    raise NotImplementedError("downgrade not implemented")
```

- [ ] **Step 5: Apply the migration**

Run: `python3 -m alembic -c alembic.ini upgrade head`
Expected: clean apply.

Verify:
```bash
sqlite3 data/quilt_trader.db "PRAGMA table_info(live_subscriptions);" | grep account_id
sqlite3 data/quilt_trader.db "SELECT count(*) FROM settings WHERE key LIKE 'live_feed_account.%';"
```
Expected: one line with account_id, count = 0.

- [ ] **Step 6: Run the tests**

Run: `pytest tests/coordinator/test_per_account_live_subscriptions.py -v`
Expected: all 3 PASS.

- [ ] **Step 7: Commit**

```bash
git add coordinator/database/models.py coordinator/database/migrations/versions/*_per_account_live_subscriptions.py tests/coordinator/test_per_account_live_subscriptions.py
git commit -m "feat(coord): LiveSubscription.account_id FK + per-account unique key"
```

---

## Task 2: Routes — `account_id` in request body + `account_name` / `algorithm_name` in response

**Files:**
- Modify: `coordinator/api/routes/live_subscriptions.py`
- Test: `tests/coordinator/test_live_subscriptions_routes.py` (extend)

- [ ] **Step 1: Append failing tests**

Append to `tests/coordinator/test_live_subscriptions_routes.py`:

```python
@pytest.mark.asyncio
async def test_create_subscription_requires_account_id(client, db_session):
    from coordinator.database.models import Account
    acct = Account(name="Alpaca Test", broker_type="alpaca",
                   credentials="{}", supported_asset_types=["equities"])
    db_session.add(acct)
    await db_session.commit()

    body = {"account_id": acct.id, "symbol": "SPY",
            "asset_class": "equities", "tick_retention_hours": 168}
    r = await client.post("/api/live-subscriptions", json=body)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["account_id"] == acct.id
    assert data["account_name"] == "Alpaca Test"
    assert data["broker"] == "alpaca"


@pytest.mark.asyncio
async def test_response_includes_algorithm_name_on_algo_consumer(
    client, db_session,
):
    from coordinator.database.models import (
        Account, Algorithm, AlgorithmInstance, Worker,
        LiveSubscription, SubscriptionConsumer,
    )
    worker = Worker(name="W", status="online")
    acct = Account(name="A", broker_type="alpaca", credentials="{}",
                   supported_asset_types=["equities"])
    algo = Algorithm(repo_url="x", name="trend-bot")
    db_session.add_all([worker, acct, algo])
    await db_session.flush()
    inst = AlgorithmInstance(
        algorithm_id=algo.id, account_id=acct.id, worker_id=worker.id,
        status="running",
    )
    db_session.add(inst)
    await db_session.flush()
    sub = LiveSubscription(
        account_id=acct.id, broker="alpaca", symbol="SPY",
        asset_class="equities", status="running",
    )
    db_session.add(sub)
    await db_session.flush()
    db_session.add(SubscriptionConsumer(
        subscription_id=sub.id, consumer_type="algo", consumer_id=inst.id,
    ))
    await db_session.commit()

    r = await client.get("/api/live-subscriptions")
    rows = r.json()
    matching = next(row for row in rows if row["id"] == sub.id)
    algo_consumer = next(c for c in matching["consumers"] if c["consumer_type"] == "algo")
    assert algo_consumer["algorithm_id"] == algo.id
    assert algo_consumer["algorithm_name"] == "trend-bot"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/coordinator/test_live_subscriptions_routes.py::test_create_subscription_requires_account_id tests/coordinator/test_live_subscriptions_routes.py::test_response_includes_algorithm_name_on_algo_consumer -v`
Expected: FAIL — the route still takes `broker`, and the consumer dict has no `algorithm_name`.

- [ ] **Step 3: Update the routes**

In `coordinator/api/routes/live_subscriptions.py`:

(a) Replace the `SubscriptionCreate` class:

```python
class SubscriptionCreate(BaseModel):
    account_id: str
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
```

(b) Replace `_consumer_dict` to include algorithm name/id for algo consumers:

```python
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
```

(c) Replace `_to_response` to take the account + algo_index and include them:

```python
def _to_response(s: LiveSubscription, algo_index: dict[str, dict]) -> dict:
    return {
        "id": s.id,
        "account_id": s.account_id,
        "account_name": s.account.name if s.account else None,
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
```

(d) Add a helper that builds the deployment_id → {algorithm_id, algorithm_name} map by joining `algorithm_instances` + `algorithms`. Add this near the other helpers:

```python
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
```

(e) Update `list_subs`, `get_sub`, `create_sub`, `patch_sub`, `unsubscribe`, `delete_sub` to:
- Use `selectinload(LiveSubscription.consumers)` AND `selectinload(LiveSubscription.account)` (the latter needed for `account_name`).
- Build the algo_index by extracting algo consumer IDs and calling `_build_algo_index`.
- Pass the index into `_to_response`.

Concretely, `list_subs`:

```python
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
```

`create_sub` body now resolves the broker from the account:

```python
@router.post("", status_code=201)
async def create_sub(body: SubscriptionCreate, db: AsyncSession = Depends(get_db)):
    from sqlalchemy.orm import selectinload
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
    symbol_upper = body.symbol.upper()
    sub = LiveSubscription(
        account_id=account.id,
        broker=account.broker_type,
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
            detail=f"Subscription already exists for {account.name}/{body.symbol}",
        )
    db.add(SubscriptionConsumer(
        subscription_id=sub.id, consumer_type="manual", consumer_id=None,
    ))
    await db.flush()

    try:
        container = get_container()
    except AssertionError:
        container = None
    if container is not None:
        if container.live_feed_manager is not None:
            container.live_feed_manager.ensure_running(
                account.broker_type, symbol_upper, "manual"
            )
        if container.live_feed_aggregator is not None:
            await container.live_feed_aggregator.start_subscription(
                account.id, account.broker_type, symbol_upper, body.asset_class,
            )

    await db.refresh(sub, ["consumers", "account"])
    return _to_response(sub, await _build_algo_index(db, []))
```

Note: `start_subscription` now takes `account_id` as the first arg. Task 5 implements that change.

Add `from coordinator.database.models import Account` at the top of the file.

Update `get_sub`, `patch_sub`, `unsubscribe`, `delete_sub` to use the same pattern — `selectinload(LiveSubscription.account)` + `selectinload(LiveSubscription.consumers)`, then build the algo_index from this row's consumers. Concretely:

```python
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
```

Apply the same pattern to `patch_sub`, `unsubscribe`, and `delete_sub`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/coordinator/test_live_subscriptions_routes.py -v`
Expected: all PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add coordinator/api/routes/live_subscriptions.py tests/coordinator/test_live_subscriptions_routes.py
git commit -m "feat(coord): live-subscription routes take account_id; responses include account_name + algorithm_name"
```

---

## Task 3: Lifecycle — use `instance.account_id`, validate asset_class compat

**Files:**
- Modify: `coordinator/services/lifecycle.py`
- Test: `tests/coordinator/services/test_lifecycle_auto_subscribe.py` (extend)

- [ ] **Step 1: Append failing tests**

Append to `tests/coordinator/services/test_lifecycle_auto_subscribe.py`:

```python
@pytest.mark.asyncio
async def test_pre_start_creates_subscription_under_deployment_account():
    """The subscription gets account_id = instance.account_id (not a setting lookup)."""
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
            assets=[{"symbol": "SPY", "asset_class": "equities"}],
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

    async with sf() as session:
        sub = (await session.execute(select(LiveSubscription))).scalar_one()
        assert sub.account_id == acct.id
        assert sub.broker == "alpaca"

    await engine.dispose()


@pytest.mark.asyncio
async def test_pre_start_raises_when_account_does_not_support_asset_class():
    """Algorithm needs crypto, account only supports equities → fail at pre_start."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(engine)

    async with sf() as session:
        worker = Worker(name="W", status="online")
        acct = Account(name="A", broker_type="alpaca", credentials="{}",
                       supported_asset_types=["equities"])  # no crypto
        algo = Algorithm(
            repo_url="x", name="crypto-only",
            assets=[{"symbol": "BTCUSD", "asset_class": "crypto"}],
            required_asset_types=["crypto"],
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
        from coordinator.services.lifecycle import CompatibilityError
        with pytest.raises(CompatibilityError):
            await service.pre_start_checks(acct, algo, inst)

    await engine.dispose()


@pytest.mark.asyncio
async def test_same_algo_on_two_accounts_creates_two_subscriptions():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(engine)

    async with sf() as session:
        worker = Worker(name="W", status="online")
        a1 = Account(name="A1", broker_type="alpaca", credentials="{}",
                     supported_asset_types=["equities"])
        a2 = Account(name="A2", broker_type="alpaca", credentials="{}",
                     supported_asset_types=["equities"])
        algo = Algorithm(
            repo_url="x", name="dual",
            assets=[{"symbol": "SPY", "asset_class": "equities"}],
        )
        session.add_all([worker, a1, a2, algo])
        await session.flush()
        i1 = AlgorithmInstance(algorithm_id=algo.id, account_id=a1.id,
                               worker_id=worker.id, status="stopped")
        i2 = AlgorithmInstance(algorithm_id=algo.id, account_id=a2.id,
                               worker_id=worker.id, status="stopped")
        session.add_all([i1, i2])
        await session.commit()

    service = LifecycleService(
        scraper_manager=ScraperManager(),
        live_feed_manager=LiveFeedManager(),
        session_factory=sf,
    )
    async with sf() as session:
        algo = (await session.execute(select(Algorithm))).scalar_one()
        instances = (await session.execute(select(AlgorithmInstance))).scalars().all()
        for inst in instances:
            acct = (await session.execute(
                select(Account).where(Account.id == inst.account_id)
            )).scalar_one()
            await service.pre_start_checks(acct, algo, inst)

    async with sf() as session:
        subs = (await session.execute(select(LiveSubscription))).scalars().all()
        assert len(subs) == 2
        assert {s.account_id for s in subs} == {instances[0].account_id, instances[1].account_id}

    await engine.dispose()
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/coordinator/services/test_lifecycle_auto_subscribe.py -v`
Expected: the 3 new tests FAIL (lifecycle doesn't use instance.account_id or check asset compat).

- [ ] **Step 3: Update lifecycle**

In `coordinator/services/lifecycle.py`, replace `pre_start_checks` body (keep signature):

```python
async def pre_start_checks(self, account: Any, algorithm: Any, instance: Any) -> None:
    if account.locked_by is not None and account.locked_by != instance.id:
        raise CompatibilityError(f"Account is locked by instance {account.locked_by}")
    result = _check_compatibility(
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

    # Asset-class compatibility: every declared asset class must be in the
    # account's supported_asset_types.
    supported = set(account.supported_asset_types or [])
    declared = {a["asset_class"] for a in assets}
    missing = declared - supported
    if missing:
        raise CompatibilityError(
            f"Account does not support asset class(es): {', '.join(sorted(missing))}"
        )

    from coordinator.database.models import LiveSubscription, SubscriptionConsumer
    from sqlalchemy.orm import selectinload

    async with self._session_factory() as session:
        for asset in assets:
            symbol = asset["symbol"]
            asset_class = asset["asset_class"]
            sub = (await session.execute(
                select(LiveSubscription)
                .where(
                    LiveSubscription.account_id == account.id,
                    LiveSubscription.symbol == symbol,
                )
                .options(selectinload(LiveSubscription.consumers))
            )).scalar_one_or_none()
            if sub is None:
                sub = LiveSubscription(
                    account_id=account.id,
                    broker=account.broker_type,
                    symbol=symbol,
                    asset_class=asset_class,
                    status="running",
                )
                session.add(sub)
                await session.flush()
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
            self._live_feed_manager.ensure_running(
                account.broker_type, symbol, instance.id,
            )
        await session.commit()

    if self._live_feed_aggregator is not None:
        for asset in assets:
            try:
                await self._live_feed_aggregator.start_subscription(
                    account.id, account.broker_type,
                    asset["symbol"], asset["asset_class"],
                )
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "Failed to start_subscription for %s/%s",
                    account.broker_type, asset["symbol"],
                )
```

And update `post_stop_actions` to look up by account_id:

```python
async def post_stop_actions(self, account, algorithm, instance) -> None:
    assets = _parse_assets(algorithm.assets)
    if not assets or self._live_feed_manager is None or self._session_factory is None:
        return

    from coordinator.database.models import LiveSubscription, SubscriptionConsumer
    from sqlalchemy.orm import selectinload

    orphaned: list[tuple[str, str]] = []
    async with self._session_factory() as session:
        for asset in assets:
            symbol = asset["symbol"]
            self._live_feed_manager.release(account.broker_type, symbol, instance.id)

            sub = (await session.execute(
                select(LiveSubscription)
                .where(
                    LiveSubscription.account_id == account.id,
                    LiveSubscription.symbol == symbol,
                )
                .options(selectinload(LiveSubscription.consumers))
            )).scalar_one_or_none()
            if sub is None:
                continue
            for c in list(sub.consumers):
                if c.consumer_type == "algo" and c.consumer_id == instance.id:
                    await session.delete(c)
            await session.flush()
            await session.refresh(sub, ["consumers"])
            if not sub.consumers:
                await session.delete(sub)
                orphaned.append((account.id, symbol))
        await session.commit()

    if self._live_feed_aggregator is not None:
        for account_id, symbol in orphaned:
            try:
                await self._live_feed_aggregator.stop_subscription(
                    account_id, symbol,
                )
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "Failed to stop_subscription for %s/%s", account_id, symbol,
                )
```

Update `_parse_assets` to not require `broker` (it just reads `symbol` + `asset_class`):

```python
def _parse_assets(assets: Any) -> list[dict]:
    """Return list of {symbol, asset_class} dicts."""
    out: list[dict] = []
    if not assets:
        return out
    for a in assets:
        if not isinstance(a, dict):
            continue
        symbol = a.get("symbol")
        asset_class = a.get("asset_class", "equities")
        if symbol:
            out.append({"symbol": symbol, "asset_class": asset_class})
    return out
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/coordinator/services/test_lifecycle_auto_subscribe.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/lifecycle.py tests/coordinator/services/test_lifecycle_auto_subscribe.py
git commit -m "feat(coord): pre_start_checks uses instance.account_id + validates asset_class"
```

---

## Task 4: Manifest parser — make `broker` optional / ignored

**Files:**
- Modify: `sdk/manifest.py`
- Test: `tests/sdk/test_manifest.py` (extend)

- [ ] **Step 1: Append failing test**

Append to `tests/sdk/test_manifest.py`:

```python
def test_manifest_assets_without_broker_field():
    """Asset entries no longer need `broker:` — only symbol + asset_class."""
    from sdk.manifest import QuiltManifest
    yaml_text = """
name: test
type: algorithm
version: 1.0.0
entry_point: a.py
class_name: A
requirements:
  asset_types: [equities, crypto]
assets:
  - symbol: SPY
    asset_class: equities
  - symbol: BTCUSD
    asset_class: crypto
"""
    m = QuiltManifest.from_string(yaml_text)
    assert len(m.assets) == 2
    assert m.assets[0] == {"symbol": "SPY", "asset_class": "equities"}
    assert m.assets[1] == {"symbol": "BTCUSD", "asset_class": "crypto"}


def test_manifest_assets_strips_broker_field_for_backwards_compat():
    """If an old manifest still has `broker:` in an asset entry, it's parsed
    but the broker field is stripped (the deployment's account decides routing)."""
    from sdk.manifest import QuiltManifest
    yaml_text = """
name: test
type: algorithm
version: 1.0.0
entry_point: a.py
class_name: A
requirements:
  asset_types: [equities]
assets:
  - broker: alpaca
    symbol: SPY
    asset_class: equities
"""
    m = QuiltManifest.from_string(yaml_text)
    assert m.assets == [{"symbol": "SPY", "asset_class": "equities"}]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/sdk/test_manifest.py::test_manifest_assets_without_broker_field tests/sdk/test_manifest.py::test_manifest_assets_strips_broker_field_for_backwards_compat -v`
Expected: second test FAILS (current parser keeps `broker:`).

- [ ] **Step 3: Update the parser**

In `sdk/manifest.py`, replace the `raw_assets` parsing block (the part that builds the `assets` list) with one that strips `broker`:

```python
        raw_assets = data.get("assets") or []
        assets: list[dict] = []
        if isinstance(raw_assets, list):
            for a in raw_assets:
                if not isinstance(a, dict):
                    continue
                symbol = a.get("symbol")
                if not symbol:
                    continue
                assets.append({
                    "symbol": symbol,
                    "asset_class": a.get("asset_class", "equities"),
                })
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/sdk/test_manifest.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add sdk/manifest.py tests/sdk/test_manifest.py
git commit -m "feat(sdk): manifest strips broker field from asset entries"
```

---

## Task 5: Aggregator — stream key becomes `(account_id, asset_class)`

**Files:**
- Modify: `coordinator/services/live_feed_aggregator.py`
- Test: `tests/coordinator/services/test_live_feed_aggregator_packing.py` (extend)

- [ ] **Step 1: Append failing test**

Append to `tests/coordinator/services/test_live_feed_aggregator_packing.py`:

```python
@pytest.mark.asyncio
async def test_two_accounts_open_separate_streams_same_broker_asset_class(
    tmp_path, monkeypatch,
):
    """Two Alpaca accounts subscribing to the same asset_class open two
    independent WS connections (so each account's free-tier slot is its own)."""
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
        def __init__(self, api_key):
            self.api_key = api_key
        def start_market_data_stream(self, symbols, on_trade, on_quote, asset_class="equities"):
            h = FakeHandle()
            h.add_symbols(symbols)
            return h
        def close(self): pass

    seen_api_keys = []

    async def fake_adapter_for_account(account_id):
        seen_api_keys.append(account_id)
        return FakeAdapter(api_key=account_id)

    agg = LiveFeedAggregator(session_factory=None, encryption=None)
    monkeypatch.setattr(agg, "_adapter_for_account", fake_adapter_for_account)
    monkeypatch.setattr(agg, "_ticks_dir",
                        lambda b, s: tmp_path / b / s / "ticks")
    monkeypatch.setattr(agg, "_mark_subscription_error",
                        lambda *a, **kw: asyncio.sleep(0))

    await agg.start_subscription("acct-1", "alpaca", "SPY", "equities")
    await agg.start_subscription("acct-2", "alpaca", "SPY", "equities")
    assert len(handles_opened) == 2
    assert seen_api_keys == ["acct-1", "acct-2"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/coordinator/services/test_live_feed_aggregator_packing.py::test_two_accounts_open_separate_streams_same_broker_asset_class -v`
Expected: FAIL — `start_subscription` doesn't take account_id; stream is keyed on broker.

- [ ] **Step 3: Refactor the aggregator**

Open `coordinator/services/live_feed_aggregator.py`.

(a) Change the stream-key tuple from `(broker, asset_class)` to `(account_id, asset_class)`. Find every place `self._streams` is used and update.

(b) Replace `_adapter_for_broker(broker)` with `_adapter_for_account(account_id)`:

```python
async def _adapter_for_account(self, account_id: str) -> Optional[object]:
    """Construct a broker adapter using credentials from a specific account."""
    if self._sf is None:
        return None
    from coordinator.database.models import Account
    async with self._sf() as session:
        account = (await session.execute(
            select(Account).where(Account.id == account_id)
        )).scalar_one_or_none()
        if account is None:
            return None
        creds = self._decrypt_creds(account)
    try:
        return self._adapter_factory(account.broker_type, account.environment, creds)
    except Exception:
        logger.exception("Failed to construct adapter for account %s", account_id)
        return None
```

(c) Update `start_subscription` signature and body to take `account_id`:

```python
async def start_subscription(
    self, account_id: str, broker: str, symbol: str, asset_class: str = "equities",
) -> None:
    """Ensure a stream exists for (account_id, asset_class), then add the
    symbol. Per-symbol _SubState is keyed on (broker, symbol) since ticks
    come back labeled by symbol regardless of which account's connection
    they arrived on."""
    stream_key = (account_id, asset_class)
    state_key = (broker, symbol)

    if state_key not in self._states:
        self._states[state_key] = _SubState()

    conn = self._streams.get(stream_key)
    if conn is None:
        adapter = await self._adapter_for_account(account_id)
        if adapter is None:
            logger.warning(
                "No adapter for account %s; aggregator idles for %s/%s",
                account_id, broker, symbol,
            )
            if self._sf is not None:
                await self._mark_subscription_error(
                    account_id, symbol, f"No adapter for account {account_id}",
                )
        else:
            conn = _StreamConn()
            try:
                conn.handle = adapter.start_market_data_stream(
                    [symbol], self._make_on_trade(broker),
                    self._make_on_quote(broker), asset_class=asset_class,
                )
                conn.symbols.add(symbol)
                self._streams[stream_key] = conn
            except NotImplementedError:
                logger.warning(
                    "Adapter for %s does not implement start_market_data_stream",
                    broker,
                )
            except Exception:
                logger.exception("Failed to start stream for %s/%s/%s",
                                 account_id, broker, symbol)
    else:
        # Reuse existing stream — same multi-symbol packing logic as today.
        cap = _MAX_SYMBOLS_PER_STREAM.get((broker, asset_class), 30)
        if len(conn.symbols) >= cap:
            raise RuntimeError(
                f"broker stream cap reached for {broker}/{asset_class}: {cap} symbols"
            )
        if symbol not in conn.symbols:
            add = getattr(conn.handle, "add_symbols", None)
            if callable(add):
                add([symbol])
                conn.symbols.add(symbol)
            elif conn.handle is not None:
                old_handle = conn.handle
                adapter = await self._adapter_for_account(account_id)
                new_handle = None
                try:
                    new_handle = adapter.start_market_data_stream(
                        list(conn.symbols | {symbol}),
                        self._make_on_trade(broker),
                        self._make_on_quote(broker),
                        asset_class=asset_class,
                    )
                except Exception:
                    logger.exception("Failed to restart stream for %s/%s", broker, symbol)
                    return
                conn.handle = new_handle
                conn.symbols.add(symbol)
                try:
                    old_handle.close()
                except Exception:
                    pass

    # Per-symbol flush task as before.
    self._tasks[state_key] = asyncio.create_task(self._run(broker, symbol, asset_class))
```

(d) Update `stop_subscription` signature to take account_id:

```python
async def stop_subscription(self, account_id: str, symbol: str) -> None:
    """Remove this symbol from its account's stream subscribe set."""
    # Find broker via state_key scan (since state is keyed by (broker, symbol)).
    state_key = None
    for k in list(self._states.keys()):
        if k[1] == symbol:
            state_key = k
            break
    if state_key is None:
        return
    broker = state_key[0]

    t = self._tasks.pop(state_key, None)
    if t:
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    self._states.pop(state_key, None)

    for key in list(self._streams.keys()):
        conn = self._streams[key]
        if key[0] != account_id or symbol not in conn.symbols:
            continue
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

(e) Update the startup sweep in `start()` so it loads each `LiveSubscription` with its account_id and passes that to `start_subscription`:

```python
async def start(self) -> None:
    # ... existing code ...
    # Inside the DB sweep:
    async with self._sf() as session:
        rows = (await session.execute(select(LiveSubscription))).scalars().all()
    for r in rows:
        await self.start_subscription(r.account_id, r.broker, r.symbol, r.asset_class)
    # ... rest of existing start() body ...
```

(f) Update `_mark_subscription_error` to take account_id when looking up the row:

```python
async def _mark_subscription_error(
    self, account_id: str, symbol: str, message: str,
) -> None:
    if self._sf is None:
        return
    async with self._sf() as session:
        sub = (await session.execute(
            select(LiveSubscription).where(
                LiveSubscription.account_id == account_id,
                LiveSubscription.symbol == symbol,
            )
        )).scalar_one_or_none()
        if sub is not None:
            sub.status = "error"
            sub.last_error = message
            await session.commit()
```

(g) `_update_rate` similarly needs to be called with account_id. Find every call site, update to pass it.

- [ ] **Step 4: Run tests**

Run: `pytest tests/coordinator/services/test_live_feed_aggregator_packing.py tests/coordinator/services/test_live_feed_aggregator_routing.py tests/coordinator/services/test_live_feed_aggregator_disconnect_events.py tests/coordinator/services/test_live_feed_aggregator_ghost_bars.py -v`
Expected: all PASS. The other existing aggregator tests may need fixture updates (e.g., adding `account_id="acct-fake"` to `LiveSubscription(...)` in the fixtures). Update those fixtures minimally.

Run also: `pytest tests/coordinator/test_per_account_live_subscriptions.py tests/coordinator/test_live_subscriptions_routes.py tests/coordinator/services/test_lifecycle_auto_subscribe.py -v`
Expected: all PASS (prior tasks).

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/live_feed_aggregator.py tests/coordinator/services/test_live_feed_aggregator_packing.py tests/coordinator/services/test_live_feed_aggregator.py
git commit -m "feat(coord): aggregator stream key is (account_id, asset_class); creds from account"
```

---

## Task 6: Validate `Algorithm.assets` shape at install time

**Files:**
- Modify: `coordinator/api/routes/algorithms.py`
- Test: `tests/coordinator/test_algorithms_install.py` (create or extend)

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/test_algorithms_install.py` (or append if it exists):

```python
import pytest


def test_validate_assets_rejects_entry_without_symbol():
    from coordinator.api.routes.algorithms import _validate_assets
    with pytest.raises(ValueError, match="missing 'symbol'"):
        _validate_assets([{"asset_class": "equities"}])


def test_validate_assets_rejects_entry_with_unknown_asset_class():
    from coordinator.api.routes.algorithms import _validate_assets
    with pytest.raises(ValueError, match="invalid asset_class"):
        _validate_assets([{"symbol": "SPY", "asset_class": "forex"}])


def test_validate_assets_defaults_missing_asset_class_to_equities():
    """asset_class is optional with a default."""
    from coordinator.api.routes.algorithms import _validate_assets
    out = _validate_assets([{"symbol": "SPY"}])
    assert out == [{"symbol": "SPY", "asset_class": "equities"}]


def test_validate_assets_passes_well_formed_entries():
    from coordinator.api.routes.algorithms import _validate_assets
    out = _validate_assets([
        {"symbol": "SPY", "asset_class": "equities"},
        {"symbol": "BTCUSD", "asset_class": "crypto"},
    ])
    assert out == [
        {"symbol": "SPY", "asset_class": "equities"},
        {"symbol": "BTCUSD", "asset_class": "crypto"},
    ]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/coordinator/test_algorithms_install.py -v`
Expected: FAIL — `_validate_assets` doesn't exist.

- [ ] **Step 3: Add the validator + wire into install paths**

In `coordinator/api/routes/algorithms.py`, add a module-level helper:

```python
_VALID_ASSET_CLASSES = {"equities", "crypto", "options"}


def _validate_assets(raw: list) -> list[dict]:
    """Validate and normalize an `assets` list from a manifest. Each entry
    must have `symbol`. `asset_class` defaults to 'equities' and must be
    one of equities/crypto/options. Raises ValueError on any bad entry."""
    out: list[dict] = []
    if not raw:
        return out
    for a in raw:
        if not isinstance(a, dict):
            raise ValueError(f"assets entry must be a dict, got {type(a).__name__}")
        symbol = a.get("symbol")
        if not symbol or not isinstance(symbol, str):
            raise ValueError(f"assets entry missing 'symbol' string: {a!r}")
        asset_class = a.get("asset_class", "equities")
        if asset_class not in _VALID_ASSET_CLASSES:
            raise ValueError(
                f"assets entry invalid asset_class {asset_class!r}; "
                f"must be one of {sorted(_VALID_ASSET_CLASSES)}"
            )
        out.append({"symbol": symbol, "asset_class": asset_class})
    return out
```

Now wire it into both install paths. In `install_from_url`, replace the existing assets-build block with:

```python
    try:
        raw_assets = list(manifest.assets) if manifest.assets else []
        if not raw_assets and manifest.requirements.data_dependencies:
            default_class = (manifest.requirements.asset_types or ["equities"])[0]
            raw_assets = [
                {"symbol": dep["symbol"], "asset_class": dep.get("asset_class") or default_class}
                for dep in manifest.requirements.data_dependencies
                if isinstance(dep, dict) and dep.get("symbol")
            ]
        assets_list = _validate_assets(raw_assets)
    except ValueError as e:
        shutil.rmtree(pkg_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=f"Invalid assets list: {e}")
```

And in the local-source install path (the second install endpoint), apply the same change with appropriate variable names (`mf` instead of `manifest`).

- [ ] **Step 4: Run tests**

Run: `pytest tests/coordinator/test_algorithms_install.py -v`
Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add coordinator/api/routes/algorithms.py tests/coordinator/test_algorithms_install.py
git commit -m "feat(coord): validate Algorithm.assets shape at install time"
```

---

## Task 7: Frontend — Account selector, account link, algorithm link

**Files:**
- Modify: `dashboard/src/api/client.ts`
- Modify: `dashboard/src/components/LiveSubscriptionsSection.tsx`
- Test: `dashboard/src/components/LiveSubscriptionsSection.test.tsx` (extend)

- [ ] **Step 1: Update the TypeScript types**

In `dashboard/src/api/client.ts`:

```typescript
export interface SubscriptionConsumer {
  id: string;
  consumer_type: "manual" | "algo";
  consumer_id: string | null;
  created_at: string | null;
  algorithm_id: string | null;
  algorithm_name: string | null;
}

export interface LiveSubscription {
  id: string;
  account_id: string;
  account_name: string;
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

And update `createLiveSubscription`:

```typescript
createLiveSubscription(body: {
  account_id: string;
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

- [ ] **Step 2: Update the component**

In `dashboard/src/components/LiveSubscriptionsSection.tsx`:

Replace the broker selector + handleAdd to use an account selector. Imports gain `useAccounts` from `../api/hooks` and `Link` from `react-router-dom`:

```typescript
import { Link } from "react-router-dom";
import {
  useLiveSubscriptions,
  useCreateLiveSubscription,
  useUnsubscribeLiveSubscription,
  useLiveSubStorageEstimate,
  useAccounts,
} from "../api/hooks";
```

Replace `broker` state + selector with `account_id`:

```typescript
  const { data: accounts = [] } = useAccounts();
  const [adding, setAdding] = useState(false);
  const [accountId, setAccountId] = useState<string>("");
  const [assetClass, setAssetClass] = useState<AssetClass>("equities");
  const [symbol, setSymbol] = useState("");
  const [retention, setRetention] = useState(168);

  const selectedAccount = accounts.find((a) => a.id === accountId) ?? null;
  const supportedAssetClasses = (selectedAccount?.supported_asset_types ?? []) as AssetClass[];

  async function handleAdd() {
    if (!symbol.trim() || !accountId) return;
    try {
      await create.mutateAsync({
        account_id: accountId,
        symbol: symbol.trim(),
        asset_class: assetClass,
        tick_retention_hours: retention,
      });
      addAlert({ message: `Subscribed to ${selectedAccount?.name} / ${symbol}.`, severity: "success" });
      setAdding(false);
      setSymbol("");
    } catch (e) {
      addAlert({ message: `Subscribe failed: ${(e as Error).message}`, severity: "error" });
    }
  }
```

In the row render, replace the broker label with an account link, and the consumer rendering with algorithm-name link:

```typescript
              <div className="flex items-center justify-between text-sm">
                <div className="flex items-center gap-3 flex-wrap min-w-0">
                  <Link
                    to={`/accounts/${s.account_id}`}
                    className="text-indigo-400 font-mono hover:underline"
                  >
                    {s.account_name}
                  </Link>
                  <span className="font-mono text-gray-200">{s.symbol}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded border bg-gray-800 text-gray-400 border-gray-700">
                    {s.asset_class}
                  </span>
                  {/* ... rest of badges (tick_rate, last_tick) unchanged ... */}
```

And the consumers row:

```typescript
              {s.consumers.length > 0 && (
                <div className="mt-1.5 text-xs text-gray-500">
                  Consumers:{" "}
                  {s.consumers.map((c, i) => (
                    <span key={c.id}>
                      {i > 0 && ", "}
                      {c.consumer_type === "manual" ? (
                        "manual"
                      ) : c.algorithm_id && c.algorithm_name ? (
                        <Link
                          to={`/algorithms/${c.algorithm_id}`}
                          className="text-indigo-400 hover:underline"
                        >
                          {c.algorithm_name}
                        </Link>
                      ) : (
                        `algo: ${c.consumer_id?.slice(0, 8) ?? "?"}`
                      )}
                    </span>
                  ))}
                </div>
              )}
```

In the Subscribe form, replace the Broker selector with an Account selector:

```typescript
          <label className="flex flex-col gap-1 text-xs text-gray-400">
            <span>Account</span>
            <select
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
            >
              <option value="">Pick account…</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} ({a.broker_type})
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-gray-400">
            <span>Asset class</span>
            <select
              value={assetClass}
              onChange={(e) => setAssetClass(e.target.value as AssetClass)}
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
              disabled={!selectedAccount}
            >
              {supportedAssetClasses.map((ac) => (
                <option key={ac} value={ac}>{ac}</option>
              ))}
            </select>
          </label>
```

Disable the Add button until `accountId` AND `symbol` are set.

- [ ] **Step 3: Update the test**

Replace `dashboard/src/components/LiveSubscriptionsSection.test.tsx` with:

```typescript
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("../api/hooks", () => ({
  useLiveSubscriptions: () => ({
    data: [
      {
        id: "sub-1",
        account_id: "acct-1",
        account_name: "Alpaca Test",
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
          { id: "c1", consumer_type: "manual", consumer_id: null,
            created_at: null, algorithm_id: null, algorithm_name: null },
          { id: "c2", consumer_type: "algo", consumer_id: "deployment-abc",
            created_at: null, algorithm_id: "algo-1", algorithm_name: "simple-ma-crossover" },
        ],
      },
    ],
    isLoading: false,
  }),
  useCreateLiveSubscription: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUnsubscribeLiveSubscription: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useLiveSubStorageEstimate: () => ({ data: null }),
  useAccounts: () => ({ data: [
    { id: "acct-1", name: "Alpaca Test", broker_type: "alpaca",
      supported_asset_types: ["equities", "crypto"] },
  ], isLoading: false }),
}));

vi.mock("../stores/ui", () => ({ useUIStore: () => vi.fn() }));

import { LiveSubscriptionsSection } from "./LiveSubscriptionsSection";

function renderIt() {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <LiveSubscriptionsSection />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("LiveSubscriptionsSection", () => {
  it("renders account name as link to /accounts/<id>", () => {
    renderIt();
    const link = screen.getByRole("link", { name: "Alpaca Test" });
    expect(link).toHaveAttribute("href", "/accounts/acct-1");
  });

  it("renders algo consumer as link to /algorithms/<id>", () => {
    renderIt();
    const link = screen.getByRole("link", { name: "simple-ma-crossover" });
    expect(link).toHaveAttribute("href", "/algorithms/algo-1");
  });

  it("shows symbol + asset_class badges", () => {
    renderIt();
    expect(screen.getByText("SPY")).toBeInTheDocument();
    expect(screen.getByText("equities")).toBeInTheDocument();
  });
});
```

- [ ] **Step 4: Type-check + run tests**

Run: `cd dashboard && npx tsc --noEmit`
Expected: clean.

Run: `cd dashboard && npx vitest run src/components/LiveSubscriptionsSection.test.tsx`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/api/client.ts dashboard/src/components/LiveSubscriptionsSection.tsx dashboard/src/components/LiveSubscriptionsSection.test.tsx
git commit -m "feat(dashboard): account link in subscription row; algo-name link in consumers"
```

---

## Task 8: Migrate `simple-ma-crossover` local manifest

**Files:**
- Modify: `data/packages/quilt-trader-test-algo/quilt.yaml`

- [ ] **Step 1: Update the manifest**

Open `data/packages/quilt-trader-test-algo/quilt.yaml`. Find the `assets:` block:

```yaml
assets:
  - broker: alpaca
    symbol: SPY
    asset_class: equities
```

Replace with:

```yaml
assets:
  - symbol: SPY
    asset_class: equities
```

- [ ] **Step 2: Verify it parses**

```bash
python3 -c "
import yaml
with open('data/packages/quilt-trader-test-algo/quilt.yaml') as f:
    m = yaml.safe_load(f)
assert m['assets'][0] == {'symbol': 'SPY', 'asset_class': 'equities'}
print('manifest OK')
"
```
Expected: `manifest OK`.

- [ ] **Step 3: Note (no git commit)**

`data/packages/` is gitignored, so this change won't commit. Add a note inline that the upstream `quilt.yaml` at https://github.com/ElectricJack/quilt-trader-test-algo also needs the same edit pushed (already in backlog as a follow-up).

---

## Task 9: Manual smoke test

**Files:** none.

- [ ] **Step 1: Build + restart**

```bash
cd dashboard && npm run build && cd ..
quilt coord restart
```

- [ ] **Step 2: Add a second Alpaca account**

In the dashboard, Accounts → Add Account. Use your second Alpaca paper account's credentials. Set `supported_asset_types = ["equities", "crypto"]`.

- [ ] **Step 3: Subscribe each account to a different asset class**

On `/data` → Live Subscriptions → Subscribe:
- Pick account #1, symbol SPY, asset_class equities → Add
- Pick account #2, symbol BTCUSD, asset_class crypto → Add

Within ~10s both subscriptions should show non-zero `tick_rate_per_min` and a green `last tick: <Ns ago>` badge.

- [ ] **Step 4: Verify rows display correctly**

Each row header should be `<Account Name>` (linked to /accounts/<id>), not `alpaca_live`.

- [ ] **Step 5: Deploy simple-ma-crossover and verify algorithm-name link**

Deploy simple-ma-crossover on account #1. The SPY subscription's consumer list should now show `simple-ma-crossover` (linked to `/algorithms/<id>`) alongside `manual`.

- [ ] **Step 6: Verify worker_activity has no `connection limit exceeded` errors**

```bash
sqlite3 data/quilt_trader.db "SELECT event_type, severity, payload FROM worker_activity WHERE timestamp > datetime('now', '-5 minutes') ORDER BY timestamp DESC LIMIT 10;"
```

Expected: no `connection limit exceeded` in payload of any `stream_disconnect` event.

---

## Self-review

**Spec coverage check:**

| Spec requirement | Implemented in |
|---|---|
| `LiveSubscription.account_id` FK NOT NULL, ON DELETE CASCADE | Task 1 |
| Unique key changes from `(broker, symbol)` to `(account_id, symbol)` | Task 1 |
| Algorithm manifests drop `broker:` from `assets:` entries (parser strips) | Task 4 |
| Lifecycle uses `instance.account_id` | Task 3 |
| Lifecycle validates account.supported_asset_types covers declared asset_class | Task 3 |
| Manual subscribe UI requires account selection | Task 7 |
| Aggregator opens one stream per `(account_id, asset_class)` | Task 5 |
| Credentials come from the named account, not Setting lookup | Task 5 |
| Response includes `account_id` + `account_name` | Task 2 |
| Response includes `algorithm_id` + `algorithm_name` on algo consumers | Task 2 |
| Frontend row label is account name (linked) | Task 7 |
| Frontend algo consumer is name (linked) | Task 7 |
| Algorithm.assets validation at install time | Task 6 |
| Migration: account_id backfilled, obsolete settings dropped, broker stripped from JSON | Task 1 |
| simple-ma-crossover manifest updated | Task 8 |

**Out-of-scope follow-ups (already in `docs/superpowers/backlog.md`):**
- Per-stream on_disconnect callback (still pending).
- add_symbols/remove_symbols on stream handles.
- Cross-account fail-over.
- Surface stream auth errors on subscription row (fix A).
