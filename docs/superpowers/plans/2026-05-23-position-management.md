# Position Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a holistic position tracking system that supports multi-leg closes, partial closes, limit/stop orders, and algorithm coordination — replacing the current single-leg market-only close.

**Architecture:** Five sub-projects layered bottom-up. Sub-project 5 (holistic position model) adds DB columns and a reconciliation service that becomes the canonical position source. Sub-projects 1-3 extend the close endpoint (`POST /api/accounts/{account_id}/positions/{position_id}/close`) with multi-leg, partial, and limit/stop support. Sub-project 4 wires coord-to-worker WebSocket notifications so running algorithms learn about manual closes. Each sub-project adds backend logic, tests, and where relevant, frontend changes.

**Tech Stack:** FastAPI + SQLAlchemy (async) on the backend; React + react-query + Tailwind on the frontend. Tests use pytest + httpx `AsyncClient` (see `tests/coordinator/conftest.py`). Alembic for migrations. Worker communicates with coordinator via JSON-over-WebSocket (see `coordinator/api/websocket.py` and `worker/agent.py`).

**Spec:** Lifts deferred items from `docs/superpowers/backlog.md` (Positions section).

**Existing code touchpoints:**
- Close endpoint: `coordinator/api/routes/accounts.py:1020-1102` (single-leg market close)
- Open endpoint: `coordinator/api/routes/accounts.py:765-1018` (multileg via `submit_multileg_order`)
- Position model: `coordinator/database/models.py:310-329`
- TradeLog model: `coordinator/database/models.py:189-211`
- BrokerAdapter ABC: `worker/broker_adapter.py:95-167`
- Tradier multileg: `worker/tradier_adapter.py:193-235` (hardcodes `buy_to_open`/`sell_to_open`)
- Alpaca multileg: `worker/alpaca_adapter.py:239-268` (hardcodes `BUY_TO_OPEN`/`SELL_TO_OPEN`)
- Worker agent: `worker/agent.py` (WebSocket message router)
- Coordinator WS handlers: `coordinator/api/websocket.py:244-535`
- Algorithm SDK: `sdk/algorithm.py` (`QuiltAlgorithm` base class)
- Runner: `worker/runner.py` (`AlgorithmRunner` wraps SDK)
- Tick loop: `worker/tick_loop.py:66-177` (submits orders per signal)
- Close tests: `tests/coordinator/test_accounts_positions_close.py`
- Frontend close flow: `dashboard/src/pages/AccountDetail.tsx:149-152, 254-264, 442-458, 762-777`
- Frontend API: `dashboard/src/api/client.ts:782-799`, `dashboard/src/api/hooks.ts:841-849`

---

## Sub-project 5: Holistic Position Tracking Model

*Done first because sub-projects 1-4 depend on the enriched Position schema.*

### Task 1: Add `owner_instance_id` and `remaining_quantity` columns to Position model

**Files:**
- Modify: `coordinator/database/models.py`
- Create: `coordinator/database/migrations/versions/xxxx_position_management_columns.py`
- Test: `tests/coordinator/test_position_management_model.py`

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/test_position_management_model.py`:

```python
import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_position_has_new_columns(db_session):
    """Position model must have owner_instance_id, remaining_quantity, and cost_basis_lots."""
    from coordinator.database.models import Position

    pos = Position(
        account_id="acct-1",
        strategy_type="vertical_spread",
        legs=[
            {"symbol": "SPY260620C00560000", "asset_type": "options",
             "side": "buy", "quantity": 2, "avg_price": 5.00},
            {"symbol": "SPY260620C00570000", "asset_type": "options",
             "side": "sell", "quantity": 2, "avg_price": 3.00},
        ],
        status="open",
        net_cost=4.00,
        remaining_quantity=2,
        cost_basis_lots=[
            {"fill_price": 4.00, "quantity": 2, "timestamp": "2026-05-23T10:00:00Z"}
        ],
    )
    db_session.add(pos)
    await db_session.flush()

    result = (await db_session.execute(
        select(Position).where(Position.id == pos.id)
    )).scalar_one()
    assert result.remaining_quantity == 2
    assert result.cost_basis_lots[0]["fill_price"] == 4.00
    assert result.status == "open"
```

Run: `pytest tests/coordinator/test_position_management_model.py -v`
Expected: FAIL (columns don't exist yet).

- [ ] **Step 2: Add columns to Position model**

Edit `coordinator/database/models.py` — add after the existing `metadata_` column on Position (line ~328):

```python
class Position(Base):
    # ... existing columns ...
    remaining_quantity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cost_basis_lots: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
```

`remaining_quantity` tracks how many contracts/shares remain open (decremented on partial close). `cost_basis_lots` is a JSON array of `{fill_price, quantity, timestamp}` for FIFO cost basis.

- [ ] **Step 3: Create Alembic migration**

Run:
```bash
cd /home/jkern/dev/quilt-trader && alembic -c coordinator/database/alembic.ini revision --autogenerate -m "position management columns"
```

Verify the generated migration adds `remaining_quantity` (Float, nullable) and `cost_basis_lots` (JSON, nullable) to the `positions` table.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/coordinator/test_position_management_model.py -v`
Expected: PASS.

---

### Task 2: Position reconciliation service — compare broker vs DB

**Files:**
- Create: `coordinator/services/position_reconciler.py`
- Test: `tests/coordinator/services/test_position_reconciler.py`

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/services/test_position_reconciler.py`:

```python
import pytest
from coordinator.services.position_reconciler import PositionReconciler


def test_reconcile_detects_orphaned_broker_position():
    """A position in the broker but not in the DB is flagged as 'untracked'."""
    broker_positions = {
        "SPY": {"symbol": "SPY", "quantity": 10, "side": "long",
                "avg_entry_price": 520.0, "current_price": 525.0},
    }
    db_positions = []  # no DB records

    result = PositionReconciler.reconcile(broker_positions, db_positions)
    assert len(result.untracked) == 1
    assert result.untracked[0]["symbol"] == "SPY"
    assert result.matched == []
    assert result.stale == []


def test_reconcile_detects_stale_db_position():
    """A position in the DB but absent from broker is flagged as 'stale'."""
    broker_positions = {}
    db_positions = [
        {"id": "pos-1", "legs": [{"symbol": "AAPL", "side": "buy", "quantity": 5}],
         "status": "open", "account_id": "acct-1"},
    ]

    result = PositionReconciler.reconcile(broker_positions, db_positions)
    assert len(result.stale) == 1
    assert result.stale[0]["id"] == "pos-1"
    assert result.untracked == []


def test_reconcile_matches_known_position():
    """A position present in both broker and DB is flagged as 'matched'."""
    broker_positions = {
        "SPY": {"symbol": "SPY", "quantity": 10, "side": "long",
                "avg_entry_price": 520.0, "current_price": 525.0},
    }
    db_positions = [
        {"id": "pos-1", "legs": [{"symbol": "SPY", "side": "buy", "quantity": 10}],
         "status": "open", "account_id": "acct-1"},
    ]

    result = PositionReconciler.reconcile(broker_positions, db_positions)
    assert len(result.matched) == 1
    assert result.matched[0]["db_id"] == "pos-1"
    assert result.matched[0]["broker_symbol"] == "SPY"


def test_reconcile_detects_quantity_mismatch():
    """When DB and broker agree on symbol but differ on quantity, flag as 'mismatched'."""
    broker_positions = {
        "SPY": {"symbol": "SPY", "quantity": 10, "side": "long",
                "avg_entry_price": 520.0, "current_price": 525.0},
    }
    db_positions = [
        {"id": "pos-1", "legs": [{"symbol": "SPY", "side": "buy", "quantity": 5}],
         "status": "open", "account_id": "acct-1"},
    ]

    result = PositionReconciler.reconcile(broker_positions, db_positions)
    assert len(result.mismatched) == 1
    assert result.mismatched[0]["broker_qty"] == 10
    assert result.mismatched[0]["db_qty"] == 5
```

Run: `pytest tests/coordinator/services/test_position_reconciler.py -v`
Expected: FAIL (module doesn't exist).

- [ ] **Step 2: Implement PositionReconciler**

Create `coordinator/services/position_reconciler.py`:

```python
from dataclasses import dataclass, field


@dataclass
class ReconciliationResult:
    matched: list[dict] = field(default_factory=list)
    untracked: list[dict] = field(default_factory=list)
    stale: list[dict] = field(default_factory=list)
    mismatched: list[dict] = field(default_factory=list)


class PositionReconciler:
    """Compare broker positions against DB positions and classify discrepancies."""

    @staticmethod
    def reconcile(
        broker_positions: dict[str, dict],
        db_positions: list[dict],
    ) -> ReconciliationResult:
        result = ReconciliationResult()

        # Build a lookup: symbol -> list of DB position dicts
        db_by_symbol: dict[str, list[dict]] = {}
        for dbp in db_positions:
            for leg in dbp.get("legs", []):
                sym = leg.get("symbol")
                if sym:
                    db_by_symbol.setdefault(sym, []).append(dbp)

        seen_db_ids: set[str] = set()

        for sym, broker_pos in broker_positions.items():
            db_matches = db_by_symbol.get(sym, [])
            if not db_matches:
                result.untracked.append(broker_pos)
                continue

            dbp = db_matches[0]
            seen_db_ids.add(dbp["id"])
            # Sum DB leg quantities for this symbol
            db_qty = sum(
                leg.get("quantity", 0)
                for leg in dbp.get("legs", [])
                if leg.get("symbol") == sym
            )
            broker_qty = broker_pos.get("quantity", 0)

            if abs(db_qty - broker_qty) > 1e-9:
                result.mismatched.append({
                    "db_id": dbp["id"],
                    "broker_symbol": sym,
                    "db_qty": db_qty,
                    "broker_qty": broker_qty,
                })
            else:
                result.matched.append({
                    "db_id": dbp["id"],
                    "broker_symbol": sym,
                })

        # DB positions not seen in broker
        for dbp in db_positions:
            if dbp["id"] not in seen_db_ids:
                result.stale.append(dbp)

        return result
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/coordinator/services/test_position_reconciler.py -v`
Expected: all 4 PASS.

---

### Task 3: Wire reconciliation into account sync endpoint

**Files:**
- Modify: `coordinator/api/routes/accounts.py`
- Test: `tests/coordinator/test_position_management_model.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/coordinator/test_position_management_model.py`:

```python
@pytest.mark.asyncio
async def test_reconcile_endpoint_returns_comparison(client, db_session, monkeypatch):
    """GET /api/accounts/{id}/positions/reconcile compares broker vs DB."""
    from coordinator.api.routes import accounts as accounts_routes
    from coordinator.database.models import Account, Position

    account = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials="{}", supported_asset_types=["equities"], pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    pos = Position(
        account_id=account.id, strategy_type="single",
        legs=[{"symbol": "SPY", "side": "buy", "quantity": 10, "asset_type": "equities", "avg_price": 520.0}],
        status="open", net_cost=5200.0,
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()

    class FakeAdapter:
        def get_positions(self):
            return {
                "SPY": {"symbol": "SPY", "quantity": 10, "side": "long",
                        "avg_entry_price": 520.0, "current_price": 525.0},
                "AAPL": {"symbol": "AAPL", "quantity": 5, "side": "long",
                         "avg_entry_price": 180.0, "current_price": 185.0},
            }
        def close(self): pass

    async def fake_adapter(acct):
        return FakeAdapter()

    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter)

    r = await client.get(f"/api/accounts/{account.id}/positions/reconcile")
    assert r.status_code == 200
    data = r.json()
    assert len(data["matched"]) == 1
    assert len(data["untracked"]) == 1
    assert data["untracked"][0]["symbol"] == "AAPL"
```

- [ ] **Step 2: Implement the reconcile endpoint**

Add to `coordinator/api/routes/accounts.py` after the `close_position` handler:

```python
@router.get("/{account_id}/positions/reconcile")
async def reconcile_positions(
    account_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Compare broker positions against Quilt's internal position records."""
    from coordinator.services.position_reconciler import PositionReconciler

    account = (await db.execute(
        select(Account).where(Account.id == account_id)
    )).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    adapter = await _adapter_for_account(account)
    try:
        broker_positions = await asyncio.to_thread(adapter.get_positions)
    finally:
        _close_adapter(adapter)

    db_positions_rows = (await db.execute(
        select(Position).where(
            Position.account_id == account_id,
            Position.status == "open",
        )
    )).scalars().all()
    db_positions = [
        {"id": p.id, "legs": p.legs, "status": p.status, "account_id": p.account_id}
        for p in db_positions_rows
    ]

    result = PositionReconciler.reconcile(broker_positions, db_positions)
    return {
        "matched": result.matched,
        "untracked": result.untracked,
        "stale": result.stale,
        "mismatched": result.mismatched,
    }
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/coordinator/test_position_management_model.py::test_reconcile_endpoint_returns_comparison -v`
Expected: PASS.

---

## Sub-project 1: Multi-leg Position Close

### Task 4: Add `position_intent` parameter to `submit_multileg_order` and adapter close-side support

**Files:**
- Modify: `worker/broker_adapter.py`
- Modify: `worker/tradier_adapter.py`
- Modify: `worker/alpaca_adapter.py`
- Test: `tests/worker/test_broker_adapter.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/worker/test_broker_adapter.py`:

```python
def test_multileg_leg_spec_accepts_position_intent():
    """MultilegLegSpec must support position_intent='close' for closing legs."""
    from worker.broker_adapter import MultilegLegSpec

    leg = MultilegLegSpec(
        symbol="SPY", asset_type="options", side="sell", quantity=2,
        expiry="2026-06-20", strike=560.0, right="call",
        position_intent="close",
    )
    assert leg.position_intent == "close"


def test_multileg_leg_spec_defaults_intent_to_open():
    from worker.broker_adapter import MultilegLegSpec

    leg = MultilegLegSpec(
        symbol="SPY", asset_type="options", side="buy", quantity=1,
    )
    assert leg.position_intent == "open"
```

Run: `pytest tests/worker/test_broker_adapter.py::test_multileg_leg_spec_accepts_position_intent -v`
Expected: FAIL (no `position_intent` field).

- [ ] **Step 2: Add `position_intent` to MultilegLegSpec**

Edit `worker/broker_adapter.py`, add to the `MultilegLegSpec` dataclass:

```python
@dataclass
class MultilegLegSpec:
    """Input shape for one leg of a multi-leg order. Matches Spec A's API LegSpec."""
    symbol: str
    asset_type: str               # "equities" | "options" | "crypto"
    side: str                     # "buy" | "sell"
    quantity: float
    expiry: Optional[str] = None  # YYYY-MM-DD, options only
    strike: Optional[float] = None
    right: Optional[str] = None   # "call" | "put"
    position_intent: str = "open" # "open" | "close"
```

- [ ] **Step 3: Update Tradier adapter side mapping**

Edit `worker/tradier_adapter.py` in `submit_multileg_order` — replace the hardcoded side_map:

```python
        for i, leg in enumerate(legs):
            data[f"option_symbol[{i}]"] = self.compose_symbol(leg)
            intent = getattr(leg, "position_intent", "open")
            if leg.side == "buy":
                data[f"side[{i}]"] = "buy_to_close" if intent == "close" else "buy_to_open"
            else:
                data[f"side[{i}]"] = "sell_to_close" if intent == "close" else "sell_to_open"
            data[f"quantity[{i}]"] = str(int(leg.quantity))
```

- [ ] **Step 4: Update Alpaca adapter PositionIntent**

Edit `worker/alpaca_adapter.py` in `submit_multileg_order` — replace the hardcoded PositionIntent:

```python
            intent = getattr(leg, "position_intent", "open")
            req_legs.append(OptionLegRequest(
                symbol=self.compose_symbol(leg),
                side=OrderSide.BUY if leg.side == "buy" else OrderSide.SELL,
                ratio_qty=int(leg.quantity),
                position_intent=(
                    (PositionIntent.BUY_TO_CLOSE if leg.side == "buy" else PositionIntent.SELL_TO_CLOSE)
                    if intent == "close"
                    else (PositionIntent.BUY_TO_OPEN if leg.side == "buy" else PositionIntent.SELL_TO_OPEN)
                ),
            ))
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/worker/test_broker_adapter.py -v`
Expected: PASS.

---

### Task 5: New `POST /api/accounts/{account_id}/positions/{position_id}/close` endpoint for multi-leg close

**Files:**
- Modify: `coordinator/api/routes/accounts.py`
- Test: `tests/coordinator/test_position_multileg_close.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/test_position_multileg_close.py`:

```python
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from coordinator.database.models import Account, Position, TradeLog


@pytest.mark.asyncio
async def test_close_multileg_position_inverts_sides(client: AsyncClient, db_session, monkeypatch):
    """Closing a 2-leg spread must invert each leg's side and submit via submit_multileg_order."""
    from worker.broker_adapter import MultilegLegSpec, MultilegLegResult, MultilegOrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A", broker_type="tradier", environment="paper",
        credentials="{}", supported_asset_types=["options"], pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    pos = Position(
        account_id=account.id,
        strategy_type="vertical_spread",
        legs=[
            {"symbol": "SPY", "asset_type": "options", "side": "buy", "quantity": 2,
             "expiry": "2026-06-20", "strike": 560.0, "right": "call", "avg_price": 5.00},
            {"symbol": "SPY", "asset_type": "options", "side": "sell", "quantity": 2,
             "expiry": "2026-06-20", "strike": 570.0, "right": "call", "avg_price": 3.00},
        ],
        status="open",
        net_cost=4.00,
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()
    pos_id = pos.id

    captured_legs = []

    class FakeAdapter:
        def supports_multileg_orders(self, legs):
            return True

        def compose_symbol(self, leg):
            return f"{leg.symbol}_OCC"

        def submit_multileg_order(self, legs, order_type, limit_price):
            for leg in legs:
                captured_legs.append({
                    "side": leg.side,
                    "symbol": leg.symbol,
                    "quantity": leg.quantity,
                    "position_intent": leg.position_intent,
                })
            return MultilegOrderResult(
                broker_order_id="close-ord-1",
                legs=[
                    MultilegLegResult(index=0, status="filled", filled_price=5.50, fees=0.65),
                    MultilegLegResult(index=1, status="filled", filled_price=2.50, fees=0.65),
                ],
                atomic=True,
            )

        def close(self):
            pass

    async def fake_adapter(acct):
        return FakeAdapter()

    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter)

    r = await client.post(f"/api/accounts/{account.id}/positions/{pos_id}/close", json={})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["broker_order_id"] == "close-ord-1"

    # Verify sides were inverted: buy->sell, sell->buy
    assert captured_legs[0]["side"] == "sell"
    assert captured_legs[0]["position_intent"] == "close"
    assert captured_legs[1]["side"] == "buy"
    assert captured_legs[1]["position_intent"] == "close"

    # Verify DB position is marked closed
    db_session.expire_all()
    refreshed = (await db_session.execute(
        select(Position).where(Position.id == pos_id)
    )).scalar_one()
    assert refreshed.status == "closed"
    assert refreshed.closed_at is not None

    # Verify TradeLog rows written
    trades = (await db_session.execute(
        select(TradeLog).where(TradeLog.position_id == pos_id)
    )).scalars().all()
    assert len(trades) == 2
```

Run: `pytest tests/coordinator/test_position_multileg_close.py -v`
Expected: FAIL (404 — endpoint doesn't exist).

- [ ] **Step 2: Implement the position-id close endpoint**

Add to `coordinator/api/routes/accounts.py`:

```python
class ClosePositionByIdRequest(BaseModel):
    order_type: str = "market"
    limit_price: Optional[float] = None
    quantity: Optional[float] = None  # partial close — sub-project 2


@router.post("/{account_id}/positions/{position_id}/close")
async def close_position_by_id(
    account_id: str,
    position_id: str,
    body: ClosePositionByIdRequest,
    db: AsyncSession = Depends(get_db),
):
    """Close a position by its Quilt position ID. Supports multi-leg positions.

    Reads the position's legs, inverts each side, and submits via
    submit_multileg_order (atomic) or sequential single-leg fallback.
    Does NOT honor account locked_by — closes are a safety valve.
    """
    from worker.broker_adapter import MultilegLegSpec

    account = (await db.execute(
        select(Account).where(Account.id == account_id)
    )).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    position = (await db.execute(
        select(Position).where(Position.id == position_id, Position.account_id == account_id)
    )).scalar_one_or_none()
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    if position.status != "open":
        raise HTTPException(status_code=409, detail=f"Position is already {position.status}")

    legs = position.legs or []
    if not legs:
        raise HTTPException(status_code=422, detail="Position has no legs")

    # Invert each leg's side for the close order
    close_quantity = body.quantity  # None means full close
    close_legs = []
    for leg in legs:
        orig_side = leg.get("side", "buy")
        inverted_side = "sell" if orig_side == "buy" else "buy"
        qty = close_quantity if close_quantity is not None else leg.get("quantity", 0)
        close_legs.append(MultilegLegSpec(
            symbol=leg["symbol"],
            asset_type=leg.get("asset_type", "options"),
            side=inverted_side,
            quantity=qty,
            expiry=leg.get("expiry"),
            strike=leg.get("strike"),
            right=leg.get("right"),
            position_intent="close",
        ))

    adapter = await _adapter_for_account(account)
    try:
        if len(close_legs) > 1 and adapter.supports_multileg_orders(close_legs):
            def _submit():
                return adapter.submit_multileg_order(
                    close_legs,
                    order_type=body.order_type,
                    limit_price=body.limit_price,
                )
            try:
                result = await asyncio.to_thread(_submit)
            except Exception as e:
                raise HTTPException(status_code=422, detail=f"Broker rejected: {e}")

            # Update position
            now = datetime.now(timezone.utc)
            if close_quantity is None:
                position.status = "closed"
                position.closed_at = now
            else:
                position.status = "closing" if close_quantity < legs[0].get("quantity", 0) else "closed"
                if position.status == "closed":
                    position.closed_at = now

            # Net proceeds from filled legs
            position.net_proceeds = sum(
                (lr.filled_price or 0.0) * cl.quantity * (1 if cl.side == "sell" else -1)
                for cl, lr in zip(close_legs, result.legs)
            )
            position.total_fees = (position.total_fees or 0.0) + sum(
                lr.fees or 0.0 for lr in result.legs
            )

            # TradeLog rows
            for leg, leg_res in zip(close_legs, result.legs):
                db.add(TradeLog(
                    account_id=account_id,
                    position_id=position_id,
                    source="manual",
                    timestamp=now,
                    symbol=leg.symbol,
                    asset_type=leg.asset_type,
                    side=leg.side,
                    quantity=leg.quantity,
                    order_type=body.order_type,
                    filled_price=leg_res.filled_price or 0.0,
                    fees=leg_res.fees or 0.0,
                    broker_txn_id=leg_res.broker_order_id,
                ))
            await db.flush()
            await db.commit()

            return {
                "position_id": position_id,
                "broker_order_id": result.broker_order_id,
                "legs": [
                    {"index": r.index, "status": r.status,
                     "filled_price": r.filled_price, "fees": r.fees}
                    for r in result.legs
                ],
                "atomic": True,
            }
        else:
            # Sequential single-leg fallback
            now = datetime.now(timezone.utc)
            leg_outcomes = []
            for i, leg in enumerate(close_legs):
                def _sub(leg=leg):
                    return adapter.submit_order(
                        symbol=adapter.compose_symbol(leg),
                        side=leg.side,
                        quantity=leg.quantity,
                        order_type=body.order_type,
                        limit_price=body.limit_price,
                        asset_type=leg.asset_type,
                    )
                try:
                    res = await asyncio.to_thread(_sub)
                    leg_outcomes.append({"index": i, "status": "filled",
                                        "filled_price": res.filled_price, "fees": res.fees})
                    db.add(TradeLog(
                        account_id=account_id, position_id=position_id,
                        source="manual", timestamp=now, symbol=leg.symbol,
                        asset_type=leg.asset_type, side=leg.side, quantity=leg.quantity,
                        order_type=body.order_type, filled_price=res.filled_price,
                        fees=res.fees or 0.0, broker_txn_id=res.broker_order_id,
                    ))
                except Exception as e:
                    leg_outcomes.append({"index": i, "status": "rejected", "error": str(e)})

            filled = [lo for lo in leg_outcomes if lo["status"] == "filled"]
            if len(filled) == len(close_legs) and close_quantity is None:
                position.status = "closed"
                position.closed_at = now
            elif filled:
                position.status = "partial_close"

            await db.flush()
            await db.commit()
            status_code = 200 if len(filled) == len(close_legs) else (207 if filled else 422)
            return Response(
                content=json.dumps({
                    "position_id": position_id,
                    "broker_order_id": None,
                    "legs": leg_outcomes,
                    "atomic": False,
                }),
                media_type="application/json",
                status_code=status_code,
            )
    finally:
        _close_adapter(adapter)
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/coordinator/test_position_multileg_close.py -v && pytest tests/coordinator/test_accounts_positions_close.py -v`
Expected: all PASS.

---

### Task 6: Test single-leg fallback path for multi-leg close

**Files:**
- Test: `tests/coordinator/test_position_multileg_close.py` (append)

- [ ] **Step 1: Write the test**

Append to `tests/coordinator/test_position_multileg_close.py`:

```python
@pytest.mark.asyncio
async def test_close_falls_back_to_sequential_when_multileg_unsupported(
    client: AsyncClient, db_session, monkeypatch
):
    """When adapter.supports_multileg_orders returns False, close each leg sequentially."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A", broker_type="mock", environment="paper",
        credentials="{}", supported_asset_types=["options"], pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    pos = Position(
        account_id=account.id, strategy_type="vertical_spread",
        legs=[
            {"symbol": "SPY", "asset_type": "options", "side": "buy", "quantity": 1,
             "expiry": "2026-06-20", "strike": 560.0, "right": "call", "avg_price": 5.0},
            {"symbol": "SPY", "asset_type": "options", "side": "sell", "quantity": 1,
             "expiry": "2026-06-20", "strike": 570.0, "right": "call", "avg_price": 3.0},
        ],
        status="open", net_cost=2.0,
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()

    submitted = []

    class FakeAdapter:
        def supports_multileg_orders(self, legs):
            return False

        def compose_symbol(self, leg):
            return leg.symbol

        def submit_order(self, symbol, side, quantity, order_type,
                         limit_price=None, stop_price=None, asset_type=None):
            submitted.append({"side": side, "symbol": symbol})
            return OrderResult(
                symbol=symbol, side=side, quantity=quantity,
                order_type=order_type, filled_price=4.0, fees=0.0,
                broker_order_id=f"ord-{len(submitted)}",
            )

        def close(self):
            pass

    async def fake_adapter(acct):
        return FakeAdapter()

    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter)

    r = await client.post(f"/api/accounts/{account.id}/positions/{pos.id}/close", json={})
    assert r.status_code == 200
    assert len(submitted) == 2
    assert submitted[0]["side"] == "sell"
    assert submitted[1]["side"] == "buy"
```

- [ ] **Step 2: Run test**

Run: `pytest tests/coordinator/test_position_multileg_close.py -v`
Expected: all PASS.

---

## Sub-project 2: Partial Position Close

### Task 7: Partial close via quantity parameter

**Files:**
- Modify: `coordinator/api/routes/accounts.py` (the `close_position_by_id` handler already accepts `quantity`)
- Test: `tests/coordinator/test_position_partial_close.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/test_position_partial_close.py`:

```python
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from coordinator.database.models import Account, Position, TradeLog


@pytest.mark.asyncio
async def test_partial_close_decrements_remaining_quantity(
    client: AsyncClient, db_session, monkeypatch
):
    """Closing 2 of 5 contracts should leave remaining_quantity=3 and status='open'."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials="{}", supported_asset_types=["equities"], pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    pos = Position(
        account_id=account.id, strategy_type="single",
        legs=[{"symbol": "SPY", "asset_type": "equities",
               "side": "buy", "quantity": 5, "avg_price": 520.0}],
        status="open", net_cost=2600.0, remaining_quantity=5,
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()

    class FakeAdapter:
        def supports_multileg_orders(self, legs):
            return False

        def compose_symbol(self, leg):
            return leg.symbol

        def submit_order(self, symbol, side, quantity, order_type,
                         limit_price=None, stop_price=None, asset_type=None):
            return OrderResult(
                symbol=symbol, side=side, quantity=quantity,
                order_type=order_type, filled_price=530.0,
                broker_order_id="ord-partial",
            )

        def close(self):
            pass

    async def fake_adapter(acct):
        return FakeAdapter()

    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter)

    r = await client.post(
        f"/api/accounts/{account.id}/positions/{pos.id}/close",
        json={"quantity": 2},
    )
    assert r.status_code == 200, r.text

    db_session.expire_all()
    refreshed = (await db_session.execute(
        select(Position).where(Position.id == pos.id)
    )).scalar_one()
    assert refreshed.status == "open"
    assert refreshed.remaining_quantity == 3

    trades = (await db_session.execute(
        select(TradeLog).where(TradeLog.position_id == pos.id)
    )).scalars().all()
    assert len(trades) == 1
    assert trades[0].quantity == 2


@pytest.mark.asyncio
async def test_partial_close_rejects_quantity_exceeding_remaining(
    client: AsyncClient, db_session, monkeypatch
):
    """Cannot close more contracts than remain open."""
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials="{}", supported_asset_types=["equities"], pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    pos = Position(
        account_id=account.id, strategy_type="single",
        legs=[{"symbol": "SPY", "asset_type": "equities",
               "side": "buy", "quantity": 5, "avg_price": 520.0}],
        status="open", net_cost=2600.0, remaining_quantity=3,
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()

    # No adapter needed — validation should reject before broker call
    r = await client.post(
        f"/api/accounts/{account.id}/positions/{pos.id}/close",
        json={"quantity": 5},
    )
    assert r.status_code == 422
    assert "exceeds" in r.json()["detail"].lower()
```

Run: `pytest tests/coordinator/test_position_partial_close.py -v`
Expected: FAIL (partial close logic not implemented yet).

- [ ] **Step 2: Add partial close logic to the handler**

Edit `coordinator/api/routes/accounts.py` — in the `close_position_by_id` handler, after the check for `position.status != "open"`, add validation for partial close:

```python
    # Determine effective remaining quantity
    effective_remaining = position.remaining_quantity
    if effective_remaining is None:
        # Legacy positions: use the first leg's quantity
        effective_remaining = legs[0].get("quantity", 0) if legs else 0

    if body.quantity is not None:
        if body.quantity > effective_remaining:
            raise HTTPException(
                status_code=422,
                detail=f"Requested quantity {body.quantity} exceeds remaining {effective_remaining}",
            )
```

Then in the success path (after fills), update `remaining_quantity`:

```python
    # After successful close fills:
    close_qty = body.quantity or effective_remaining
    new_remaining = effective_remaining - close_qty

    if new_remaining <= 0:
        position.status = "closed"
        position.closed_at = now
        position.remaining_quantity = 0
    else:
        position.remaining_quantity = new_remaining
        # Position stays "open" for partial close
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/coordinator/test_position_partial_close.py -v && pytest tests/coordinator/test_position_multileg_close.py -v`
Expected: all PASS.

---

### Task 8: Cost basis tracking for partial closes

**Files:**
- Modify: `coordinator/api/routes/accounts.py`
- Test: `tests/coordinator/test_position_partial_close.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/coordinator/test_position_partial_close.py`:

```python
@pytest.mark.asyncio
async def test_partial_close_records_cost_basis_lot(
    client: AsyncClient, db_session, monkeypatch
):
    """Each partial close should append a lot entry to cost_basis_lots."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials="{}", supported_asset_types=["equities"], pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    pos = Position(
        account_id=account.id, strategy_type="single",
        legs=[{"symbol": "SPY", "asset_type": "equities",
               "side": "buy", "quantity": 10, "avg_price": 500.0}],
        status="open", net_cost=5000.0, remaining_quantity=10,
        cost_basis_lots=[],
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()

    class FakeAdapter:
        def supports_multileg_orders(self, legs): return False
        def compose_symbol(self, leg): return leg.symbol
        def submit_order(self, symbol, side, quantity, order_type, **kw):
            return OrderResult(symbol=symbol, side=side, quantity=quantity,
                               order_type=order_type, filled_price=510.0,
                               broker_order_id="ord-lot")
        def close(self): pass

    async def fake_adapter(acct): return FakeAdapter()
    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter)

    r = await client.post(
        f"/api/accounts/{account.id}/positions/{pos.id}/close",
        json={"quantity": 3},
    )
    assert r.status_code == 200

    db_session.expire_all()
    refreshed = (await db_session.execute(
        select(Position).where(Position.id == pos.id)
    )).scalar_one()
    assert refreshed.remaining_quantity == 7
    assert len(refreshed.cost_basis_lots) == 1
    assert refreshed.cost_basis_lots[0]["quantity"] == 3
    assert refreshed.cost_basis_lots[0]["fill_price"] == 510.0
```

- [ ] **Step 2: Implement cost basis lot recording**

In the `close_position_by_id` handler, after updating `remaining_quantity`, append to `cost_basis_lots`:

```python
    # Record cost basis lot
    lots = list(position.cost_basis_lots or [])
    for leg_outcome in filled_leg_results:
        lots.append({
            "quantity": close_qty,
            "fill_price": leg_outcome.get("filled_price") or leg_outcome.filled_price,
            "timestamp": now.isoformat(),
            "type": "close",
        })
    position.cost_basis_lots = lots
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/coordinator/test_position_partial_close.py -v`
Expected: all PASS.

---

## Sub-project 3: Limit/Stop Close Orders

### Task 9: Accept `order_type` and `limit_price` in the close endpoint

**Files:**
- Modify: `coordinator/api/routes/accounts.py` (already has `ClosePositionByIdRequest` with `order_type` and `limit_price`)
- Test: `tests/coordinator/test_position_limit_close.py` (create)

- [ ] **Step 1: Write the test for limit close**

Create `tests/coordinator/test_position_limit_close.py`:

```python
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from coordinator.database.models import Account, Position, TradeLog


@pytest.mark.asyncio
async def test_limit_close_passes_order_type_and_price_to_adapter(
    client: AsyncClient, db_session, monkeypatch
):
    """A limit close must pass order_type='limit' and the limit_price to the adapter."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials="{}", supported_asset_types=["equities"], pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    pos = Position(
        account_id=account.id, strategy_type="single",
        legs=[{"symbol": "SPY", "asset_type": "equities",
               "side": "buy", "quantity": 5, "avg_price": 520.0}],
        status="open", net_cost=2600.0, remaining_quantity=5,
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()

    captured = {}

    class FakeAdapter:
        def supports_multileg_orders(self, legs): return False
        def compose_symbol(self, leg): return leg.symbol
        def submit_order(self, symbol, side, quantity, order_type,
                         limit_price=None, stop_price=None, asset_type=None):
            captured["order_type"] = order_type
            captured["limit_price"] = limit_price
            return OrderResult(
                symbol=symbol, side=side, quantity=quantity,
                order_type=order_type, filled_price=525.0,
                broker_order_id="ord-limit",
            )
        def close(self): pass

    async def fake_adapter(acct): return FakeAdapter()
    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter)

    r = await client.post(
        f"/api/accounts/{account.id}/positions/{pos.id}/close",
        json={"order_type": "limit", "limit_price": 525.0},
    )
    assert r.status_code == 200, r.text
    assert captured["order_type"] == "limit"
    assert captured["limit_price"] == 525.0


@pytest.mark.asyncio
async def test_limit_close_requires_limit_price(
    client: AsyncClient, db_session, monkeypatch
):
    """Requesting order_type='limit' without limit_price must return 422."""
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials="{}", supported_asset_types=["equities"], pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    pos = Position(
        account_id=account.id, strategy_type="single",
        legs=[{"symbol": "SPY", "asset_type": "equities",
               "side": "buy", "quantity": 5, "avg_price": 520.0}],
        status="open", net_cost=2600.0, remaining_quantity=5,
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()

    r = await client.post(
        f"/api/accounts/{account.id}/positions/{pos.id}/close",
        json={"order_type": "limit"},
    )
    assert r.status_code == 422
    assert "limit_price" in r.json()["detail"].lower()
```

Run: `pytest tests/coordinator/test_position_limit_close.py -v`

- [ ] **Step 2: Add limit_price validation**

In `close_position_by_id`, before submitting to broker:

```python
    if body.order_type == "limit" and body.limit_price is None:
        raise HTTPException(
            status_code=422,
            detail="limit_price is required when order_type is 'limit'",
        )
    if body.order_type == "stop" and body.limit_price is None:
        raise HTTPException(
            status_code=422,
            detail="limit_price is required when order_type is 'stop' (used as stop_price)",
        )
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/coordinator/test_position_limit_close.py -v`
Expected: PASS.

---

### Task 10: Add `stop_price` support to the close request model

**Files:**
- Modify: `coordinator/api/routes/accounts.py`
- Test: `tests/coordinator/test_position_limit_close.py` (append)

- [ ] **Step 1: Write the test**

Append to `tests/coordinator/test_position_limit_close.py`:

```python
@pytest.mark.asyncio
async def test_stop_close_passes_stop_price_to_adapter(
    client: AsyncClient, db_session, monkeypatch
):
    """A stop close must pass stop_price to the adapter."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials="{}", supported_asset_types=["equities"], pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    pos = Position(
        account_id=account.id, strategy_type="single",
        legs=[{"symbol": "SPY", "asset_type": "equities",
               "side": "buy", "quantity": 5, "avg_price": 520.0}],
        status="open", net_cost=2600.0, remaining_quantity=5,
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()

    captured = {}

    class FakeAdapter:
        def supports_multileg_orders(self, legs): return False
        def compose_symbol(self, leg): return leg.symbol
        def submit_order(self, symbol, side, quantity, order_type,
                         limit_price=None, stop_price=None, asset_type=None):
            captured["order_type"] = order_type
            captured["stop_price"] = stop_price
            return OrderResult(
                symbol=symbol, side=side, quantity=quantity,
                order_type=order_type, filled_price=510.0,
                broker_order_id="ord-stop",
            )
        def close(self): pass

    async def fake_adapter(acct): return FakeAdapter()
    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter)

    r = await client.post(
        f"/api/accounts/{account.id}/positions/{pos.id}/close",
        json={"order_type": "stop", "limit_price": 510.0},
    )
    assert r.status_code == 200
    assert captured["order_type"] == "stop"
    assert captured["stop_price"] == 510.0
```

- [ ] **Step 2: Add `stop_price` to ClosePositionByIdRequest and wire it**

Edit `ClosePositionByIdRequest`:

```python
class ClosePositionByIdRequest(BaseModel):
    order_type: str = "market"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    quantity: Optional[float] = None
```

In the sequential fallback, pass `stop_price` when `order_type == "stop"`:

```python
    def _sub(leg=leg):
        return adapter.submit_order(
            symbol=adapter.compose_symbol(leg),
            side=leg.side,
            quantity=leg.quantity,
            order_type=body.order_type,
            limit_price=body.limit_price if body.order_type == "limit" else None,
            stop_price=body.limit_price if body.order_type == "stop" else body.stop_price,
            asset_type=leg.asset_type,
        )
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/coordinator/test_position_limit_close.py -v`
Expected: all PASS.

---

### Task 11: Position `status` transitions for pending limit/stop orders

**Files:**
- Modify: `coordinator/api/routes/accounts.py`
- Test: `tests/coordinator/test_position_limit_close.py` (append)

- [ ] **Step 1: Write the test**

Append to `tests/coordinator/test_position_limit_close.py`:

```python
@pytest.mark.asyncio
async def test_limit_close_sets_status_to_closing(
    client: AsyncClient, db_session, monkeypatch
):
    """A limit order that returns filled_price=None means pending; status should be 'closing'."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials="{}", supported_asset_types=["equities"], pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    pos = Position(
        account_id=account.id, strategy_type="single",
        legs=[{"symbol": "SPY", "asset_type": "equities",
               "side": "buy", "quantity": 5, "avg_price": 520.0}],
        status="open", net_cost=2600.0, remaining_quantity=5,
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()

    class FakeAdapter:
        def supports_multileg_orders(self, legs): return False
        def compose_symbol(self, leg): return leg.symbol
        def submit_order(self, symbol, side, quantity, order_type,
                         limit_price=None, stop_price=None, asset_type=None):
            return OrderResult(
                symbol=symbol, side=side, quantity=quantity,
                order_type=order_type, filled_price=0.0,
                broker_order_id="ord-pending",
            )
        def close(self): pass

    async def fake_adapter(acct): return FakeAdapter()
    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter)

    r = await client.post(
        f"/api/accounts/{account.id}/positions/{pos.id}/close",
        json={"order_type": "limit", "limit_price": 525.0},
    )
    assert r.status_code == 200

    db_session.expire_all()
    refreshed = (await db_session.execute(
        select(Position).where(Position.id == pos.id)
    )).scalar_one()
    # filled_price=0.0 is our sentinel for "pending" on limit orders
    assert refreshed.status == "closing"
```

- [ ] **Step 2: Implement status=closing for unfilled limit/stop**

In the `close_position_by_id` handler, when determining the final status after fills:

```python
    # After sequential close:
    if body.order_type in ("limit", "stop"):
        # Check if any fill is pending (filled_price=0.0 or None)
        any_pending = any(
            lo.get("filled_price") in (None, 0.0)
            for lo in leg_outcomes if lo["status"] == "filled"
        )
        if any_pending:
            position.status = "closing"
        elif len(filled) == len(close_legs) and close_qty >= effective_remaining:
            position.status = "closed"
            position.closed_at = now
    # ... existing market order status logic
```

Store the broker order ID in position metadata for later cancel/replace:

```python
    meta = dict(position.metadata_ or {})
    meta["pending_close_order_ids"] = [
        lo.get("broker_order_id") for lo in leg_outcomes
        if lo["status"] == "filled"
    ]
    position.metadata_ = meta
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/coordinator/test_position_limit_close.py -v`
Expected: all PASS.

---

## Sub-project 4: Coordinate Manual Close with Running Algorithm

### Task 12: Add `position_closed` WebSocket message type from coordinator to worker

**Files:**
- Modify: `coordinator/api/websocket.py`
- Test: `tests/coordinator/test_websocket_handlers.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/coordinator/test_websocket_handlers.py`:

```python
@pytest.mark.asyncio
async def test_send_position_closed_to_worker():
    """ConnectionManager should be able to send position_closed to a specific worker."""
    from coordinator.api.websocket import manager

    class FakeWS:
        def __init__(self):
            self.messages = []
        async def send_json(self, data):
            self.messages.append(data)

    ws = FakeWS()
    manager.register_worker("worker-1", ws)

    try:
        worker_ws = manager.worker_connections.get("worker-1")
        assert worker_ws is not None
        await worker_ws.send_json({
            "type": "position_closed",
            "instance_id": "inst-1",
            "position_id": "pos-1",
            "symbol": "SPY",
            "reason": "manual_close",
        })
        assert len(ws.messages) == 1
        assert ws.messages[0]["type"] == "position_closed"
        assert ws.messages[0]["symbol"] == "SPY"
    finally:
        manager.disconnect_worker_by_socket(ws)
```

- [ ] **Step 2: Run test**

This test should already pass since `send_json` is generic. The test verifies the message shape. Run: `pytest tests/coordinator/test_websocket_handlers.py::test_send_position_closed_to_worker -v`

---

### Task 13: Notify running algorithm when position is manually closed

**Files:**
- Modify: `coordinator/api/routes/accounts.py`
- Test: `tests/coordinator/test_position_algo_notify.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/test_position_algo_notify.py`:

```python
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from coordinator.database.models import Account, Position, AlgorithmInstance


@pytest.mark.asyncio
async def test_close_notifies_owning_algorithm_instance(
    client: AsyncClient, db_session, monkeypatch
):
    """When a position owned by a running algorithm is closed, the coordinator
    must send a position_closed message to the worker and set state_stale."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes
    from coordinator.api import websocket as ws_module

    account = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials="{}", supported_asset_types=["equities"], pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    # We need a minimal Algorithm + Worker for the instance FK
    from coordinator.database.models import Algorithm, Worker
    algo = Algorithm(name="test-algo", repo_url="https://github.com/test/algo",
                     status="installed")
    worker = Worker(name="w1", status="online")
    db_session.add_all([algo, worker])
    await db_session.flush()

    instance = AlgorithmInstance(
        algorithm_id=algo.id, account_id=account.id, worker_id=worker.id,
        status="running",
    )
    db_session.add(instance)
    await db_session.flush()

    pos = Position(
        account_id=account.id, instance_id=instance.id,
        strategy_type="single",
        legs=[{"symbol": "SPY", "asset_type": "equities",
               "side": "buy", "quantity": 5, "avg_price": 520.0}],
        status="open", net_cost=2600.0, remaining_quantity=5,
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()

    # Track messages sent to worker
    sent_messages = []

    class FakeWS:
        async def send_json(self, data):
            sent_messages.append(data)

    fake_ws = FakeWS()
    ws_module.manager.register_worker(worker.id, fake_ws)

    class FakeAdapter:
        def supports_multileg_orders(self, legs): return False
        def compose_symbol(self, leg): return leg.symbol
        def submit_order(self, symbol, side, quantity, order_type, **kw):
            return OrderResult(symbol=symbol, side=side, quantity=quantity,
                               order_type=order_type, filled_price=530.0,
                               broker_order_id="ord-notify")
        def close(self): pass

    async def fake_adapter(acct): return FakeAdapter()
    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter)

    r = await client.post(
        f"/api/accounts/{account.id}/positions/{pos.id}/close", json={},
    )
    assert r.status_code == 200, r.text

    # Verify position_closed message was sent to worker
    pos_closed_msgs = [m for m in sent_messages if m.get("type") == "position_closed"]
    assert len(pos_closed_msgs) == 1
    assert pos_closed_msgs[0]["instance_id"] == instance.id
    assert pos_closed_msgs[0]["symbol"] == "SPY"
    assert pos_closed_msgs[0]["position_id"] == pos.id
    assert pos_closed_msgs[0]["reason"] == "manual_close"

    # Verify state_stale set on instance
    db_session.expire_all()
    refreshed_inst = (await db_session.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.id == instance.id)
    )).scalar_one()
    assert refreshed_inst.state_stale is True

    # Clean up
    ws_module.manager.disconnect_worker_by_socket(fake_ws)
```

Run: `pytest tests/coordinator/test_position_algo_notify.py -v`
Expected: FAIL (notification logic doesn't exist).

- [ ] **Step 2: Implement notification in close handler**

In `close_position_by_id`, after marking position as closed and committing, add:

```python
    # Notify running algorithm if position is algo-owned
    if position.instance_id:
        try:
            from coordinator.api.websocket import manager as ws_manager

            inst = (await db.execute(
                select(AlgorithmInstance).where(
                    AlgorithmInstance.id == position.instance_id
                )
            )).scalar_one_or_none()

            if inst and inst.status == "running":
                # Set state_stale flag
                inst.state_stale = True
                await db.commit()

                # Find the worker WebSocket and send notification
                worker_ws = ws_manager.worker_connections.get(inst.worker_id)
                if worker_ws is not None:
                    # Collect all unique symbols from the closed legs
                    symbols = list({leg.get("symbol") for leg in (position.legs or [])})
                    await worker_ws.send_json({
                        "type": "position_closed",
                        "instance_id": position.instance_id,
                        "position_id": position_id,
                        "symbol": symbols[0] if symbols else None,
                        "symbols": symbols,
                        "reason": "manual_close",
                    })
        except Exception:
            logger.exception(
                "Failed to notify algorithm instance %s about position close",
                position.instance_id,
            )
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/coordinator/test_position_algo_notify.py -v`
Expected: PASS.

---

### Task 14: Worker agent handles `position_closed` message

**Files:**
- Modify: `worker/agent.py`
- Test: `tests/worker/test_agent_position_closed.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/worker/test_agent_position_closed.py`:

```python
import asyncio
import pytest

from worker.agent import WorkerAgent


@pytest.mark.asyncio
async def test_agent_dispatches_position_closed_to_runtime():
    """When the agent receives a position_closed message, it should forward
    it to the matching running instance's runtime."""
    class FakeWS:
        def __init__(self):
            self.sent = []
        async def send(self, data):
            self.sent.append(data)

    ws = FakeWS()
    agent = WorkerAgent(
        worker_id="w1", worker_name="test",
        websocket=ws, coordinator_http_url="http://test",
    )

    # Add a fake runtime
    received = []

    class FakeRuntime:
        async def on_position_closed(self, message):
            received.append(message)

        def is_healthy(self):
            return True

    agent._running_instances["inst-1"] = FakeRuntime()

    await agent.router.dispatch({
        "type": "position_closed",
        "instance_id": "inst-1",
        "position_id": "pos-1",
        "symbol": "SPY",
        "reason": "manual_close",
    })

    assert len(received) == 1
    assert received[0]["symbol"] == "SPY"
    assert received[0]["reason"] == "manual_close"
```

Run: `pytest tests/worker/test_agent_position_closed.py -v`
Expected: FAIL (no handler registered).

- [ ] **Step 2: Register position_closed handler on WorkerAgent**

Edit `worker/agent.py` — in `register_handlers`, add:

```python
    self.router.register("position_closed", self._handle_position_closed)
```

Add the handler method:

```python
    async def _handle_position_closed(self, message: dict) -> None:
        instance_id = message.get("instance_id")
        runtime = self._running_instances.get(instance_id)
        if runtime is not None and hasattr(runtime, "on_position_closed"):
            try:
                await runtime.on_position_closed(message)
            except Exception:
                logger.exception(
                    "Failed to forward position_closed to instance %s", instance_id
                )
        else:
            logger.debug(
                "position_closed for unknown/incompatible instance %s", instance_id
            )
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/worker/test_agent_position_closed.py -v`
Expected: PASS.

---

### Task 15: Add `on_position_closed` to SDK `QuiltAlgorithm` base class

**Files:**
- Modify: `sdk/algorithm.py`
- Modify: `worker/runner.py`
- Test: `tests/sdk/test_algorithm.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/sdk/test_algorithm.py`:

```python
def test_on_position_closed_default_is_noop():
    """Base QuiltAlgorithm.on_position_closed should be a no-op (no crash)."""
    from sdk.algorithm import QuiltAlgorithm

    class MyAlgo(QuiltAlgorithm):
        def on_start(self, config, restored_state): pass
        def on_tick(self, ctx): return []
        def on_stop(self): return {}
        def save_state(self): return {}

    algo = MyAlgo()
    # Should not raise
    algo.on_position_closed("SPY", "manual_close", {"position_id": "pos-1"})


def test_on_position_closed_can_be_overridden():
    """Subclass can override on_position_closed to receive close events."""
    from sdk.algorithm import QuiltAlgorithm

    received = {}

    class MyAlgo(QuiltAlgorithm):
        def on_start(self, config, restored_state): pass
        def on_tick(self, ctx): return []
        def on_stop(self): return {}
        def save_state(self): return {}
        def on_position_closed(self, symbol, reason, details):
            received["symbol"] = symbol
            received["reason"] = reason

    algo = MyAlgo()
    algo.on_position_closed("SPY", "manual_close", {})
    assert received["symbol"] == "SPY"
    assert received["reason"] == "manual_close"
```

Run: `pytest tests/sdk/test_algorithm.py::test_on_position_closed_default_is_noop -v`
Expected: FAIL (method doesn't exist).

- [ ] **Step 2: Add on_position_closed to QuiltAlgorithm**

Edit `sdk/algorithm.py` — add after `on_trade_executed`:

```python
    def on_position_closed(self, symbol: str, reason: str, details: Optional[dict] = None) -> None:
        """Called when a position is manually closed by the user.

        Override this to acknowledge or react to manual position closes.
        ``reason`` is one of: "manual_close", "stop_loss", "take_profit".
        ``details`` contains position_id, symbols list, etc.
        """
        pass
```

- [ ] **Step 3: Add forwarding in AlgorithmRunner**

Edit `worker/runner.py` — add after `on_trade_executed`:

```python
    def on_position_closed(self, symbol: str, reason: str, details: dict) -> None:
        self._algorithm.on_position_closed(symbol, reason, details)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/sdk/test_algorithm.py -v && pytest tests/worker/test_runner.py -v`
Expected: all PASS.

---

### Task 16: LiveInstanceRuntime forwards position_closed to runner

**Files:**
- Modify: `worker/live_instance_runtime.py` (or wherever the runtime class lives)
- Test: `tests/worker/test_agent_position_closed.py` (append)

- [ ] **Step 1: Locate LiveInstanceRuntime**

The runtime is imported in `worker/agent.py:140`: `from worker.live_instance_runtime import LiveInstanceRuntime`.

- [ ] **Step 2: Write the test**

Append to `tests/worker/test_agent_position_closed.py`:

```python
@pytest.mark.asyncio
async def test_live_instance_runtime_on_position_closed_calls_runner():
    """LiveInstanceRuntime.on_position_closed should call runner.on_position_closed."""
    received = []

    class FakeRunner:
        instance_id = "inst-1"
        def on_position_closed(self, symbol, reason, details):
            received.append({"symbol": symbol, "reason": reason})

    class FakeRuntime:
        """Minimal stand-in to test the forwarding logic."""
        def __init__(self):
            self._runner = FakeRunner()

        async def on_position_closed(self, message):
            symbol = message.get("symbol")
            reason = message.get("reason", "manual_close")
            self._runner.on_position_closed(symbol, reason, message)

    runtime = FakeRuntime()
    await runtime.on_position_closed({
        "type": "position_closed",
        "instance_id": "inst-1",
        "position_id": "pos-1",
        "symbol": "SPY",
        "reason": "manual_close",
    })

    assert len(received) == 1
    assert received[0]["symbol"] == "SPY"
```

- [ ] **Step 3: Add `on_position_closed` to LiveInstanceRuntime**

Read `worker/live_instance_runtime.py` and add:

```python
    async def on_position_closed(self, message: dict) -> None:
        """Forward a manual position close event to the algorithm."""
        symbol = message.get("symbol")
        reason = message.get("reason", "manual_close")
        if self._runner is not None:
            try:
                self._runner.on_position_closed(symbol, reason, message)
            except Exception:
                logger.exception(
                    "Algorithm on_position_closed raised for instance %s",
                    self._runner.instance_id,
                )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/worker/test_agent_position_closed.py -v`
Expected: all PASS.

---

## Frontend Tasks

### Task 17: Update API client with new close-by-position-id method

**Files:**
- Modify: `dashboard/src/api/client.ts`

- [ ] **Step 1: Add `closePositionById` method**

Edit `dashboard/src/api/client.ts` — add after the existing `closePosition` method:

```typescript
  // ── Close position by Quilt position ID (multi-leg, partial, limit/stop) ──
  closePositionById(
    accountId: string,
    positionId: string,
    body: {
      order_type?: "market" | "limit" | "stop";
      limit_price?: number;
      stop_price?: number;
      quantity?: number;
    } = {}
  ): Promise<{
    position_id: string;
    broker_order_id: string | null;
    legs: Array<{
      index: number;
      status: string;
      filled_price: number | null;
      fees: number | null;
    }>;
    atomic: boolean;
  }> {
    return request(`/api/accounts/${accountId}/positions/${positionId}/close`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  // ── Reconcile positions (broker vs DB) ──
  reconcilePositions(
    accountId: string
  ): Promise<{
    matched: Array<{ db_id: string; broker_symbol: string }>;
    untracked: Array<{ symbol: string }>;
    stale: Array<{ id: string }>;
    mismatched: Array<{ db_id: string; broker_qty: number; db_qty: number }>;
  }> {
    return request(`/api/accounts/${accountId}/positions/reconcile`);
  },
```

---

### Task 18: Add `useClosePositionById` hook

**Files:**
- Modify: `dashboard/src/api/hooks.ts`

- [ ] **Step 1: Add the hook**

Edit `dashboard/src/api/hooks.ts` — add after the existing `useClosePosition`:

```typescript
export function useClosePositionById(accountId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      positionId,
      ...body
    }: {
      positionId: string;
      order_type?: "market" | "limit" | "stop";
      limit_price?: number;
      stop_price?: number;
      quantity?: number;
    }) => api.closePositionById(accountId, positionId, body),
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: ["accounts", accountId, "broker-info"],
      });
      void qc.invalidateQueries({
        queryKey: ["accounts", accountId, "positions"],
      });
    },
  });
}
```

---

### Task 19: Add quantity input and order type to close dialog

**Files:**
- Modify: `dashboard/src/pages/AccountDetail.tsx`

- [ ] **Step 1: Add state for close options**

In the state section of AccountDetail, add:

```typescript
  const [closeQuantity, setCloseQuantity] = useState<number | null>(null);
  const [closeOrderType, setCloseOrderType] = useState<"market" | "limit" | "stop">("market");
  const [closeLimitPrice, setCloseLimitPrice] = useState<number | null>(null);
```

- [ ] **Step 2: Update ConfirmDialog to include inputs**

Replace the ConfirmDialog message with a richer body that includes:
- A number input for quantity (defaulting to full position quantity)
- A select for order type (market/limit/stop)
- A price input that appears when limit or stop is selected

```typescript
      <ConfirmDialog
        open={!!closeTarget}
        title="Close position"
        message={
          closeTarget
            ? `Close ${closeTarget.symbol} (${closeTarget.side}, ${closeTarget.quantity} units)`
            : ""
        }
        confirmLabel={closePos.isPending ? "Closing…" : "Close position"}
        cancelLabel="Cancel"
        onConfirm={onConfirmClose}
        onCancel={() => {
          setCloseTarget(null);
          setCloseError(null);
          setCloseQuantity(null);
          setCloseOrderType("market");
          setCloseLimitPrice(null);
        }}
      >
        {closeTarget && (
          <div className="space-y-3 mt-3">
            <div>
              <label className="block text-xs text-zinc-400 mb-1">Quantity</label>
              <input
                type="number"
                min={1}
                max={closeTarget.quantity}
                value={closeQuantity ?? closeTarget.quantity}
                onChange={(e) => setCloseQuantity(Number(e.target.value) || null)}
                className="w-full px-2 py-1 rounded bg-zinc-800 border border-zinc-600 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-zinc-400 mb-1">Order Type</label>
              <select
                value={closeOrderType}
                onChange={(e) => setCloseOrderType(e.target.value as "market" | "limit" | "stop")}
                className="w-full px-2 py-1 rounded bg-zinc-800 border border-zinc-600 text-sm"
              >
                <option value="market">Market</option>
                <option value="limit">Limit</option>
                <option value="stop">Stop</option>
              </select>
            </div>
            {(closeOrderType === "limit" || closeOrderType === "stop") && (
              <div>
                <label className="block text-xs text-zinc-400 mb-1">
                  {closeOrderType === "limit" ? "Limit Price" : "Stop Price"}
                </label>
                <input
                  type="number"
                  step="0.01"
                  value={closeLimitPrice ?? ""}
                  onChange={(e) => setCloseLimitPrice(Number(e.target.value) || null)}
                  className="w-full px-2 py-1 rounded bg-zinc-800 border border-zinc-600 text-sm"
                />
              </div>
            )}
            {closeError && (
              <p className="text-red-400 text-xs">{closeError}</p>
            )}
          </div>
        )}
      </ConfirmDialog>
```

- [ ] **Step 3: Update `onConfirmClose` to pass new parameters**

```typescript
  const onConfirmClose = () => {
    if (!closeTarget || closePos.isPending) return;
    setCloseError(null);
    closePos.mutate(
      {
        symbol: closeTarget.symbol,
        asset_type: closeTarget.asset_class || "equities",
        side: closeTarget.side === "short" ? "short" : "long",
        quantity: closeQuantity ?? closeTarget.quantity,
        order_type: closeOrderType,
        limit_price: closeOrderType !== "market" ? closeLimitPrice : undefined,
      },
      {
        onSuccess: () => {
          setCloseTarget(null);
          setCloseQuantity(null);
          setCloseOrderType("market");
          setCloseLimitPrice(null);
        },
        onError: (err: Error) => {
          setCloseError(err.message);
        },
      },
    );
  };
```

Note: This uses the existing `useClosePosition` hook for now since the legacy endpoint already handles single-leg. For multi-leg Quilt positions, use `useClosePositionById` when the UI knows the Quilt position ID.

---

### Task 20: Update frontend close API type to support new fields

**Files:**
- Modify: `dashboard/src/api/client.ts`

- [ ] **Step 1: Extend `closePosition` body type**

Edit the existing `closePosition` method signature to accept optional new fields:

```typescript
  closePosition(
    accountId: string,
    body: {
      symbol: string;
      asset_type: string;
      side: "long" | "short";
      quantity: number;
      order_type?: "market" | "limit" | "stop";
      limit_price?: number;
    }
  ): Promise<{
    broker_order_id: string | null;
    filled_price: number | null;
    status: "filled" | "pending";
  }> {
    return request(`/api/accounts/${accountId}/positions/close`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
```

---

## Bug Fix Tasks

### Task 21: Fix `open_position` sequential fallback missing `asset_type`

**Files:**
- Modify: `coordinator/api/routes/accounts.py`
- Test: `tests/coordinator/test_accounts_positions_open.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/coordinator/test_accounts_positions_open.py`:

```python
@pytest.mark.asyncio
async def test_open_position_sequential_passes_asset_type(
    client: AsyncClient, db_session, monkeypatch
):
    """The sequential fallback in open_position must pass asset_type to submit_order."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials="{}", supported_asset_types=["crypto"], pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()
    await db_session.commit()

    captured = {}

    class FakeAdapter:
        def supports_multileg_orders(self, legs):
            return False

        def compose_symbol(self, leg):
            return leg.symbol

        def submit_order(self, symbol, side, quantity, order_type,
                         limit_price=None, stop_price=None, asset_type=None):
            captured["asset_type"] = asset_type
            return OrderResult(
                symbol=symbol, side=side, quantity=quantity,
                order_type=order_type, filled_price=50000.0,
                broker_order_id="ord-crypto",
            )

        def close(self):
            pass

    async def fake_adapter(acct):
        return FakeAdapter()

    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter)

    body = {
        "legs": [{"symbol": "BTCUSD", "asset_type": "crypto",
                  "side": "buy", "quantity": 0.01}],
        "order_type": "market",
    }
    r = await client.post(f"/api/accounts/{account.id}/positions/open", json=body)
    assert r.status_code == 200, r.text
    assert captured.get("asset_type") == "crypto"
```

Run: `pytest tests/coordinator/test_accounts_positions_open.py::test_open_position_sequential_passes_asset_type -v`
Expected: FAIL (asset_type not passed).

- [ ] **Step 2: Fix the sequential fallback**

Edit `coordinator/api/routes/accounts.py` at line ~909 in the sequential fallback:

```python
                def _sub(leg=leg):
                    return adapter.submit_order(
                        symbol=adapter.compose_symbol(leg),
                        side=leg.side,
                        quantity=leg.quantity,
                        order_type=body.order_type,
                        limit_price=body.limit_price,
                        asset_type=leg.asset_type,
                    )
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/coordinator/test_accounts_positions_open.py -v`
Expected: all PASS.

---

### Task 22: Extend legacy close endpoint to pass `order_type` and `limit_price`

**Files:**
- Modify: `coordinator/api/routes/accounts.py`
- Test: `tests/coordinator/test_accounts_positions_close.py` (append)

- [ ] **Step 1: Write the test**

Append to `tests/coordinator/test_accounts_positions_close.py`:

```python
@pytest.mark.asyncio
async def test_close_passes_order_type_and_limit_price_to_adapter(
    client: AsyncClient, db_session, monkeypatch
):
    """The legacy close endpoint should forward order_type and limit_price."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials="{}", supported_asset_types=["equities"], pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()
    await db_session.commit()

    captured = {}

    class FakeAdapter:
        def submit_order(self, symbol, side, quantity, order_type,
                         limit_price=None, stop_price=None, asset_type=None):
            captured["order_type"] = order_type
            captured["limit_price"] = limit_price
            return OrderResult(
                symbol=symbol, side=side, quantity=quantity,
                order_type=order_type, filled_price=525.0,
                broker_order_id="ord-lim",
            )
        def close(self): pass

    async def fake_adapter(acct): return FakeAdapter()
    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter)

    body = {
        "symbol": "SPY", "asset_type": "equities",
        "side": "long", "quantity": 5,
        "order_type": "limit", "limit_price": 525.0,
    }
    r = await client.post(f"/api/accounts/{account.id}/positions/close", json=body)
    assert r.status_code == 200, r.text
    assert captured["order_type"] == "limit"
    assert captured["limit_price"] == 525.0
```

- [ ] **Step 2: Update ClosePositionRequest and handler**

Edit `ClosePositionRequest`:

```python
class ClosePositionRequest(BaseModel):
    symbol: str
    asset_type: str
    side: str
    quantity: float
    order_type: str = "market"
    limit_price: Optional[float] = None
```

Edit the `close_position` handler to use `body.order_type` and `body.limit_price`:

```python
        def _sub():
            return adapter.submit_order(
                symbol=body.symbol,
                side=order_side,
                quantity=body.quantity,
                order_type=body.order_type,
                limit_price=body.limit_price,
                asset_type=body.asset_type,
            )
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/coordinator/test_accounts_positions_close.py -v`
Expected: all PASS.

---

## Integration and Verification

### Task 23: Run full test suite to verify no regressions

- [ ] **Step 1: Run all position-related tests**

```bash
pytest tests/coordinator/test_accounts_positions_open.py \
       tests/coordinator/test_accounts_positions_close.py \
       tests/coordinator/test_position_management_model.py \
       tests/coordinator/test_position_multileg_close.py \
       tests/coordinator/test_position_partial_close.py \
       tests/coordinator/test_position_limit_close.py \
       tests/coordinator/test_position_algo_notify.py \
       tests/worker/test_agent_position_closed.py \
       tests/worker/test_broker_adapter.py \
       tests/sdk/test_algorithm.py \
       tests/coordinator/services/test_position_reconciler.py \
       -v
```

Expected: all PASS.

- [ ] **Step 2: Run broader test suite**

```bash
pytest tests/ -x --timeout=60
```

Expected: all existing tests remain green.

---

### Task 24: Update backlog to mark resolved items

**Files:**
- Modify: `docs/superpowers/backlog.md`

- [ ] **Step 1: Mark resolved items**

In `docs/superpowers/backlog.md`, add "RESOLVED" markers to each of the 5 position items that this plan addresses:

- Multi-leg / spread-aware position close -> RESOLVED by Task 5-6
- Partial position close -> RESOLVED by Task 7-8
- Limit / stop close orders -> RESOLVED by Task 9-11
- Coordinate manual close with running algorithm -> RESOLVED by Task 12-16
- Holistic position-tracking model -> RESOLVED by Task 1-3
- `open_position` doesn't forward `asset_type` -> RESOLVED by Task 21

Keep "Bulk close-all action" as still deferred (not in scope for this plan).

---

## Summary

| Sub-project | Tasks | Files Created | Files Modified |
|---|---|---|---|
| 5: Holistic Position Model | 1-3 | `test_position_management_model.py`, `position_reconciler.py`, `test_position_reconciler.py` | `models.py`, `accounts.py` |
| 1: Multi-leg Close | 4-6 | `test_position_multileg_close.py` | `broker_adapter.py`, `tradier_adapter.py`, `alpaca_adapter.py`, `accounts.py` |
| 2: Partial Close | 7-8 | `test_position_partial_close.py` | `accounts.py` |
| 3: Limit/Stop Close | 9-11 | `test_position_limit_close.py` | `accounts.py` |
| 4: Algo Coordination | 12-16 | `test_position_algo_notify.py`, `test_agent_position_closed.py` | `agent.py`, `algorithm.py`, `runner.py`, `live_instance_runtime.py`, `websocket.py`, `accounts.py` |
| Frontend | 17-20 | (none) | `client.ts`, `hooks.ts`, `AccountDetail.tsx` |
| Bug Fix | 21-22 | (none) | `accounts.py` |
| Integration | 23-24 | (none) | `backlog.md` |

**Total: 24 tasks, ~30 test functions, 5 new test files, 1 new service file, 1 migration.**
