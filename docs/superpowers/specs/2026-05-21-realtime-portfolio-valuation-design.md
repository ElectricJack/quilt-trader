# Real-Time Portfolio Valuation & Historical Equity Curves — Design

## Problem

The quilt-trader dashboard's portfolio and account values don't update in real-time, and equity curves don't reflect actual historical asset prices. Currently, equity curves interpolate between sparse broker snapshots using cash-flow adjustments, missing the actual mark-to-market movement of held positions. The dashboard polls REST endpoints every 15-60 seconds for values that should feel instant.

## Solution Overview

Six interconnected changes:

1. Add Tradier, Alpaca, and Polygon as historical data providers in the download manager
2. Build a daily position ledger by replaying broker transaction history
3. Materialize daily equity curves by joining the position ledger against historical close prices
4. Push real-time mark-to-market updates via WebSocket as live ticks arrive
5. Automate account sync lifecycle (replacing manual sync button)
6. Update dashboard to consume WebSocket pushes for live data and read materialized history for charts

## Section 1: Historical Data Providers

Add Tradier and Alpaca as data providers alongside Polygon in the download manager. Each implements a common interface:

```
DataProvider
  ├── download_bars(symbol, timeframe, start, end) → DataFrame
  ├── max_lookback() → timedelta | None
  └── rate_limit_delay() → float (seconds between requests)
```

Provider capabilities tested:

- **Tradier:** 10+ years of daily history, free with brokerage account
- **Polygon:** 2-year lookback on free plan
- **Alpaca:** Requires paid data subscription for historical bars

Provider priority is configurable per-coordinator (default: Tradier → Polygon → Alpaca). When the download manager needs historical bars for a symbol, it tries providers in priority order — if one fails or has no data for that range, it falls to the next. Results stored in existing parquet structure: `data/market/{provider}/{symbol}/{timeframe}.parquet`.

A `default_history_provider` setting in coordinator config/settings specifies which single provider the equity curve builder uses for account history reconstruction. This avoids multi-provider fallback complexity for this specific use case. Users can change it if they have a paid Alpaca plan or prefer Polygon.

The existing `DataService.load_market_data(source, symbol, timeframe)` loads from disk. The equity curve builder uses a "best available" source lookup — checking all providers on disk for a symbol and using whichever has coverage for the needed date range.

**Account-triggered downloads use the same pipeline as manual downloads.** When a new account is added and the backfill discovers historically-held symbols, it creates download jobs through the existing download manager — the same path as `quilt data download`. These downloads appear in the data tab, are tracked in the `MarketDataDownload` table, and land in the standard `data/market/{provider}/{symbol}/{timeframe}.parquet` structure. There is no separate storage for "account history" vs "manually downloaded" data — it's all the same data, just triggered automatically.

**Intraday vs. historical data sources are decoupled.** During market hours, the PortfolioTracker (Section 4) uses live ticks from whatever subscriptions are active — these can come from any broker regardless of which provider supplies historical data. For example, an Alpaca account can use Alpaca live ticks for real-time valuation while Polygon supplies the historical daily closes. Intraday points are ephemeral (streamed to the dashboard, never stored). The daily close job writes the official end-of-day row using the default history provider's closing prices, keeping the materialized history clean and consistent for backtesting.

## Section 2: Position Ledger from Transactions

When an account is added (or on first sync), the coordinator pulls full transaction history from the broker and builds a daily position ledger — a table that answers "what did this account hold on date X?"

**Table: `account_position_ledger`**

- `account_id`, `date`, `symbol`, `quantity`, `avg_cost`
- One row per (account, date, symbol) where quantity > 0
- Built by replaying fills chronologically: buys add shares, sells remove them

**Construction flow:**

1. Pull all transactions via `adapter.get_transactions(since=account_inception)`
2. Walk fills in chronological order, tracking running position per symbol
3. At each date boundary, snapshot the positions into the ledger table
4. Cash tracked separately (starting balance + inflows - outflows - purchases + sales)

**Incremental updates:** After the initial backfill, the periodic sync only needs to process new transactions since the last sync and append/update ledger rows for affected dates.

## Section 3: Materialized Equity Curve

A background job joins the position ledger against historical prices to produce a daily equity time series per account.

**Table: `account_equity_daily`**

- `account_id`, `date`, `total_value`, `positions_value`, `cash`, `net_deposits_cumulative`
- One row per (account, date)

**Computation for each date:**

```
positions_value = sum(quantity * close_price for each held symbol)
cash = running cash balance from ledger
total_value = positions_value + cash
```

**When it runs:**

1. **Account added** — full backfill: download missing price data from the default history provider, then materialize from first transaction date to yesterday
2. **After market close** — daily job appends today's row using closing prices
3. **After data download completes** — re-materialize any dates that were missing prices (fills gaps)

**Missing price data handling:** If a symbol has no price for a given date (delisted, not yet downloaded), use the last known price (forward-fill). Flag the row as `estimated=True` so the frontend can indicate lower confidence if desired.

**Replaces current equity curve endpoint:** The existing `GET /api/accounts/{id}/equity-curve` currently does cash-flow-based estimation. It switches to reading from this table — much faster and accurate since it uses actual position mark-to-market.

## Section 4: Real-Time Mark-to-Market via WebSocket

During market hours, extend the last materialized equity point with live prices.

**New coordinator service: `PortfolioTracker`**

Maintains an in-memory mark-to-market cache per account:

- On startup (or account subscribe), loads current positions from broker + last known prices
- Listens to the LiveFeedAggregator's bar/tick callbacks for held symbols
- When a held symbol's price updates, recomputes that account's total value
- Debounces to at most 1 update per second per account

**WebSocket topic subscriptions:**

- `portfolio:summary` — pushes `{total_equity, today_pnl, today_pnl_pct}` aggregated across all visible accounts. Used by Overview page KPI strip.
- `account:{id}` — pushes `{total_value, positions_value, cash, today_pnl}` for one account. Used by Account Detail page.
- `deployment:{id}:equity` — pushes `{portfolio_value, cash}` for the deployment's account. Used by Deployment Detail page.

**Lifecycle:**

- Dashboard navigates to Account Detail → subscribes to `account:{id}`
- PortfolioTracker starts tracking that account (if not already)
- As ticks arrive for held symbols, pushes updated values
- Dashboard navigates away → unsubscribes → PortfolioTracker stops tracking if no other subscribers

**Intraday equity curve:** The frontend appends each pushed value as a new point on the chart, giving a smooth intraday line extending from the last materialized daily close. No storage needed — ephemeral, rebuilds on page load from today's ticks.

## Section 5: Automatic Account Lifecycle

Replace the manual sync workflow with automatic background jobs.

**On account added:**

1. Validate broker credentials (existing)
2. Pull full transaction history → build position ledger
3. Trigger download of daily bars for all historically-held symbols from default provider
4. Materialize equity curve once downloads complete
5. Push progress to dashboard via WebSocket (`account:{id}:setup_progress`)

**Periodic sync job (every 15 minutes during market hours):**

- For each connected account, pull new transactions since last sync
- Update position ledger for any new fills
- Update cash flows for dividends/deposits/withdrawals
- Refresh current positions from broker (catches manual trades done outside quilt)

**Daily close job (runs ~30 min after market close):**

- Download today's closing prices for all held symbols
- Append today's row to `account_equity_daily` for each account
- Recompute deployment report metrics (existing LiveFinalizer logic)

**Sync button:** Demoted to a "more actions" menu on the account page. Triggers the same logic as the periodic sync but immediately. Useful for debugging or forcing a refresh after manual broker activity.

**Frontend feedback:** During the initial backfill (which could take minutes for a new account with years of history), the dashboard shows a progress indicator. WebSocket pushes status updates: "Pulling transactions... Downloading price data (12/34 symbols)... Building equity curve..."

## Section 6: Dashboard Integration

**Overview page:**

- KPI strip (total equity, today P&L, etc.) subscribes to `portfolio:summary` on mount — updates within 1 second of any price change
- Portfolio equity chart loads materialized history from REST, then appends live points from WebSocket stream
- Allocation chart continues polling every 60s (positions don't change frequently enough for real-time)

**Account Detail page:**

- Subscribes to `account:{id}` on mount
- Equity curve loads from `GET /api/accounts/{id}/equity-curve` (now reads from `account_equity_daily` — fast), then extends with live intraday points from WebSocket
- Positions table shows current quantities + live unrealized P&L updating as prices tick
- New account setup shows progress bar during initial backfill

**Deployment Detail page:**

- Subscribes to `deployment:{id}:equity` on mount
- Equity curve: materialized history + live extension (same pattern)
- Activity feed already updates via WebSocket (no change needed)

**Frontend changes:**

- `WebSocketManager` gets new topic types but subscribe/unsubscribe pattern unchanged
- React Query hooks for equity curves switch from polling to "fetch once + append from WebSocket"
- KPI hooks switch from `refetchInterval: 30s` to WebSocket-driven invalidation
- New `useAccountSetupProgress` hook for backfill progress indicator

**No changes needed:**

- Chart components (lightweight-charts) — already accept data arrays, just append to them
- Page routing, layout, component structure
- Activity/log feeds, worker status, deployment controls

## Key Files Affected

**New files:**

- `coordinator/services/portfolio_tracker.py` — real-time mark-to-market service
- `coordinator/services/account_backfill.py` — transaction replay + ledger construction
- `coordinator/data_providers/tradier_provider.py` — Tradier historical bars
- `coordinator/data_providers/alpaca_provider.py` — Alpaca historical bars

**Modified coordinator files:**

- `coordinator/main.py` — wire PortfolioTracker, backfill jobs, periodic sync
- `coordinator/services/download_manager.py` — support new providers
- `coordinator/api/routes/accounts.py` — equity-curve endpoint reads from materialized table
- `coordinator/api/routes/portfolio.py` — KPIs computed from PortfolioTracker cache
- `coordinator/api/websocket.py` — handle new subscription topics, push equity updates
- `coordinator/database/models.py` — new tables (account_position_ledger, account_equity_daily)

**Modified dashboard files:**

- `dashboard/src/api/websocket.ts` — handle new message types
- `dashboard/src/api/hooks.ts` — switch from polling to WebSocket-driven updates
- `dashboard/src/components/widgets/PortfolioEquityWidget.tsx` — append live points
- `dashboard/src/components/widgets/KpiStripWidget.tsx` — consume WebSocket pushes
- `dashboard/src/pages/AccountDetail.tsx` — live equity updates, setup progress
- `dashboard/src/pages/DeploymentDetail.tsx` — live equity updates

## New Database Tables

```sql
CREATE TABLE account_position_ledger (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    date DATE NOT NULL,
    symbol TEXT NOT NULL,
    quantity REAL NOT NULL,
    avg_cost REAL NOT NULL,
    UNIQUE(account_id, date, symbol)
);

CREATE TABLE account_equity_daily (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    date DATE NOT NULL,
    total_value REAL NOT NULL,
    positions_value REAL NOT NULL,
    cash REAL NOT NULL,
    net_deposits_cumulative REAL NOT NULL DEFAULT 0,
    estimated BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE(account_id, date)
);
```
