# Congress Copy-Trading Algorithm — Design

**Date:** 2026-06-19
**Status:** Approved design, pending implementation plan
**Package:** `data/packages/congress-copytrader/`

## Goal

A trading algorithm that mirrors a configurable whitelist of members of Congress.
For each tracked member it expresses every disclosed position as a *percentage of
that member's own disclosed portfolio*, combines those percentages across members
per symbol into a single conviction figure, and rebalances our own equity book
daily to match — buying when Congress buys and trimming/exiting when Congress sells.

The defining idea: weight by **share-of-portfolio conviction**, not raw disclosed
dollars, so a wealthy member trading large dollar amounts does not dominate the
basket purely by virtue of being rich. A member putting 5% of their book into a
name counts as conviction `0.05` regardless of their absolute wealth.

## Data foundation

The algorithm consumes the already-shipped bitemporal disclosure datasets:

- `fmp.house_disclosures` — endpoint `/stable/house-latest`
- `fmp.senate_disclosures` — endpoint `/stable/senate-latest`

Both are `symbol_keyed=False` firehoses (transactions for all members), read via
`ctx.dataset(name, ...)`, which enforces point-in-time correctness by filtering
`knowledge_date (= disclosureDate) <= timestamp - lag`. No lookahead is possible.

Relevant columns: `symbol`, `firstName`, `lastName`, `transactionDate`,
`disclosureDate`, `type` (Purchase / Sale / Sale (Partial) / ...), `amount`
(a fixed-bucket range string such as `"$1,001 - $15,000"`).

### Known data limitation (out of scope to fix here)

Free-tier FMP `*-latest` is a shallow firehose with limited history, so deep
multi-year backtests are data-limited until a paid tier or a historical backfill
exists. This is documented, not solved, by this spec. See the Backlog section.

## Architecture

A single self-contained algorithm package. No coordinator/SDK changes are
required by the core design (one open verification item below).

```
data/packages/congress-copytrader/
  quilt.yaml        # manifest
  algorithm.py      # CongressCopyTrader(QuiltAlgorithm)
  requirements.txt  # empty (pandas already available to algorithms)
```

### Manifest (`quilt.yaml`)

```yaml
name: congress-copytrader
type: algorithm
version: 1.0.0
description: Mirrors whitelisted members of Congress, weighting each name by combined
  share-of-portfolio conviction, rebalanced daily.
entry_point: algorithm.py
class_name: CongressCopyTrader
trigger: bar:1day          # anchor tick: one rebalance per trading day
requirements:
  asset_types: [equities]
data:
  - source: fmp.house_disclosures
    type: dataset
  - source: fmp.senate_disclosures
    type: dataset
config:
  parameters:
    - name: members          # whitelist; empty list = track everyone
      type: string           # comma-separated "Last, First" (or just "Last")
      default: ""
    - name: chambers         # "house", "senate", or "both"
      type: string
      default: both
    - name: history_days     # window of disclosure history to recompute over (0 = all)
      type: integer
      default: 0
    - name: min_member_history_usd   # floor: ignore members with tiny disclosed books
      type: integer
      default: 50000
    - name: max_weight       # per-name concentration cap
      type: float
      default: 0.25
    - name: min_rebalance_pct  # drift band to suppress churn
      type: float
      default: 0.02
    - name: pct_invest       # fraction of equity deployed (cash buffer = 1 - this)
      type: float
      default: 0.95
    - name: disclosure_lag_days  # extra safety lag on knowledge_date
      type: integer
      default: 0
assets:
  - symbol: SPY              # anchor only — drives the daily tick + market-open calendar
    asset_class: equities
    timeframe: 1day
    source: polygon
```

Notes:

- **`assets:` holds only SPY.** The traded universe is dynamic (Congress trades an
  arbitrary, growing set of symbols), so SPY is purely the heartbeat — its daily
  bar close drives the rebalance tick, and it anchors the NYSE trading calendar.
  Every actually-traded symbol is resolved at runtime via `ctx.market_data(symbol)`.
- **`data:` block with `type: dataset` — OPEN VERIFICATION ITEM.** The manifest
  parser documents `data:` block `type` values as `{scraper, csv, json, parquet}`.
  Datasets are registered in the coordinator registry and `ctx.dataset()` resolves
  by name independent of the manifest. During planning, verify whether declaring
  datasets in the manifest is required/supported. If it is not, omit the `data:`
  block entirely and rely on the direct `ctx.dataset("fmp.house_disclosures")`
  call. The core design does not depend on the manifest declaration.

## Core computation (`on_tick`)

`on_tick` is a **pure function** of `(date, config)` → `{symbol: target_dollars}`.
No state is persisted (Approach 1: stateless recomputation). The bitemporal
`dataset()` call already guarantees point-in-time correctness, so persisted state
would be pure liability. Same inputs always produce the same basket.

### Step 1 — Pull point-in-time disclosures

```
rows = []
for chamber in selected_chambers:        # from `chambers` config
    df = ctx.dataset(
        f"fmp.{chamber}_disclosures",
        start = None if history_days == 0 else (today - history_days),
        lag   = timedelta(days=disclosure_lag_days),
    )
    rows.append(df)
disc = concat(rows)
```

### Step 2 — Filter to whitelist & parse

- Keep rows whose `(lastName, firstName)` is in `members`. Empty whitelist ⇒ keep
  all members. Matching is case-insensitive; `firstName` is optional in the config
  entry (last-name-only matches any first name).
- Map `amount` range string → midpoint USD via a fixed parse table covering FMP's
  standard buckets (e.g. `"$1,001 - $15,000"` → `8000`). Blank or unrecognized
  bucket → skip the row and log.
- Map `type` → sign: any "Purchase" → `+midpoint`; any "Sale" (including
  "Sale (Partial)" / "Sale (Full)") → `−midpoint`. Unknown type → skip + log.

### Step 3 — Per-member normalization

```
for each member m:
    book_m = Σ over all of m's rows of signed midpoint    # estimated disclosed portfolio
    if book_m < min_member_history_usd: drop member m     # noise floor
    for each symbol s held by m:
        net_{m,s}   = Σ signed midpoints of m in s
        share_{m,s} = max(net_{m,s}, 0) / book_m          # % of THEIR book in s
```

A name a member has net-sold to ≤ 0 contributes `share = 0` (they are out of it).
Partial sales reduce both `net_{m,s}` (numerator) and `book_m` (denominator)
consistently, since both are signed sums across the same rows.

### Step 4 — Combine across members

```
conviction_s = Σ_m share_{m,s}
```

Drop any symbol with `conviction_s == 0`. This is the combined cross-member signal.

### Step 5 — Target weights

```
w_s = conviction_s / Σ conviction                  # normalized, sums to 1
apply max_weight cap, redistribute excess          # iterate until no name exceeds cap
target_$_s = w_s * account_value * pct_invest
```

The `max_weight` redistribution iterates: clamp any weight above `max_weight`,
re-normalize the remainder across uncapped names, repeat until stable (or all names
are capped, in which case residual stays as cash).

### Step 6 — Resolve prices & emit orders

```
for s in (target_symbols ∪ current_positions):
    px = ctx.market_data(s, "1day", 1)            # latest visible close
    if px missing/empty:
        log "skip {s}: no price data"; continue   # skip-and-log rule
    target_shares  = floor(target_$_s / px)       # 0 if s left the basket
    current_shares = ctx.positions.get(s).quantity or 0
    delta = target_shares - current_shares
    drift = |target_$_s - current_$_s| / account_value
    if drift < min_rebalance_pct: continue         # churn band
    if delta > 0: emit BUY  delta
    if delta < 0: emit SELL -delta                 # trims or fully exits
```

- A name that left the basket (`conviction → 0`, i.e. Congress sold out) has
  `target_shares = 0` → full **SELL**. This is the exit path. No time-based aging:
  a name is held as long as Congress's net disclosed position in it stays positive.
- Equities only: `Signal.simple(symbol, SignalType.BUY|SELL, qty,
  asset_type="equities", order_type=OrderType.MARKET, reasoning=...)`.
- `reasoning` carries a human-readable rationale (e.g. "3 members, combined
  conviction 11%, target 4.2%") for the dashboard / trade log.

## Edge cases

- Empty/blank `amount` or unrecognized range bucket → skip row, log.
- Unknown `type` value → skip row, log.
- Member discloses a sale of a name we never tracked → no current position,
  `target=0`, no-op.
- No parseable disclosures / all conviction zero → emit nothing, hold cash.
- Whitelisted member name not present in the data → log once, continue (catches typos).
- `book_m` below `min_member_history_usd` → member excluded entirely (handles the
  rarely-trading buy-and-hold member whose occasional trades would otherwise look
  oversized relative to their thin disclosed book).
- Fractional shares: floor to whole shares; a name whose target is < 1 share is
  effectively skipped.

## Backtest data prerequisites (workflow, not new code)

For a meaningful backtest, two things must be present:

1. **Disclosure datasets downloaded** for the backtest window (subject to the
   free-tier history limitation above).
2. **OHLCV bars for every traded symbol.** The universe is dynamic, so symbols
   cannot be pre-listed. Workflow: run the algo, collect the "skip: no price data"
   logs, then queue those symbols through the existing data-acquisition machinery
   (`DataGoal` / `quilt data`). Auto-downloading bars for newly-disclosed symbols
   is explicitly out of scope (see Backlog).

## Testing

`on_tick` is a pure function, so unit tests are the backbone:

- **Amount parse table:** every FMP range bucket + garbage/blank input → expected
  midpoint or skip.
- **Weighting pipeline:** a fixture disclosure frame → assert exact target weights
  through Steps 3–5, including `max_weight` redistribution and the
  `min_member_history_usd` floor.
- **Exit path:** a sale that zeroes a member's net in a held name → assert full-exit
  SELL.
- **Churn band:** target within `min_rebalance_pct` of current → assert no order.
- **Whitelist matching:** last-name-only and "Last, First" forms; case-insensitivity.
- **Integration-style:** run `on_tick` against a small seeded dataset + a fake price
  source, asserting the emitted `Signal` list (symbols, sides, quantities).

## Non-goals / deferred (see backlog)

- Options or short-selling Congress positions (equities long-only here).
- Auto-downloading price bars for newly-disclosed symbols.
- Historical disclosure backfill / paid-tier FMP for deep backtests.
- External per-member net-worth data (we use the self-contained disclosed-portfolio
  proxy for `book_m`).
- Per-member track-record weighting (e.g. up-weighting historically strong members).
