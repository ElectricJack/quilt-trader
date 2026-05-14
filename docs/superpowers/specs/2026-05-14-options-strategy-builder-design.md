---
title: Options Strategy Builder & Visualizer
status: design
date: 2026-05-14
---

# Options Strategy Builder & Visualizer — Design

A dedicated tool for **evaluating and learning about options spreads, then submitting them as orders on a specific account**. Inspired by optionstrat.com but scoped tighter: no named-strategy library, no separate workspace. The builder is bound to an account so its chain, pricing, and submission target are unambiguous. State persists per-account across page closes (so you can submit one, tweak, resubmit; or step away and come back).

## Goals

1. Explore option chains for a chosen underlying + expiry on the account's broker.
2. Pick from a template list (Long Call, Vertical, Iron Condor, Custom, …) or build from scratch.
3. Visualize P&L at expiry and at any intermediate date via a scrub-date slider.
4. See aggregate Greeks (Δ, Γ, Θ, V) and cost / max profit / max loss / breakevens update live.
5. Submit the constructed spread as a real order through Spec A's `POST /api/accounts/{id}/positions/open` endpoint.

## Non-Goals

- Saved strategies / strategy library (multiple named strategies you manage and switch between). The builder remembers **one in-progress strategy per account**, but anything richer — naming, cataloging, deleting from a list — is a follow-up.
- Algorithm-style automated execution. This is manual order entry with rich pre-trade analysis.
- Probability-of-profit shading on the chart. (Tracked as a follow-up — see §6.)
- Drag-on-chart strike editing. (Tracked as a follow-up.)
- Multi-account simultaneous submission. One builder = one account.
- Replacing Spec A's "Open Position" modal. That modal stays for equities/crypto/single-leg orders; the builder is the options-spread path.

## Dependencies

- **Spec A** must land first or alongside: Strategy Builder's "Submit as Order" calls Spec A's endpoint and relies on its native multi-leg atomic submission (`AlpacaAdapter.submit_multileg_order`, `TradierAdapter.submit_multileg_order`).
- Spec B is independent; the builder uses **broker chain APIs**, not the historical/live market data subsystem.

---

## 1. Routing & navigation

- New sub-route `/accounts/:id/strategies`. Lives under AccountDetail in `dashboard/src/App.tsx`. The page is account-scoped — broker, creds, and order target are fixed by the URL.
- A new **Strategies** button on AccountDetail's header (next to Refresh / Sync / Edit / Delete) opens it. Disabled with a tooltip when the account doesn't have `"options"` in `supported_asset_types`.
- A back arrow (mirroring `/accounts/:id/instances` patterns) returns to AccountDetail.
- The route is hidden when `account.locked_by` is set; the "Strategies" button on AccountDetail is disabled with the same lock-tooltip Spec A uses ("Locked by algorithm [instance link]. Stop the algo to open positions manually."). Submission is the destructive operation; analysis-only mode would be a future enhancement but isn't useful without submission.

## 2. Page layout

```
┌──────────────────────────────────────────────────────────────────────┐
│  [←]  Strategies — {account.name}                       [Submit Order]│
├──────────────────────────────────────────────────────────────────────┤
│ Underlying: [SPY ▾]    Template: [Vertical Spread ▾]   Expiry: [▾]   │
├───────────────────────────────────┬──────────────────────────────────┤
│  Legs                             │  Chain (collapsible)              │
│  ┌──────────────────────────────┐ │  strike  call b/a    put b/a      │
│  │ buy  call 560  ×1  $8.20/8.40│ │   540    22.0/22.3  0.2/0.4       │
│  │ sell call 570  ×1  $4.10/4.30│ │   560 ★  8.2/8.4    1.1/1.3       │
│  │ [+ add leg]                  │ │   570 ★  4.1/4.3    2.0/2.2       │
│  └──────────────────────────────┘ │   580    1.8/2.0    4.5/4.7       │
├───────────────────────────────────┴──────────────────────────────────┤
│  ┌──── P&L chart ──────────────────────────────────┐ ┌── Greeks ───┐ │
│  │                                                │ │ Δ  +0.32     │ │
│  │      ───── at-expiry curve (faint) ─────       │ │ Γ  +0.018    │ │
│  │              ╭──────╮ ←── at-date curve         │ │ Θ  -12.40    │ │
│  │             ╱        ╲                          │ │ V  +45.20    │ │
│  │       ─────╯ spot          ╲────                │ │              │ │
│  │                                                │ │ Cost $245     │ │
│  └────────────────────────────────────────────────┘ │ Max P $755    │ │
│   [Today |═══●═══════════| Expiry]  5 days from now │ Max L $245    │ │
│                                                     │ B/E  564.50   │ │
│                                                     └───────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

Components (new files unless noted):
- `dashboard/src/pages/Strategies.tsx` — page shell, owns top-level state.
- `dashboard/src/components/strategy/UnderlyingPicker.tsx` — symbol input, validates against the account's `supported_asset_types`.
- `dashboard/src/components/strategy/TemplatePicker.tsx` — dropdown of templates listed in §3.
- `dashboard/src/components/strategy/ExpiryPicker.tsx` — dropdown of expiries returned by the chain API.
- `dashboard/src/components/strategy/LegsTable.tsx` — editable rows; each row binds an `OptionLeg` (see §3).
- `dashboard/src/components/strategy/ChainBrowser.tsx` — chain table; clicking a strike promotes it into the legs table.
- `dashboard/src/components/strategy/PnlChart.tsx` — extends the existing `PriceChart` pattern; renders two series (at-expiry faint, at-date solid) with breakeven / max P/L markers.
- `dashboard/src/components/strategy/DateSlider.tsx` — slider with "today" and "expiry" anchors; emits the scrub date.
- `dashboard/src/components/strategy/GreeksPanel.tsx` — Δ Γ Θ V + cost / max P / max L / breakevens.
- `dashboard/src/lib/options.ts` — pure Black-Scholes + Greeks + payoff math (see §5).

## 3. Strategy model & templates

### Client-side leg type

```typescript
type OptionLeg = {
  id: string;                 // local uuid for React keys
  side: "buy" | "sell";
  right: "call" | "put";
  strike: number;
  expiry: string;             // YYYY-MM-DD
  quantity: number;           // contracts
  bid: number;                // snapshot at last chain fetch
  ask: number;
  iv: number;                 // implied vol, decimal (e.g. 0.32 = 32%)
};
```

This isn't persisted — it's React state on the Strategies page. On Submit, it's translated to Spec A's `LegSpec`.

### Templates v1

| Template          | Legs                                                  |
|---                |---                                                    |
| Long Call         | 1: buy call                                           |
| Long Put          | 1: buy put                                            |
| Short Call        | 1: sell call (unprotected; flag risk in UI)           |
| Short Put         | 1: sell put                                           |
| Bull Call Spread  | buy call @ lower strike, sell call @ higher           |
| Bear Call Spread  | sell call @ lower, buy call @ higher                  |
| Bull Put Spread   | sell put @ higher, buy put @ lower                    |
| Bear Put Spread   | buy put @ higher, sell put @ lower                    |
| Straddle          | buy call + buy put, same strike & expiry              |
| Strangle          | buy call @ OTM up + buy put @ OTM down                |
| Iron Condor       | bull put spread + bear call spread                    |
| Iron Butterfly    | sell ATM straddle + buy OTM call + buy OTM put        |
| Calendar Spread   | sell near-expiry option + buy far-expiry, same strike |
| Custom            | starts empty; user adds legs manually                 |

Templates are pure functions: `templateName -> (chain, spot, expiry) -> OptionLeg[]`. The picker calls one and the result replaces the current legs. Editing legs afterward is unrestricted (no enforcement that you "stay" inside a template).

### Per-account state persistence

The builder remembers its in-progress state per account, scoped to the browser. Use case: submit a vertical spread, glance at the resulting position, come back and tweak strikes to submit a second variant; or close the tab to research overnight and resume tomorrow.

**Storage:** `localStorage` key `quilt.strategyBuilder.{accountId}`. Per-account isolation prevents one account's strategy from bleeding into another's view.

**What's persisted (structural state only):**

```typescript
type PersistedBuilderState = {
  version: 1;
  underlying: string | null;
  template: string | null;             // last picked template name
  legs: Array<{
    side: "buy" | "sell";
    right: "call" | "put";
    strike: number;
    expiry: string;
    quantity: number;
  }>;
  scrubDateOffsetMs: number | null;    // slider position relative to earliest expiry; null = "today"
  savedAt: number;                     // epoch ms
};
```

**Not persisted:** bid / ask / IV / Greeks on each leg. Those are live market data and stale immediately — they're re-fetched from the chain on hydrate. A leg can be hydrated with `bid: undefined, ask: undefined` and rendered with an "as of last refresh" indicator until the chain populates them.

**Lifecycle:**

- **Save** — debounced to ~500ms on every state change in the builder. The `version` field lets us migrate the shape later if needed.
- **Hydrate** — on `Strategies.tsx` mount: read the key, validate against the current shape, populate legs/underlying/expiry/template state. Then trigger a chain fetch to re-attach live pricing. If validation fails (corrupt entry, stale schema) silently fall back to empty state.
- **Auto-prune** — entries older than 30 days are ignored on hydrate (their `savedAt` is too old to be meaningful; user has likely moved on). Removed from storage on the next save.
- **Stale-expiry handling** — on hydrate, drop any leg whose `expiry` is in the past. If all legs drop, fall back to empty state. Display a one-time toast: "Removed N expired legs from your saved strategy."
- **Manual reset** — a "Reset builder" button in the page header clears the key and resets to empty state. Confirmation dialog ("Discard current strategy?") because the state can represent real research effort.
- **After-submit behavior** — successful submission does NOT clear the state. The legs stay so the user can immediately tweak and resubmit. (Submission feedback in the result modal makes it clear the order went through; the state is the "draft you might iterate on" — different concept.)

**Why localStorage and not the server:**

- One dashboard, one machine for this user. Cross-device sync isn't a requirement.
- Avoids a new DB table, migration, API endpoints, and reconcile logic — all of which would expand the spec's surface area without adding capability the user asked for.
- If we later want cross-device sync or a multi-strategy library, the persisted shape above is a clean unit to lift into a server-backed model.

### Backwards-compat note

Spec A's order ticket has a `strategy_type` label on the request body. The builder passes the picked template's name (lowercased, underscore-joined: `"vertical_bull_call"`, `"iron_condor"`, etc.) as that label so the resulting `Position` row carries the strategy intent for later display. If the user has edited away from the template into something unrecognized, `strategy_type` falls back to `"custom"`.

## 4. Broker chain API

### Backend

New router `coordinator/api/routes/options_chain.py` mounted at `/api/accounts/{account_id}/options-chain`:

| Method | Path | Query | Notes |
|---|---|---|---|
| `GET` | `/expiries?underlying={symbol}` | — | List of YYYY-MM-DD expirations the broker has chains for. |
| `GET` | `/{expiry}?underlying={symbol}` | `expiry` is YYYY-MM-DD | Returns the chain: per-strike call+put with bid/ask/last/iv/Greeks. |

Both endpoints honor `account.locked_by` (423 if locked).

### BrokerAdapter additions

```python
class BrokerAdapter(ABC):
    def list_option_expiries(self, underlying: str) -> list[date]:
        """Return available option expirations for the underlying."""
        raise NotImplementedError

    def get_option_chain(self, underlying: str, expiry: date) -> "OptionChainSnapshot":
        """Return the full chain for one expiry."""
        raise NotImplementedError
```

```python
@dataclass
class OptionContract:
    strike: float
    right: str             # "call" | "put"
    occ_symbol: str        # broker's full OCC-style symbol
    bid: float | None
    ask: float | None
    last: float | None
    iv: float | None       # implied vol, decimal
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    open_interest: int | None
    volume: int | None

@dataclass
class OptionChainSnapshot:
    underlying: str
    spot: float                       # current underlying price
    expiry: date
    contracts: list[OptionContract]   # both calls and puts, sorted by strike
    as_of: datetime                   # quote timestamp from broker
```

- `TradierAdapter.list_option_expiries` → GET `/v1/markets/options/expirations`.
- `TradierAdapter.get_option_chain` → GET `/v1/markets/options/chains` (Tradier returns full chain w/ Greeks computed server-side).
- `AlpacaAdapter.list_option_expiries` → GET `/v1beta1/options/contracts?underlying_symbol=...` (paginate).
- `AlpacaAdapter.get_option_chain` → GET `/v1beta1/options/snapshots/{underlying}` (filtered by expiry); Greeks may be `None` (Alpaca doesn't always return them; the client falls back to its own Black-Scholes).

Both adapters expose Δ/Γ/Θ/V on the contract when the broker provides them; the client uses broker Greeks when present and falls back to computed Greeks otherwise.

### Caching

A small `dashboard/src/api/hooks.ts` hook `useOptionChain(accountId, underlying, expiry)` uses React Query with a 30-second stale time. Cache is cleared on:
- Underlying or expiry change.
- Explicit refresh button click.
- Window/tab refocus (React Query's default `refetchOnWindowFocus`).

The header shows `as of HH:MM:SS` and a refresh button.

## 5. Client-side math (`dashboard/src/lib/options.ts`)

Pure TypeScript. Runs on every state change in the builder — no server hop, no debounce needed (the math is microseconds).

### Black-Scholes

```typescript
function bsCall(S: number, K: number, T: number, r: number, sigma: number): number;
function bsPut(S: number, K: number, T: number, r: number, sigma: number): number;
function greeks(side, right, S, K, T, r, sigma): { delta, gamma, theta, vega };
```

- `S` = spot, `K` = strike, `T` = years to expiry (positive float), `r` = risk-free rate (default 0.04, configurable later), `sigma` = IV (decimal).
- Standard normal CDF / PDF via inline approximations (Abramowitz & Stegun 7.1.26 — small, accurate to ~7 decimal places, ~10 lines of code).

### Payoff aggregation

```typescript
function legCostAtExpiry(leg: OptionLeg, S: number): number;
function legCostAtDate(leg: OptionLeg, S: number, dateMs: number): number;
function strategyPnl(legs: OptionLeg[], S: number, dateMs: number | "expiry"): number;
function strategyGreeks(legs: OptionLeg[], S: number, dateMs: number): GreeksSum;
```

- At expiry: intrinsic value × side × quantity − entry cost.
- At date: Black-Scholes value with `T = (expiry − dateMs) / yearInMs`, summed across legs, minus entry cost.
- Entry cost: each leg is priced at the **mid of bid/ask** when those are available; falls back to `last`; otherwise 0 (and the cost panel shows "—" with a note).

### Chart series generation

```typescript
function pnlCurve(
  legs: OptionLeg[],
  spotRange: [number, number],   // typically [spot * 0.7, spot * 1.3]
  dateMs: number | "expiry",
  steps: number = 200,
): { x: number; y: number }[];
```

`PnlChart.tsx` calls this twice (once for expiry, once for the selected scrub date) and renders both series on the same lightweight-charts instance.

### Breakevens / max P / max L

Computed by sampling the at-expiry curve at `steps = 1000`, finding the contiguous P/L > 0 and < 0 regions, and reporting:
- **Breakevens** = x-values where the at-expiry curve crosses 0.
- **Max profit** / **Max loss** = max/min of the at-expiry curve over the sampled range (with a sentinel "unlimited" displayed when the curve is unbounded — e.g. a naked short call).

## 6. Submit flow

1. User clicks **Submit Order** (top-right of the page).
2. Confirmation modal summarizes: cost, max P / max L / breakevens / Greeks at spot today.
3. On confirm, the client builds a Spec A `OpenPositionRequest`:
   ```ts
   {
     legs: legs.map(l => ({
       symbol: underlying,         // Spec A handles OCC composition per-broker
       asset_type: "options",
       side: l.side,
       quantity: l.quantity,
       expiry: l.expiry,
       strike: l.strike,
       right: l.right,
     })),
     strategy_type: templateLabel, // see §3
     order_type: "limit",          // see below
     limit_price: estimatedNetCost,
   }
   ```
4. POST to `/api/accounts/{account_id}/positions/open`. Spec A's adapter logic dispatches to native multi-leg atomic submission when supported.
5. Result modal renders Spec A's per-leg response. On success, page navigates back to AccountDetail.

### Order type default

The submit dialog defaults to **limit at the current estimated net cost** (mid-of-bid/ask sum across legs). Reason: market orders on multi-leg options are dangerous — slippage between legs can be brutal. The user can override to market or adjust the limit. The limit input is pre-filled with the same number shown in the cost panel; "Use mid" / "Use natural" (bid for long legs, ask for short legs — the worst-case fill) buttons offer one-click adjustments.

## 7. Edge cases & error handling

- **Chain empty for the chosen expiry.** Tradier returns `null` for some far-dated chains; show "No chain available for {expiry}" and disable the legs table until a valid expiry is picked.
- **Broker returns no IV** for a contract. Mark the leg's IV cell with a warning icon; cost-at-date math substitutes a default vol of 30% and the GreeksPanel labels values as "approximate."
- **Negative time-to-expiry** (date slider past expiry). Clamp the slider to expiry-minus-one-millisecond. Past-expiry analysis is meaningless.
- **One leg's expiry differs from the others** (calendar/diagonal). Allowed; the date slider's max is the **earliest** of the leg expiries. After that date the near leg has expired; the math collapses to intrinsic for the expired leg + Black-Scholes for the rest.
- **Limit price isn't acceptable to the broker** (e.g. outside their step / spread rules). Surfaced via Spec A's per-leg error path; the result modal shows the broker's exact message.

## 8. Cross-cutting concerns

### Database migrations

None. Strategies aren't persisted.

### Tests

- **`dashboard/src/lib/options.test.ts`**: Black-Scholes against published reference values (e.g., Hull textbook examples); payoff curves against hand-computed values for each template; breakeven detection for vertical spreads & condors.
- **`coordinator/api/routes/options_chain.py` tests**: mock broker adapter returning fixed chain, assert API shape; 423 when account locked; 422 when account doesn't support options.
- **Template generators**: `templates.test.ts` — given a chain + spot + expiry, each template emits the documented shape (right number of legs, correct sides, correct strike relationships).
- **Frontend component test for Strategies page**: drives the full flow with a fake account + fake chain, asserts the chart redraws when the slider moves and that "Submit Order" sends the right request body.
- **Persistence test**: builds a strategy, unmounts and remounts the page, asserts state hydrates correctly. Separate test for stale-expiry pruning (a leg with an expiry in the past is dropped on hydrate with a toast).
- **Manual smoke** against a real Tradier paper account: open the Strategies tab, build a vertical spread, submit, verify the spread shows up as one atomic broker order with a parent ID.

### Performance budget

- Black-Scholes math: thousands of calls per second on the main thread, fine. Re-rendering the chart at each slider step is the dominant cost; lightweight-charts handles this gracefully.
- Chain fetch: bounded by broker latency (~200-800ms typical). Display a spinner; don't block the UI on legs that are already configured.

### Implementation order

1. **BrokerAdapter additions** (`list_option_expiries`, `get_option_chain`) + Tradier impl + tests with mocked HTTP.
2. **Chain API endpoints** (`/api/accounts/{id}/options-chain/*`) + tests.
3. **Client math library** (`dashboard/src/lib/options.ts`) + tests. Self-contained; can land first.
4. **Strategies page skeleton** (route, layout, header) + template picker + legs table editing.
5. **Chain browser** + click-to-promote.
6. **PnL chart + date slider + Greeks panel**.
7. **Submit flow** (depends on Spec A being merged).
8. **Alpaca adapter** for chain/expiries.
9. **Polish pass**: error states, empty states, lock state, IV warnings.

Each step lands in its own PR. Step 7 explicitly depends on Spec A; steps 1-6 + 8 are independent and can interleave.

## 9. Tracked follow-ups (intentionally out of scope)

- **Saved strategies / strategy library.** A multi-strategy manager — name, list, switch between, delete. Builds on the persistence shape from §3 but adds the management UI and (likely) server-side storage for cross-device sync.
- **Probability-of-profit shading** on the chart (background bands from IV-implied price distribution).
- **Drag-on-chart strike editing.** Move strike markers via mouse drag; snap to chain.
- **What-if IV adjustment.** Slider to test "what if IV moves ±5% before expiry?" — needs the math to take IV per leg as a free parameter.
- **Multi-account submission.** Same strategy fanned out across multiple accounts.
- **Historical IV chart** for the underlying — provides "is today's vol high or low?" context. Would draw from Spec B's data subsystem.
