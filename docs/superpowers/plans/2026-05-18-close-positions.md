# Close Positions From Account Page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user close any open broker position visible on the AccountDetail page by clicking a per-row Close button, which submits an opposite-side market order via the broker adapter and (if Quilt has an internal `Position` record for that symbol) marks it closed.

**Architecture:** New `POST /api/accounts/{account_id}/positions/close` endpoint on the existing accounts router. It resolves the broker adapter via the existing `_adapter_for_account` helper, calls `adapter.submit_order` with the inverted side, and — only if a matching internal `Position` exists — updates its status + writes a closing `TradeLog` row. The frontend adds an action column to the positions table, opens a `ConfirmDialog`, and fires a new `useClosePosition` mutation that invalidates the broker-info query so the table refetches.

**Tech Stack:** FastAPI + SQLAlchemy (async) on the backend; React + react-query + Tailwind on the frontend. Tests use pytest (`tests/coordinator/test_accounts_positions_open.py` is the reference fixture pattern) and vitest + react-testing-library + `vi.mock` (`dashboard/src/pages/DeploymentDetail.test.tsx` is the reference).

**Spec:** `docs/superpowers/specs/2026-05-18-close-positions-design.md` (commit `a3bfaee`).

**Deferred (do not include):** multi-leg close, partial close, limit/stop close, bulk close-all, algo coordination. See `docs/superpowers/backlog.md`.

---

## File Structure

**Backend:**
- Modify `coordinator/api/routes/accounts.py` — add `ClosePositionRequest` model + new handler. Append after the existing `open_position` handler so the related code lives together.
- Create `tests/coordinator/test_accounts_positions_close.py` — dedicated test file mirroring `test_accounts_positions_open.py`.

**Frontend:**
- Modify `dashboard/src/api/client.ts` — add `closePosition` method.
- Modify `dashboard/src/api/hooks.ts` — add `useClosePosition` hook.
- Modify `dashboard/src/pages/AccountDetail.tsx` — add action column, close-target state, error state, ConfirmDialog wiring.
- Create `dashboard/src/pages/AccountDetail.test.tsx` — new test file covering the close flow.

**Known existing-code quirk (NOT a fix in scope):** `useOpenPosition` invalidates query key `["brokerInfo", accountId]` but `useBrokerInfo` registers under `["accounts", id, "broker-info"]`. The new `useClosePosition` must use the *correct* key `["accounts", accountId, "broker-info"]` so the table actually refreshes. Don't touch the existing buggy invalidation — it's out of scope for this plan and would need its own fix.

---

## Task 1: Backend — request model + handler for long position close

**Files:**
- Modify: `coordinator/api/routes/accounts.py` (append after existing `open_position` handler)
- Test: `tests/coordinator/test_accounts_positions_close.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/test_accounts_positions_close.py` with:

```python
import pytest
from httpx import AsyncClient

from coordinator.database.models import Account


@pytest.mark.asyncio
async def test_close_long_position_submits_sell_market_order(
    client: AsyncClient, db_session, monkeypatch
):
    """Closing a long position must submit an opposite-side (sell) market order."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A",
        broker_type="alpaca",
        environment="paper",
        credentials="{}",
        supported_asset_types=["equities"],
        pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()
    await db_session.commit()

    captured = {}

    class FakeAdapter:
        def submit_order(self, symbol, side, quantity, order_type,
                         limit_price=None, stop_price=None):
            captured["symbol"] = symbol
            captured["side"] = side
            captured["quantity"] = quantity
            captured["order_type"] = order_type
            return OrderResult(
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type=order_type,
                filled_price=521.23,
                fees=0.0,
                broker_order_id="ord-abc",
            )

        def close(self):
            pass

    async def fake_adapter_for_account(acct):
        return FakeAdapter()

    monkeypatch.setattr(
        accounts_routes, "_adapter_for_account", fake_adapter_for_account
    )

    body = {
        "symbol": "SPY",
        "asset_type": "equities",
        "side": "long",
        "quantity": 5,
    }
    r = await client.post(f"/api/accounts/{account.id}/positions/close", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["broker_order_id"] == "ord-abc"
    assert data["filled_price"] == 521.23
    assert data["status"] == "filled"
    # Side passed to adapter must be the *opposite* of the position side.
    assert captured["side"] == "sell"
    assert captured["symbol"] == "SPY"
    assert captured["quantity"] == 5
    assert captured["order_type"] == "market"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/coordinator/test_accounts_positions_close.py::test_close_long_position_submits_sell_market_order -v`
Expected: FAIL (404 on POST — endpoint doesn't exist yet).

- [ ] **Step 3: Implement the endpoint**

Open `coordinator/api/routes/accounts.py`. At the top of the file, confirm these imports are already present (they are, per the existing `open_position` handler). At the end of the file, add the `ClosePositionRequest` model alongside the existing pydantic models (near `OpenPositionRequest`):

```python
class ClosePositionRequest(BaseModel):
    symbol: str
    asset_type: str
    side: str  # "long" or "short" — the *position* side, not the order side
    quantity: float
```

Then add the new handler — place it directly after the `open_position` handler so related code lives together:

```python
@router.post("/{account_id}/positions/close")
async def close_position(
    account_id: str,
    body: ClosePositionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Close an open broker position by submitting an opposite-side market order.

    Identifies the position by broker-visible (symbol, side, quantity).
    Does NOT honor the account `locked_by` check — closes must work as a
    safety valve even when an algorithm holds the account lock.
    """
    account = (await db.execute(
        select(Account).where(Account.id == account_id)
    )).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")

    pos_side = body.side.lower()
    if pos_side not in ("long", "short"):
        raise HTTPException(
            status_code=422,
            detail=f"side must be 'long' or 'short', got {body.side!r}",
        )
    order_side = "sell" if pos_side == "long" else "buy"

    adapter = await _adapter_for_account(account)
    try:
        def _sub():
            return adapter.submit_order(
                symbol=body.symbol,
                side=order_side,
                quantity=body.quantity,
                order_type="market",
            )
        result = await asyncio.to_thread(_sub)
    finally:
        _close_adapter(adapter)

    return {
        "broker_order_id": result.broker_order_id,
        "filled_price": result.filled_price,
        "status": "filled" if result.filled_price else "pending",
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/coordinator/test_accounts_positions_close.py::test_close_long_position_submits_sell_market_order -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/coordinator/test_accounts_positions_close.py coordinator/api/routes/accounts.py
git commit -m "feat(coord): POST /accounts/{id}/positions/close — long position case"
```

---

## Task 2: Backend — short position close submits a buy order

**Files:**
- Test: `tests/coordinator/test_accounts_positions_close.py` (append)

- [ ] **Step 1: Add the failing test**

Append to `tests/coordinator/test_accounts_positions_close.py`:

```python
@pytest.mark.asyncio
async def test_close_short_position_submits_buy_market_order(
    client: AsyncClient, db_session, monkeypatch
):
    """Closing a short position must submit a buy order."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A",
        broker_type="alpaca",
        environment="paper",
        credentials="{}",
        supported_asset_types=["equities"],
        pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()
    await db_session.commit()

    captured = {}

    class FakeAdapter:
        def submit_order(self, symbol, side, quantity, order_type,
                         limit_price=None, stop_price=None):
            captured["side"] = side
            return OrderResult(
                symbol=symbol, side=side, quantity=quantity,
                order_type=order_type, filled_price=100.0,
                broker_order_id="ord-x",
            )
        def close(self): pass

    async def fake_adapter_for_account(acct):
        return FakeAdapter()
    monkeypatch.setattr(
        accounts_routes, "_adapter_for_account", fake_adapter_for_account
    )

    body = {"symbol": "TSLA", "asset_type": "equities",
            "side": "short", "quantity": 2}
    r = await client.post(f"/api/accounts/{account.id}/positions/close", json=body)
    assert r.status_code == 200
    assert captured["side"] == "buy"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/coordinator/test_accounts_positions_close.py::test_close_short_position_submits_buy_market_order -v`
Expected: PASS (the implementation from Task 1 already handles this — the test locks in the behavior).

- [ ] **Step 3: Commit**

```bash
git add tests/coordinator/test_accounts_positions_close.py
git commit -m "test(coord): close-position handles short→buy inversion"
```

---

## Task 3: Backend — mark internal Position closed when one exists

**Files:**
- Modify: `coordinator/api/routes/accounts.py` (the new `close_position` handler)
- Test: `tests/coordinator/test_accounts_positions_close.py` (append)

- [ ] **Step 1: Add the failing test**

Append to `tests/coordinator/test_accounts_positions_close.py`:

```python
@pytest.mark.asyncio
async def test_close_marks_internal_position_closed_and_writes_trade(
    client: AsyncClient, db_session, monkeypatch
):
    """If Quilt has an internal Position for the symbol, mark it closed
    and write a closing TradeLog row."""
    from datetime import datetime, timezone
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes
    from coordinator.database.models import Position, TradeLog
    from sqlalchemy import select

    account = Account(
        name="A",
        broker_type="alpaca",
        environment="paper",
        credentials="{}",
        supported_asset_types=["equities"],
        pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    pos = Position(
        account_id=account.id,
        strategy_type="single",
        legs=[{"symbol": "SPY", "asset_type": "equities",
               "side": "buy", "quantity": 5, "avg_price": 500.0}],
        status="open",
        net_cost=2500.0,
    )
    db_session.add(pos)
    await db_session.flush()
    await db_session.commit()
    pos_id = pos.id

    class FakeAdapter:
        def submit_order(self, symbol, side, quantity, order_type,
                         limit_price=None, stop_price=None):
            return OrderResult(
                symbol=symbol, side=side, quantity=quantity,
                order_type=order_type, filled_price=520.0,
                fees=0.0, broker_order_id="ord-xyz",
            )
        def close(self): pass

    async def fake_adapter_for_account(acct):
        return FakeAdapter()
    monkeypatch.setattr(
        accounts_routes, "_adapter_for_account", fake_adapter_for_account
    )

    body = {"symbol": "SPY", "asset_type": "equities",
            "side": "long", "quantity": 5}
    r = await client.post(f"/api/accounts/{account.id}/positions/close", json=body)
    assert r.status_code == 200, r.text

    db_session.expire_all()
    refreshed = (await db_session.execute(
        select(Position).where(Position.id == pos_id)
    )).scalar_one()
    assert refreshed.status == "closed"
    assert refreshed.closed_at is not None

    trades = (await db_session.execute(
        select(TradeLog).where(TradeLog.position_id == pos_id)
    )).scalars().all()
    assert len(trades) == 1
    assert trades[0].symbol == "SPY"
    assert trades[0].side == "sell"
    assert trades[0].quantity == 5
    assert trades[0].filled_price == 520.0
    assert trades[0].broker_txn_id == "ord-xyz"
    assert trades[0].source == "manual"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/coordinator/test_accounts_positions_close.py::test_close_marks_internal_position_closed_and_writes_trade -v`
Expected: FAIL (position remains open; no TradeLog row).

- [ ] **Step 3: Extend the handler**

In `coordinator/api/routes/accounts.py`, modify the `close_position` handler so that after the successful `submit_order` call (and before returning), it looks up any matching open `Position` row and updates it. Replace the existing return with:

```python
    # If Quilt has an internal Position record for this symbol, mark it closed
    # and write a closing TradeLog row. Multiple matches are allowed (e.g. an
    # algo + a manual position on the same symbol); we close all of them here
    # because the broker treats this as a single net flat.
    matches = (await db.execute(
        select(Position).where(
            Position.account_id == account_id,
            Position.status == "open",
        )
    )).scalars().all()
    matching = [
        p for p in matches
        if any(leg.get("symbol") == body.symbol for leg in (p.legs or []))
    ]
    now = datetime.now(timezone.utc)
    for p in matching:
        p.status = "closed"
        p.closed_at = now
        db.add(TradeLog(
            account_id=account_id,
            position_id=p.id,
            source="manual",
            timestamp=now,
            symbol=body.symbol,
            asset_type=body.asset_type,
            side=order_side,
            quantity=body.quantity,
            order_type="market",
            filled_price=result.filled_price,
            fees=result.fees or 0.0,
            broker_txn_id=result.broker_order_id,
        ))
    await db.flush()
    await db.commit()

    return {
        "broker_order_id": result.broker_order_id,
        "filled_price": result.filled_price,
        "status": "filled" if result.filled_price else "pending",
    }
```

If the imports for `Position`, `TradeLog`, `datetime`, `timezone` aren't already at the top of the file, confirm they are (the `open_position` handler uses all of them; they should already be imported).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/coordinator/test_accounts_positions_close.py::test_close_marks_internal_position_closed_and_writes_trade -v`
Expected: PASS.

Also re-run the whole file to make sure Tasks 1 and 2 still pass:
Run: `pytest tests/coordinator/test_accounts_positions_close.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add coordinator/api/routes/accounts.py tests/coordinator/test_accounts_positions_close.py
git commit -m "feat(coord): close-position marks internal Position closed + writes TradeLog"
```

---

## Task 4: Backend — close succeeds when no internal Position exists

**Files:**
- Test: `tests/coordinator/test_accounts_positions_close.py` (append)

- [ ] **Step 1: Add the test**

Append to `tests/coordinator/test_accounts_positions_close.py`:

```python
@pytest.mark.asyncio
async def test_close_succeeds_when_no_internal_position_record(
    client: AsyncClient, db_session, monkeypatch
):
    """If the user opened a position directly in the broker (no internal
    Position row), the close should still succeed — just submit the broker
    order and skip the DB update."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes
    from coordinator.database.models import TradeLog
    from sqlalchemy import select

    account = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials="{}", supported_asset_types=["equities"], pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()
    await db_session.commit()

    class FakeAdapter:
        def submit_order(self, symbol, side, quantity, order_type,
                         limit_price=None, stop_price=None):
            return OrderResult(
                symbol=symbol, side=side, quantity=quantity,
                order_type=order_type, filled_price=42.0,
                broker_order_id="ord-1",
            )
        def close(self): pass

    async def fake_adapter_for_account(acct):
        return FakeAdapter()
    monkeypatch.setattr(
        accounts_routes, "_adapter_for_account", fake_adapter_for_account
    )

    body = {"symbol": "AAPL", "asset_type": "equities",
            "side": "long", "quantity": 1}
    r = await client.post(f"/api/accounts/{account.id}/positions/close", json=body)
    assert r.status_code == 200
    # No TradeLog rows should be written if there was no Position to attribute to.
    trades = (await db_session.execute(select(TradeLog))).scalars().all()
    assert trades == []
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/coordinator/test_accounts_positions_close.py::test_close_succeeds_when_no_internal_position_record -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/coordinator/test_accounts_positions_close.py
git commit -m "test(coord): close-position no-op DB when no internal Position record"
```

---

## Task 5: Backend — adapter failures surface as 500; missing account is 404

**Files:**
- Test: `tests/coordinator/test_accounts_positions_close.py` (append)

- [ ] **Step 1: Add tests**

Append to `tests/coordinator/test_accounts_positions_close.py`:

```python
@pytest.mark.asyncio
async def test_close_returns_404_for_missing_account(client: AsyncClient):
    body = {"symbol": "SPY", "asset_type": "equities",
            "side": "long", "quantity": 1}
    r = await client.post("/api/accounts/does-not-exist/positions/close", json=body)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_close_propagates_adapter_error_as_500(
    client: AsyncClient, db_session, monkeypatch
):
    """If the broker adapter raises (broker rejection, network, etc.) the
    error must surface to the caller — not be silently swallowed."""
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials="{}", supported_asset_types=["equities"], pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()
    await db_session.commit()

    class FakeAdapter:
        def submit_order(self, symbol, side, quantity, order_type,
                         limit_price=None, stop_price=None):
            raise RuntimeError("broker says no: insufficient buying power")
        def close(self): pass

    async def fake_adapter_for_account(acct):
        return FakeAdapter()
    monkeypatch.setattr(
        accounts_routes, "_adapter_for_account", fake_adapter_for_account
    )

    body = {"symbol": "SPY", "asset_type": "equities",
            "side": "long", "quantity": 1}
    r = await client.post(f"/api/accounts/{account.id}/positions/close", json=body)
    assert r.status_code == 500
    assert "insufficient buying power" in r.text
```

- [ ] **Step 2: Run tests to verify them**

Run: `pytest tests/coordinator/test_accounts_positions_close.py::test_close_returns_404_for_missing_account tests/coordinator/test_accounts_positions_close.py::test_close_propagates_adapter_error_as_500 -v`
Expected: 404 test PASSES (handler already raises 404). The 500 test may PASS (FastAPI raises 500 on unhandled exception by default, and the RuntimeError message ends up in the response body in test mode) — if it fails because the error message isn't in the body, wrap the `submit_order` call:

```python
    try:
        def _sub():
            return adapter.submit_order(
                symbol=body.symbol,
                side=order_side,
                quantity=body.quantity,
                order_type="market",
            )
        result = await asyncio.to_thread(_sub)
    except Exception as e:
        _close_adapter(adapter)
        raise HTTPException(status_code=500, detail=str(e))
    else:
        _close_adapter(adapter)
```

(Replace the existing `try/finally` block around `submit_order` with the `try/except/else` form above.)

Then re-run the test. Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/coordinator/test_accounts_positions_close.py coordinator/api/routes/accounts.py
git commit -m "feat(coord): close-position 404 on missing account, surface adapter errors as 500"
```

---

## Task 6: Backend — close ignores account `locked_by`

**Files:**
- Test: `tests/coordinator/test_accounts_positions_close.py` (append)

- [ ] **Step 1: Add the test**

Append to `tests/coordinator/test_accounts_positions_close.py`:

```python
@pytest.mark.asyncio
async def test_close_succeeds_even_when_account_is_locked(
    client: AsyncClient, db_session, monkeypatch
):
    """The close endpoint is a safety valve and must work even when an
    algorithm currently holds the account lock. Unlike open_position
    (which returns 423), close ignores locked_by."""
    from worker.broker_adapter import OrderResult
    from coordinator.api.routes import accounts as accounts_routes

    account = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials="{}", supported_asset_types=["equities"],
        pdt_mode="off",
        locked_by="instance-running",
    )
    db_session.add(account)
    await db_session.flush()
    await db_session.commit()

    class FakeAdapter:
        def submit_order(self, symbol, side, quantity, order_type,
                         limit_price=None, stop_price=None):
            return OrderResult(
                symbol=symbol, side=side, quantity=quantity,
                order_type=order_type, filled_price=100.0,
                broker_order_id="ord-locked",
            )
        def close(self): pass

    async def fake_adapter_for_account(acct):
        return FakeAdapter()
    monkeypatch.setattr(
        accounts_routes, "_adapter_for_account", fake_adapter_for_account
    )

    body = {"symbol": "SPY", "asset_type": "equities",
            "side": "long", "quantity": 1}
    r = await client.post(f"/api/accounts/{account.id}/positions/close", json=body)
    assert r.status_code == 200, r.text
```

- [ ] **Step 2: Run test to verify**

Run: `pytest tests/coordinator/test_accounts_positions_close.py::test_close_succeeds_even_when_account_is_locked -v`
Expected: PASS (the handler from Task 1 deliberately omits the locked_by check).

Full backend sweep — make sure nothing earlier regressed:
Run: `pytest tests/coordinator/test_accounts_positions_close.py tests/coordinator/test_accounts_positions_open.py -v`
Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add tests/coordinator/test_accounts_positions_close.py
git commit -m "test(coord): close-position bypasses locked_by check"
```

---

## Task 7: Frontend — API client method + react-query hook

**Files:**
- Modify: `dashboard/src/api/client.ts`
- Modify: `dashboard/src/api/hooks.ts`

- [ ] **Step 1: Add the `closePosition` client method**

Open `dashboard/src/api/client.ts`. Locate the existing `openPosition` method (around line 609). Immediately after it, add:

```typescript
closePosition(
  accountId: string,
  body: {
    symbol: string;
    asset_type: string;
    side: "long" | "short";
    quantity: number;
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
}
```

- [ ] **Step 2: Add the `useClosePosition` hook**

Open `dashboard/src/api/hooks.ts`. Locate the existing `useOpenPosition` hook (around line 693). Immediately after it, add:

```typescript
export function useClosePosition(accountId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof api.closePosition>[1]) =>
      api.closePosition(accountId, body),
    onSuccess: () => {
      // Match the actual query key used by useBrokerInfo so the table refetches.
      void qc.invalidateQueries({
        queryKey: ["accounts", accountId, "broker-info"],
      });
    },
  });
}
```

- [ ] **Step 3: Type-check**

Run: `cd dashboard && npx tsc --noEmit`
Expected: clean (no new errors). If existing-codebase errors are present, ensure the new lines didn't introduce any *additional* ones.

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/api/client.ts dashboard/src/api/hooks.ts
git commit -m "feat(dashboard): closePosition client method + useClosePosition hook"
```

---

## Task 8: Frontend — Close button in positions table + confirm dialog wiring

**Files:**
- Modify: `dashboard/src/pages/AccountDetail.tsx`

- [ ] **Step 1: Add state + handlers + dialog**

Open `dashboard/src/pages/AccountDetail.tsx`.

Add these imports (or extend existing ones) at the top:

```typescript
import { useState } from "react";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useClosePosition } from "../api/hooks";
import type { BrokerPosition } from "../api/client";
```

Inside the `AccountDetail` component (near the other `useState` / mutation declarations), add:

```typescript
const [closeTarget, setCloseTarget] = useState<BrokerPosition | null>(null);
const [closeError, setCloseError] = useState<string | null>(null);
const closePos = useClosePosition(id ?? "");

const onConfirmClose = () => {
  if (!closeTarget || closePos.isPending) return;
  setCloseError(null);
  closePos.mutate(
    {
      symbol: closeTarget.symbol,
      asset_type: "equities",  // v1: equities only; broker-info doesn't expose asset_type today
      side: closeTarget.side === "short" ? "short" : "long",
      quantity: closeTarget.quantity,
    },
    {
      onSuccess: () => {
        setCloseTarget(null);
      },
      onError: (err: Error) => {
        setCloseError(err.message);
      },
    },
  );
};
```

Locate the `positionColumns` array (around line 182). Append one more column to the array:

```typescript
{
  id: "actions",
  header: "",
  cell: ({ row }) => (
    <button
      type="button"
      onClick={() => { setCloseError(null); setCloseTarget(row.original); }}
      className="px-2 py-1 rounded text-xs font-medium text-white bg-red-600 hover:bg-red-500 transition-colors"
    >
      Close
    </button>
  ),
},
```

NOTE: `positionColumns` is currently defined at module scope but now references the `setCloseTarget` / `setCloseError` closures. Move the array definition *inside* the `AccountDetail` component (after the state declarations) so it captures those closures. If `positionColumns` was previously memoized with `useMemo`, keep it memoized — just move it inside.

Locate the positions `CollapsibleSection` JSX (around line 665). Immediately after the closing `</CollapsibleSection>` for positions, add the dialog:

```typescript
<ConfirmDialog
  open={!!closeTarget}
  title="Close position"
  message={
    closeTarget
      ? `Close ${closeTarget.quantity} ${closeTarget.side} ${closeTarget.symbol} at market? ` +
        `This will submit a ${closeTarget.side === "short" ? "buy" : "sell"} order ` +
        `to ${account?.broker_type ?? "the broker"}.` +
        (closeError ? `\n\nError: ${closeError}` : "")
      : ""
  }
  confirmLabel={closePos.isPending ? "Closing…" : "Close position"}
  cancelLabel="Cancel"
  onConfirm={onConfirmClose}
  onCancel={() => { setCloseTarget(null); setCloseError(null); }}
/>
```

- [ ] **Step 2: Type-check**

Run: `cd dashboard && npx tsc --noEmit`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/pages/AccountDetail.tsx
git commit -m "feat(dashboard): Close button + confirm dialog on AccountDetail positions table"
```

---

## Task 9: Frontend — tests for the close flow

**Files:**
- Create: `dashboard/src/pages/AccountDetail.test.tsx`

- [ ] **Step 1: Write the test file**

Create `dashboard/src/pages/AccountDetail.test.tsx` with:

```typescript
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const closeMutate = vi.fn();

vi.mock("../api/hooks", () => ({
  useAccount: () => ({
    data: {
      id: "ac1",
      name: "Alpaca Test",
      broker_type: "alpaca",
      environment: "paper",
      supported_asset_types: ["equities"],
      pdt_mode: "off",
    },
    isLoading: false,
  }),
  useBrokerInfo: () => ({
    data: {
      account_info: { cash: 1000, equity: 1500 },
      positions: [
        {
          symbol: "SPY", quantity: 5, side: "long",
          avg_price: 500, current_price: 521.23,
          unrealized_pnl: 106.15, market_value: 2606.15,
        },
      ],
    },
    isLoading: false,
    error: null,
  }),
  useClosePosition: () => ({
    mutate: closeMutate,
    isPending: false,
  }),
  // All other hooks AccountDetail consumes — stubbed with empty/no-op defaults
  // so the page renders. Add fields here if a future page change needs them.
  useAccountEquityCurve: () => ({ data: [], isLoading: false }),
  useAccountTrades: () => ({ data: { items: [] }, isLoading: false }),
  useAllInstances: () => ({ data: [], isLoading: false }),
  useCashFlows: () => ({ data: [], isLoading: false }),
  useCreateCashFlow: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteAccount: () => ({ mutate: vi.fn(), isPending: false }),
  useSyncAccount: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateAccount: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("../stores/ui", () => ({
  useUIStore: (selector: (s: { addAlert: (a: unknown) => void }) => unknown) =>
    selector({ addAlert: vi.fn() }),
}));

import { AccountDetail } from "./AccountDetail";

function renderPage() {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/accounts/ac1"]}>
        <Routes>
          <Route path="/accounts/:id" element={<AccountDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AccountDetail — close position", () => {
  it("renders a Close button for each position row", () => {
    renderPage();
    expect(screen.getByRole("button", { name: /close/i })).toBeInTheDocument();
  });

  it("opens the confirm dialog with position details when Close is clicked", () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(screen.getByText(/close 5 long spy at market/i)).toBeInTheDocument();
    expect(screen.getByText(/sell order to alpaca/i)).toBeInTheDocument();
  });

  it("fires useClosePosition.mutate with the row's symbol/side/qty on confirm", async () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    fireEvent.click(screen.getByRole("button", { name: /close position/i }));
    await waitFor(() => expect(closeMutate).toHaveBeenCalledTimes(1));
    expect(closeMutate.mock.calls[0][0]).toMatchObject({
      symbol: "SPY",
      side: "long",
      quantity: 5,
      asset_type: "equities",
    });
  });
});

describe("AccountDetail — close position (pending state)", () => {
  it("does not fire the mutation again while a previous close is in flight", async () => {
    closeMutate.mockClear();
    // Re-mock useClosePosition for this block with isPending: true to simulate
    // an already-in-flight close.
    vi.doMock("../api/hooks", async () => {
      const base = await vi.importActual<Record<string, unknown>>("../api/hooks");
      return {
        ...base,
        useClosePosition: () => ({ mutate: closeMutate, isPending: true }),
      };
    });
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    fireEvent.click(screen.getByRole("button", { name: /closing/i }));
    // Mutation should NOT fire — the onConfirmClose handler early-returns
    // while isPending. (mockClear above means count starts at 0.)
    expect(closeMutate).not.toHaveBeenCalled();
  });
});
```

The mock above lists every hook `AccountDetail` currently consumes from `../api/hooks`. If the page is later extended to use additional hooks, vitest will surface `useFoo is not a function` — add a stub to the mock for each missing one.

- [ ] **Step 2: Run the tests**

Run: `cd dashboard && npx vitest run src/pages/AccountDetail.test.tsx`
Expected: all 3 PASS.

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/pages/AccountDetail.test.tsx
git commit -m "test(dashboard): close-position flow on AccountDetail"
```

---

## Task 10: Manual smoke test

**Files:** none.

- [ ] **Step 1: Build and restart**

```bash
cd dashboard && npm run build && cd ..
quilt coord restart
```

- [ ] **Step 2: Verify in browser**

1. Open `http://localhost:8000/accounts/<alpaca-test-account-id>`
2. Scroll to the Positions section — confirm the new "Close" button appears at the end of each row.
3. Click Close on one position. Confirm dialog appears with the right text (qty, side, symbol, broker name).
4. Click Cancel — dialog dismisses, no order placed (verify in Alpaca Test).
5. Click Close again, then Confirm. Wait ~2 seconds. The row should disappear from the table (broker-info refetches and the position is gone from Alpaca).
6. If the position was opened via Quilt earlier (i.e. has an internal `Position` row), verify in the DB:

```bash
sqlite3 data/quilt_trader.db "SELECT id, status, closed_at FROM positions WHERE account_id = '<alpaca-test-account-id>' ORDER BY opened_at DESC LIMIT 5;"
sqlite3 data/quilt_trader.db "SELECT symbol, side, quantity, filled_price, source FROM trade_log WHERE account_id = '<alpaca-test-account-id>' ORDER BY timestamp DESC LIMIT 5;"
```

Expected: the Position row shows `status=closed` with a non-null `closed_at`; a `trade_log` row exists with `source=manual`, the opposite side, and the filled price from the close order.

- [ ] **Step 3: Verify in Alpaca**

Log into Alpaca's paper-trading dashboard and confirm the closing order appears in Order History and the position quantity is now zero.

---

## Self-review

**Spec coverage check:**

| Spec requirement | Implemented in |
|---|---|
| `POST /api/accounts/{account_id}/positions/close` endpoint | Task 1 |
| `side: long/short` inversion to order side | Tasks 1, 2 |
| Resolve broker adapter; call `submit_order` market | Task 1 |
| Update internal `Position` to `closed` + write closing `TradeLog` | Task 3 |
| No internal record → still succeed, only submit broker order | Task 4 |
| Skip `locked_by` check | Task 1 (omitted by design), Task 6 (regression test) |
| Adapter failure → 500 with broker message | Task 5 |
| Missing account → 404 | Task 5 |
| `useClosePosition` mutation hook + client method | Task 7 |
| AccountDetail action column with Close button | Task 8 |
| Confirm dialog with formatted message | Task 8 |
| Don't double-fire while in flight | Task 8 — `onConfirmClose` early-returns if `closePos.isPending`; label switches to "Closing…" for feedback. ConfirmDialog itself doesn't visually disable the button, but the mutation can't fire twice. |
| Error display in dialog | Task 8 (`closeError` appended to message) |
| Refetch broker-info on success | Task 7 (`useClosePosition` invalidates `["accounts", id, "broker-info"]`) |
| Frontend tests: button per row, dialog, mutation fires | Task 9 |

**Out-of-scope items deferred to `docs/superpowers/backlog.md`:**
- Multi-leg / spread-aware close
- Partial close
- Limit/stop close orders
- Bulk close-all
- Coordinated manual-close with running algorithm
