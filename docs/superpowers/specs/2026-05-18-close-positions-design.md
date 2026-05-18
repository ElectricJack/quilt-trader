# Close Positions From Account Page — Design

## Problem

An open broker position visible on the AccountDetail page has no UI control to close it. Today the positions table is read-only. If a user opens a position manually (via the existing open-position flow) or directly in their broker (e.g. Alpaca), the only way to close it is to leave Quilt and use the broker's own UI. This is a usability gap and a safety gap — there is no in-app way to bail out of a position if something is going wrong.

## Goal

Add a per-row "Close" action to the positions table on the AccountDetail page. Clicking it submits an opposite-side market order through the broker adapter and refreshes the table.

## Non-goals

- Limit/stop-limit close orders. Market only for v1.
- Partial close. The action closes the full quantity shown in the row.
- Multi-leg position close. Single-leg only for v1.
- Bulk "close all" action.
- Closing positions on accounts that don't have a wired broker adapter.

## Design

### Identification: close by broker symbol

The button on each row submits a request keyed by the broker-visible position (symbol, asset_type, side, quantity). This is independent of whether Quilt has an internal `Position` row for that symbol. Anything visible in the broker's position list is closable.

If an internal `Position` row matches the symbol on this account and is in status `open`, we mark it closed as a side effect of a successful close. If no such row exists, we just submit the broker order.

### Backend

**New endpoint:** `POST /api/accounts/{account_id}/positions/close`

Request body:
```json
{
  "symbol": "AAPL",
  "asset_type": "equity",
  "side": "long",
  "quantity": 5
}
```

`side` is the *position* side, not the order side. The handler inverts it.

Handler steps:

1. Look up the account. 404 if missing.
2. Resolve the broker adapter for the account. 400 if no adapter is wired (e.g. account has no credentials configured).
3. Compute closing order side: `long → sell`, `short → buy`.
4. Call `adapter.submit_order(symbol=..., quantity=..., side=..., asset_type=..., order_type="market")`.
5. If any internal `Position` rows match `(account_id, symbol, side, status="open")`, mark them `closed`, set `closed_at`, and persist a closing trade row pointing at the broker's order/fill ids.
6. Return `{ broker_order_id, status, filled_price }`. `status` is whatever the adapter reports (`filled`, `pending`, `rejected`).

**Lock-check policy:** the close endpoint deliberately *skips* the `locked_by` check that the open-position endpoint enforces. Closing must work even when an algorithm is currently running on the account — it is a safety valve. Any cleanup with respect to the running algo (e.g. the algo trying to manage a position that is no longer there) is out of scope for this endpoint; the algo will see the position disappear on its next broker sync.

**Idempotency:** the endpoint is not idempotent. Submitting it twice will submit two opposing orders. This matches the behavior of the existing open-position endpoint and is acceptable behind a confirmation dialog. A double-click guard on the frontend (button disabled while the mutation is in flight) is sufficient.

### Frontend

**New hook:** `useClosePosition(accountId)` in `dashboard/src/api/hooks.ts`, mirrors `useOpenPosition`. On success, invalidates `["brokerInfo", accountId]` so the positions table refetches.

**AccountDetail positions table:**
- Add a trailing action column.
- Each row gets a small destructive-styled `Close` button.
- Click → `ConfirmDialog` with body: `"Close <qty> <side> <symbol> at market? This will submit a <order-side> order to <broker>."`
- Confirm → fire the mutation. Button is disabled while the mutation is in flight.
- On success: dialog closes, react-query refetches, the row disappears (or quantity updates if the close partially filled).
- On error: dialog stays open and shows the error message inline.

### Failure modes

| Case | Behavior |
|---|---|
| Adapter raises (network, rate limit, broker rejection) | 500 with the broker's message; frontend shows it inline in the dialog. |
| Account has no broker credentials | 400 `broker_not_configured`. Button should still appear but the request fails fast. |
| Quantity changed on the broker between the table render and the click (e.g. an algo already partially closed) | We submit for the quantity the user saw. If the broker rejects, the error surfaces. We do *not* re-fetch to "true up" the quantity first. |
| Pending fill | Endpoint returns with `status: "pending"`. UI shows it as a transient state; the refetch will pick up the new broker state shortly. |
| Multi-leg position (e.g. options spread leg shown as a single row) | Out of scope for v1. The Close button on such a row will submit a single-leg opposing order; if that fails the user sees the broker's error. This is acceptable because v1 is documented as single-leg only. |

## Tests

**Backend (`tests/coordinator/test_positions_close.py`):**
- Long position close → adapter receives a `sell` order with the correct symbol, qty, and asset_type.
- Short position close → adapter receives a `buy` order.
- Internal `Position` row matching the close gets marked `closed` with `closed_at` set and a closing trade row persisted.
- Close request with no matching internal `Position` row succeeds and only submits the broker order (no DB writes other than the trade row, if any).
- Adapter raising surfaces as a 500 with the adapter's error message in the response body.
- 404 when account is missing; 400 when broker adapter not configured.
- Endpoint does *not* check `locked_by` — succeeds even when the account has an active algorithm lock.

**Frontend (`dashboard/src/pages/AccountDetail.test.tsx`):**
- Positions table renders the Close button per row.
- Clicking Close opens the confirm dialog with the expected text.
- Confirming calls the close mutation with the row's symbol/side/qty.
- While the mutation is in flight, the confirm button is disabled.
- On mutation success, `["brokerInfo", accountId]` is invalidated.
- On mutation error, the error message is shown in the dialog and the dialog stays open.

## Out-of-scope follow-ups

These are explicitly deferred and not part of this spec:

- Limit/stop close orders.
- Partial close (user enters a smaller quantity).
- "Close all" bulk action.
- Multi-leg / spread-aware closes.
- Coordinating with an algorithm's own position tracking so the algo doesn't immediately try to reopen the position. Today the algo will see the position disappear on its next sync; that is acceptable for v1.
