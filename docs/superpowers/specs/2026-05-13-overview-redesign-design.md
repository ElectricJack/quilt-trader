# Dashboard Overview Redesign

**Date:** 2026-05-13
**Status:** Draft
**Repo:** `quilt-trader` (dashboard + coordinator)

## 1. Overview

Replace the current Overview page widgets with trader-focused widgets that surface real data: charts where today there is only text, names where today there are GUIDs, and concrete trade details where today there is only the literal string `"Trade Executed"`. Every row becomes clickable and links to an existing detail page.

The page architecture is unchanged: the customizable widget grid (`DashboardGrid` + `useDashboardStore`) stays, every widget remains self-contained and individually draggable/toggleable, and the existing `Customize` modal continues to work without modification. What changes is the data each widget displays and the visual form (chart vs. list vs. number).

### 1.1 Goals

- Replace 9 of the 12 current widgets with redesigned versions; remove 3 widgets that don't earn their space.
- Surface real trade data (symbol, side, qty, price) instead of generic `event_type` strings.
- Show algorithm and account names everywhere — never raw GUIDs in user-facing text.
- Make every row in every list widget clickable and route to the relevant detail page.
- Add charts: a stacked portfolio equity curve, per-algorithm P&L sparklines, an asset allocation donut, a per-position sparkline, and a cash/positions split bar per account.
- Add the backend endpoints required to do all of the above (the underlying database tables already exist; we just need read APIs).

### 1.2 Non-goals

- No new top-level page or route. This redesign only touches the Overview page (`/`) and its widgets.
- No new detail pages. Rows link to existing detail pages (`InstanceDetail`, `AccountDetail`, `WorkerDetail`, `BacktestDetail`). A future spec may add `PositionDetail` and `TradeDetail`.
- No WebSocket plumbing changes. Widgets continue to use React Query polling for refresh; the existing `useWebSocket` consumer code is untouched.
- No mobile layout. The existing single-grid desktop layout is unchanged.
- No customize/reorder UX changes. `CustomizeModal` and the drag handle behavior are unchanged.
- No real-time tick streaming, position-level P&L recomputation in the dashboard, or anything that pushes pricing logic into the frontend. The coordinator computes; the dashboard renders.

## 2. Widget catalog (8 widgets)

The current Overview has 12 widgets. The new Overview has 8. Two existing widgets are merged into one (`ActiveAlgorithmsWidget` + `TodaysPnLWidget` → `AlgorithmsWidget`), two are removed (`WorkerHealthWidget`, no replacement; the historical `Drawdown` was proposed and rejected), and the two alert widgets are merged (`SystemEventsWidget` + `BacktestAlertsWidget` → `AlertsWidget`).

For each widget below: the **source data**, the **visual form**, and **what each row links to** when clicked.

### 2.1 PortfolioEquityWidget (new; replaces PortfolioValueWidget)

A stacked area chart of total portfolio value over time, with each broker account rendered as a separate colored band that sums to the total. Above the chart: the current total equity as a large number, the lifetime $ and % delta, and time-range pills (`1D` / `1W` / `1M` / `All`). The white line on top of the stack traces the running total.

**Source data:** `GET /api/portfolio/equity?range={1d|1w|1m|all}` — returns `{ accounts: [{ account_id, account_name, points: [{ timestamp, value }] }] }`. Built from `account_snapshots` aligned to a common time axis.

**Click target:** chart bands are not clickable (would conflict with chart hover); legend entries link to `/accounts/:account_id`.

### 2.2 KpiStripWidget (new)

A 4×2 grid of 8 today's-summary KPIs filling the card edge-to-edge with a 1px divider between cells. Each cell has a small uppercase label, a large bold value, and a one-line sub-text.

**KPIs (in grid order):**

| # | Label | Source |
|---|---|---|
| 1 | Today P&L | sum of `trade_log.realized_pnl` today + sum of `positions.unrealized_pnl` delta since open today |
| 2 | Total Equity | latest `account_snapshots.total_value` summed across accounts |
| 3 | Trades Today | count of `trade_log` rows with `timestamp >= today` |
| 4 | Win Rate | win count / total count of closed trade groups today (sub: 7-day avg) |
| 5 | Open Positions | count of `positions` where `status='open'` (sub: long vs short split) |
| 6 | Open Risk | sum of `positions.unrealized_pnl` over open positions (signed; sub: % of equity) |
| 7 | Deployed | sum of `positions_value` / sum of `total_value` (sub: $ deployed) |
| 8 | Buying Power | sum of `cash` across accounts (sub: % of equity) |

**Source data:** `GET /api/portfolio/kpis` — returns a flat object with all eight values plus the sub-text companions. Server-side aggregation; the dashboard does no math.

**Click target:** none — KPIs are summaries, not entry points.

### 2.3 AlgorithmsWidget (merged; replaces ActiveAlgorithmsWidget + TodaysPnLWidget)

A table of all algorithm instances (both running and stopped), ranked by lifetime P&L descending. Each row shows: status dot · algorithm name (resolved from `algorithm_id`) · account name (subtext) · today's P&L (subtext) · cumulative P&L sparkline · trade count · win % · lifetime $. Header row above the data uses uppercase labels. Above the header: total lifetime P&L and "N running · M stopped" summary.

**Source data:** existing `useAllInstances()` enriched with three new fields on the API response:

- `algorithm_name: string` — joined from `algorithms.name`
- `account_name: string` — joined from `accounts.name`
- `pnl_sparkline: number[]` — downsampled cumulative-P&L series (~20 points) from the latest active `algorithm_run.equity_curve`

The coordinator modifies `GET /api/instances` to include these fields. No new endpoint.

**Today's P&L** per instance is derived from `trade_log` joined on `instance_id` filtered to today. The instance response includes `today_pnl: number` as a fourth new field.

**Click target:** row → `/instances/:id`.

### 2.4 OpenPositionsWidget (replaces existing; same name, different innards)

A list of open positions, one row per position. Each row: symbol · side (Long/Short) · quantity on line 1 along with unrealized P&L $ and %; line 2: entry → mark price and the algorithm name (muted).

**Source data:** new `GET /api/positions?status=open&limit=10` — returns `Position` rows joined with the position's algorithm name and the latest mark price per symbol. The `legs` JSON column already exists; the endpoint expands the first leg for the row display (single-leg case) and shows a `+N legs` indicator for multi-leg positions like spreads.

The mark price comes from the latest worker tick recorded on the position itself. There is no per-position sparkline in this iteration — the existing schema has no per-position time series, and adding one is out of scope (see §1.2). A position-level sparkline can be added in a later spec once a `position_snapshots` table exists.

**Click target:** row → `/instances/:instance_id`.

### 2.5 RecentTradesWidget (rewrite; same name)

A list of the most recent trades from `trade_log` (not from the `events` table). Each row is a CSS grid with columns: time · BUY/SELL pill (green/red) · symbol · `qty @ price` · $ notional · algorithm name (muted).

**Source data:** new `GET /api/trades?limit=10` — returns `TradeLog` rows joined with `algorithm_instances` → `algorithms.name` for the algorithm-name column. Sorted by `timestamp` descending.

**Click target:** row → `/instances/:instance_id`.

### 2.6 AccountBalancesWidget (rewrite; same name)

One row per account. Each row: account name on the left, total value and day Δ% on the right; below that, a horizontal stacked bar (blue = positions, green = cash) that always spans the card width; below that, a single line of sub-text with the percent and $ split.

**Source data:** new `GET /api/accounts/snapshots/latest` — returns one entry per account with the most recent `account_snapshots` row (`total_value`, `cash`, `positions_value`) plus a 24-hour-prior comparison row for the day Δ%. Single request, no per-account fan-out. The widget pairs the response with the existing `useAccounts()` call for the account display name and broker type.

**Click target:** row → `/accounts/:id`.

### 2.7 AssetAllocationWidget (new)

A donut chart of the portfolio with a legend. A `By Class` / `By Symbol` toggle above the donut switches between two views:

- **By Class** (default): segments are asset classes (`equities`, `crypto`, `options`, `cash`). At most 5 segments — `cash` is always present.
- **By Symbol**: segments are the top 6 individual symbols by $ value, with a "+N more" segment grouping the long tail. Always-present `cash` slice last.

The legend on the right lists each segment with a colored swatch, name, percentage, and dollar value.

**Source data:** new `GET /api/portfolio/allocation` — returns both groupings in one payload to avoid round-tripping on toggle. Shape: `{ by_class: AllocSegment[], by_symbol: AllocSegment[] }` where `AllocSegment = { key, label, value_usd, percent, color }`. Computed from open `positions` joined with `account_snapshots.cash`.

**Click target:** legend entries are not clickable in this iteration. (A future enhancement could filter Open Positions by clicking a segment.)

### 2.8 AlertsWidget (merged; replaces SystemEventsWidget + BacktestAlertsWidget)

A unified list of all alert-style items: warning/error events from the `events` table plus backtest divergence findings from the `backtest_comparisons` table. Each row: a colored pill on the left (`WARN`/`ERR`/`PDT`/`DISC`/`87%` etc.) and a two-line label on the right (event description and source · timestamp).

**Source data:** new aggregator `GET /api/alerts?limit=10` that unions:

- `events` rows where `severity IN ('warning', 'error')`
- `backtest_comparisons` rows where `match_percentage < 90` (rolling window: last 24h)

The endpoint resolves `source_id` to a human name (algorithm name, worker name) before returning. Each row also includes `link_path` so the dashboard doesn't need source-type-specific routing logic.

**Click target:** row → `link_path` returned by the API.

## 3. Backend additions

All new endpoints live under existing route files where possible. No new top-level route file is added.

| Endpoint | File | Notes |
|---|---|---|
| `GET /api/portfolio/equity?range=...` | new `coordinator/api/routes/portfolio.py` | Stacked per-account equity series |
| `GET /api/portfolio/kpis` | `portfolio.py` | The 8 KPIs as a flat object |
| `GET /api/portfolio/allocation` | `portfolio.py` | Both `by_class` and `by_symbol` segments |
| `GET /api/positions?status=open&limit=N` | new `coordinator/api/routes/positions.py` | Open positions with joined algorithm name |
| `GET /api/trades?limit=N` | new `coordinator/api/routes/trades.py` | Recent trades with joined algorithm name |
| `GET /api/alerts?limit=N` | new `coordinator/api/routes/alerts.py` | Aggregated alerts with resolved names + `link_path` |
| `GET /api/accounts/snapshots/latest` | `coordinator/api/routes/accounts.py` (extend) | One entry per account: latest snapshot + 24h-prior for day Δ% |
| `GET /api/instances` (modify) | `coordinator/api/routes/algorithms.py` | Add `algorithm_name`, `account_name`, `today_pnl`, `pnl_sparkline` fields |

### 3.1 Schema additions

None. Every field needed already exists in the database. Specifically:

- `algorithms.name`, `accounts.name` — for resolving GUIDs
- `trade_log.symbol`, `side`, `quantity`, `filled_price`, `fees` — for trade details
- `positions.legs`, `unrealized_pnl`, `net_pnl`, `status` — for open positions
- `account_snapshots.total_value`, `cash`, `positions_value`, `timestamp` — for equity and balances
- `algorithm_runs.equity_curve` — for per-algo P&L sparkline
- `events.severity`, `payload`, `source_id` — for alerts (warning/error filter)
- `backtest_comparisons.match_percentage`, `instance_id` — for backtest divergence alerts

### 3.2 Aggregation logic

The portfolio endpoints (`/equity`, `/kpis`, `/allocation`) do server-side aggregation. Each is a single SQL query — no Python-side iteration over large result sets. The dashboard receives shaped data ready to render.

For `pnl_sparkline` on the `/api/instances` modification: the coordinator picks the most recent `algorithm_runs` row per instance and downsamples its `equity_curve` JSON to 20 points using simple linear-by-index resampling. If `equity_curve` is null (a freshly-created instance that's never run), `pnl_sparkline` is null and the widget renders just an empty rectangle in that column.

### 3.3 Authorization

All new endpoints use the existing `Depends(require_auth)` dependency the rest of the API uses. No new permission tiers.

## 4. Frontend additions

### 4.1 New widget components

In `dashboard/src/components/widgets/`:

- `PortfolioEquityWidget.tsx` — replaces `PortfolioValueWidget.tsx` (file kept, contents replaced)
- `KpiStripWidget.tsx` — net-new file
- `AlgorithmsWidget.tsx` — net-new file; replaces both `ActiveAlgorithmsWidget.tsx` and `TodaysPnLWidget.tsx`, which are deleted
- `OpenPositionsWidget.tsx` — rewrite in place
- `RecentTradesWidget.tsx` — rewrite in place
- `AccountBalancesWidget.tsx` — rewrite in place
- `AssetAllocationWidget.tsx` — net-new file
- `AlertsWidget.tsx` — net-new file; replaces both `SystemEventsWidget.tsx` and `BacktestAlertsWidget.tsx`, which are deleted

`widgets/index.ts` is updated to remove the deleted widgets from `WIDGET_REGISTRY` and `WIDGET_TITLES` and add the new ones.

### 4.2 Shared chart components

Three reusable chart components in `dashboard/src/components/`:

- `Sparkline.tsx` — a tiny SVG line chart used by `AlgorithmsWidget`. Props: `points: number[]`, `color?: string`, `height?: number`.
- `StackedAreaChart.tsx` — wraps `lightweight-charts` for the portfolio equity widget. Uses `addAreaSeries` once per stack band. Falls back to a single area when there's only one account.
- `Donut.tsx` — pure SVG donut (no chart library). Props: `segments: AllocSegment[]`, `size?: number`.

The existing `EquityCurve.tsx` is unchanged and continues to be used on detail pages.

### 4.3 New hooks

In `dashboard/src/api/hooks.ts`:

```ts
usePortfolioEquity(range: '1d' | '1w' | '1m' | 'all')
usePortfolioKpis()
usePortfolioAllocation()
useOpenPositions(limit?: number)
useRecentTrades(limit?: number)
useAlerts(limit?: number)
useAccountSnapshotsLatest()
```

All use the existing `useQuery` pattern with reasonable `staleTime` (15–30 s) and `refetchInterval` (30 s for live data, none for slower-moving data like allocation).

### 4.4 Type additions

In `dashboard/src/types/index.ts`, add the response shapes for the new endpoints. The existing `Position` and `TradeLogEntry` types already cover most of what's needed; the new types are:

```ts
PortfolioEquityResponse
PortfolioKpis
AllocSegment
AllocationResponse
AlertItem
AccountSnapshotLatest  // one per account: latest + 24h-prior pair
```

The existing `AlgorithmInstance` type gets four new optional fields: `algorithm_name`, `account_name`, `today_pnl`, `pnl_sparkline`.

### 4.5 Dashboard store changes

In `dashboard/src/stores/dashboard.ts`, the default widget list is updated:

**Removed from defaults:** `worker_health` (the widget is also deleted from the registry).
**Renamed in defaults:** `todays_pnl` and `active_algorithms` → both replaced by single `algorithms` entry.
**Renamed in defaults:** `system_events` and `backtest_alerts` → both replaced by single `alerts` entry.
**Added to defaults:** `kpi_strip`, `asset_allocation`.

Existing users have a persisted layout in `localStorage`. The store's load logic must drop unknown widget IDs (so `worker_health` etc. don't error) and append any new widgets to the end of the user's existing layout (so users see the new widgets but their customizations aren't blown away). A `resetLayout()` call always uses the new defaults.

## 5. Click-through routing

| Row type | Destination |
|---|---|
| Algorithm row (AlgorithmsWidget) | `/instances/:id` |
| Position row (OpenPositionsWidget) | `/instances/:instance_id` |
| Trade row (RecentTradesWidget) | `/instances/:instance_id` |
| Account row (AccountBalancesWidget) | `/accounts/:id` |
| Alert row (AlertsWidget) | `link_path` returned by the API |
| KPI cell | not clickable |
| Donut segment | not clickable in this iteration |
| Equity chart band | not clickable |
| Legend entry (PortfolioEquityWidget) | `/accounts/:id` |

All clickable rows use `useNavigate` from `react-router-dom`. Rows visually indicate clickability with `cursor: pointer` and a subtle hover background change.

## 6. Testing

### 6.1 Backend tests

In `tests/coordinator/`, one test file per new route file:

- `test_portfolio_api.py` — happy-path tests for `/equity`, `/kpis`, `/allocation`. Seed `account_snapshots`, `positions`, `trade_log` and assert response shape and computed values.
- `test_positions_api.py` — happy-path test plus `status=open` filter and `limit` clamping (max 100). One test verifies the joined `algorithm_name`.
- `test_trades_api.py` — happy-path test, `limit` clamping, `algorithm_name` join.
- `test_alerts_api.py` — verifies the union of `events` (warning/error) and `backtest_comparisons` (low match %), plus name resolution and `link_path` correctness.

Edge cases (zero accounts, no positions, empty trade log) return empty arrays — not 404s. One test per endpoint asserts the empty case.

### 6.2 Frontend tests

The existing dashboard test setup is light (no Vitest/Jest config currently). This spec does not require new unit tests; verification is via running the dev server and clicking through the widgets. The test plan in the implementation plan will cover this.

### 6.3 Manual verification

After implementation, the verification checklist is:

1. Every widget renders with seeded data.
2. Every clickable row navigates to the right detail page.
3. No GUIDs appear in user-facing text in any widget.
4. All three chart types render at proper width and respond to container resize.
5. `Customize` modal still toggles widgets correctly with the new widget set.
6. Drag-to-reorder still works with the new widget set.
7. Existing users with a persisted layout see their old layout plus new widgets appended; no errors in console for removed widget IDs.

## 7. Migration / rollout

This is a frontend + backend change shipped together in a single commit (or single PR). There is no schema migration. There is no feature flag. The new endpoints are additive; the modified `/api/instances` endpoint adds fields without changing existing ones (the worker, which consumes this endpoint, ignores unknown fields).

The dashboard store changes are guarded by the existing schema version mechanism in `useDashboardStore`. If the store is read with stale widget IDs, those IDs are dropped silently — no error toast, no console warning. New widgets are appended to existing layouts.

`scripts/seed_data.py` is updated to seed enough data for the new widgets to look populated in development: at least one `account_snapshots` history per account spanning a few days, at least one open `position` per running instance, at least 10 `trade_log` rows.

## 8. Out of scope

- **Position detail page.** Position rows route to the owning instance detail page. A future spec may add `PositionDetail`.
- **Trade detail page.** Same — trades route to the owning instance.
- **Per-position sparklines.** No per-position time series in the existing schema. A `position_snapshots` table is left for a later spec.
- **Multi-leg position rendering.** The Open Positions widget displays the first leg with a `+N legs` indicator when there are more. Full multi-leg breakdown is on the (future) PositionDetail page.
- **Mobile layout.** Same grid as today.
- **Drawdown chart.** Considered and rejected during brainstorming as not actionable on the Overview page (a per-algorithm drawdown belongs on the algorithm detail page).
- **Worker health widget.** Removed. Worker health is still surfaced via alerts when a worker disconnects, and via the `/workers` list page.
- **Allocation drill-down.** Clicking a donut segment to filter Open Positions is a nice-to-have left for a later iteration.
- **Color customization.** Hard-coded palette in the new chart components. No theming knob in this iteration.
