# Running Algorithm UX — Design Spec

**Date:** 2026-05-16
**Status:** Draft for implementation
**Scope:** Dashboard UX/UI overhaul for running algorithms on workers, plus the
backend changes needed to support a live "report" view modeled on the backtest
report page.

---

## 1. Motivation

The current UI exposes three internal concepts to the user — `Worker`,
`AlgorithmInstance`, and `AlgorithmRun` — and the user is forced to navigate
between them to answer the basic question "is this algorithm working?". On top
of that:

- Starting and stopping an algorithm feels slow because the three pages that
  show its status (worker, algorithm, instance) only update on refetch, not on
  a coordinated event.
- There's no live signal that an algorithm is doing anything once it's
  running — no log stream, no live metrics.
- Worker heartbeat times can render as a negative number of seconds, while
  status sticks at "Online" even after disconnect.
- The instance page is a stub. Backtests already have a rich, multi-panel
  report; there's no equivalent for live runs, and the data paths are
  different so direct comparison is awkward.

This spec rebuilds the surfaces around a single user-facing concept —
**Algorithm Deployment** — and reuses the backtest streaming pipeline to power
its live report. Internal data model (`AlgorithmInstance`, `AlgorithmRun`) is
unchanged; only the API surface and UI labels move.

---

## 2. Information Architecture

### Vocabulary

| Public name | Underlying model | Notes |
|---|---|---|
| Algorithm | `Algorithm` | The installed package. No change. |
| Account | `Account` | Brokerage account. No change. |
| Worker | `Worker` | Pi node. No change. |
| **Algorithm Deployment** | `AlgorithmInstance` | NEW PUBLIC NAME. Durable association of (algorithm, account, worker). |
| (hidden) | `AlgorithmRun` | A single start→stop session of a deployment. Not exposed as a standalone concept; surfaced as run history inside the deployment page. |

The words **instance** and **run** disappear from URLs, page titles, breadcrumbs,
labels, and section headers. They remain in DB tables, ORM models, internal
service code, websocket message types, and code comments. No schema migration
is required for the rename.

### URL changes

| Old | New | Behavior |
|---|---|---|
| `/instances/:id` | `/deployments/:id` | Page is the new live report (Section 3). |
| `/runs/:id` | *removed* | Single-run focus is a filter/query-param on the deployment page. |
| `/instances/:id` (legacy) | `/deployments/:id` | 301 redirect for one release for any saved bookmarks. |
| `/algorithms/:id` | unchanged | Now lists "Running Algorithm Deployments". |
| `/workers/:id` | unchanged | Now has "Running Algorithms" + "Activity" sections. |

### Page surfaces

- **Worker detail** (`/workers/:id`)
  - Worker info card grid (unchanged shape, fixed heartbeat display).
  - **Running Algorithms** section — table of deployments assigned to this worker.
  - **Activity** panel — live event/log stream (Section 4).
- **Algorithm detail** (`/algorithms/:id`)
  - Algorithm metadata (unchanged).
  - Config schema (unchanged).
  - **Running Algorithm Deployments** section — table of deployments for this algo.
  - "Deploy" button (renamed from "Create Instance") — modal is functionally identical.
- **Deployment detail** (`/deployments/:id`)
  - The new live report (Section 3).
- **Overview**
  - "Running Instances" widget → renamed **Running Algorithms**.

---

## 3. The Deployment Page

Replaces `dashboard/src/pages/InstanceDetail.tsx`. Structurally modeled on
`dashboard/src/pages/BacktestRunDetail.tsx`, reusing the
`components/report/*` building blocks where they already exist.

### 3.1 Header

- Page title: algorithm name.
- Subtitle line: `<account name> · <worker name>` (both linked).
- Status badge: the deployment's current status (Section 5.3 vocabulary).
- Action buttons: **Start** (when stopped or error), **Stop** (when running),
  **Edit Config**, **Delete**. Start/Stop apply optimistic state changes
  (Section 5.3).

### 3.2 Lifetime KPI row

Same `KpiCard` components as the backtest report:
`Annual Return (CAGR) · Total Return · Max Drawdown · RoMaD · Sharpe · Sortino · Longest DD Days`.

All KPIs are computed **across all runs of the deployment** (lifetime), with
cash flows between runs handled as TWR using the same approach as
`SnapshotService.compute_twr`. The data source is the consolidated parquet
described in Section 4.

### 3.3 Chart grid (2×2)

Reuses `EquitySlot`, `DrawdownSlot`, `ReturnsDistributionSlot`,
`RollingMetricsSlot` from `dashboard/src/components/report/`. They render the
report payload that the `LiveFinalizer` writes (Section 4.4), which has the
same shape as the backtest report payload.

Specific to live:

- **Equity chart** renders the curve **continuously across runs**, with
  **vertical dashed markers** at each run boundary:
  - Light green dashed line at `started_at`.
  - Light gray dashed line at `stopped_at`.
  - Hover tooltip: `Run #N · started/stopped <ts> · <reason>`.
- Periods when the deployment was stopped appear as **gaps in the line**, not
  carried-forward flats. Implemented by injecting `NaN` rows in the
  consolidated parquet between consecutive runs at `daily` resample
  granularity.
- **Drawdown chart** treats gap periods as zero-return days; the existing
  `qs.stats.to_drawdown_series` call handles this if we ffill the parquet
  before passing it in, but for the live page we want the *visible* curve in
  the equity chart to break (not the drawdown calculation underneath it).

### 3.4 Run filter

A dropdown above the KPI row:

```
[ All runs (lifetime)        ▾ ]
   • All runs (lifetime)            ← default
   • Run #12 (current, running)
   • Run #11 — 2026-05-14 to 2026-05-15
   • Run #10 — 2026-05-12 to 2026-05-13
   • …
```

Selecting a specific run re-runs the entire page's data fetch with `?run=<id>`
appended (server-side filter — Section 4.5). KPIs, charts, and tables update
to reflect only that run's slice. Run filter is a query parameter so users
can deep-link a focused view.

### 3.5 Side tables (3-column)

Same components as the backtest report:

- `ParametersTable` — current `config_values` of the deployment.
- `EoyTable` — annual returns from the report payload.
- `DrawdownsTable` — top-N drawdown periods.

### 3.6 Metrics + Trades panel (2-column)

- Left: `MetricsTable` (strategy column only, no benchmark in v1). Same
  component as backtest; sourced from `report.key_metrics.strategy`.
- Right: live trades table from `TradeLog`, filtered to this deployment,
  newest first, paged. Polls at the same cadence as the report.

### 3.7 Runs list (bottom)

A compact table:

| Run # | Status | Started | Ended | Duration | Net P&L | Trades |
|---|---|---|---|---|---|---|

Most-recent-first, including the currently running run at the top. Clicking a
row sets the run filter (3.4) to that run. No separate page.

### 3.8 Configuration & details disclosure (bottom, collapsed by default)

- Full `config_values` JSON.
- `persisted_state` summary (size, last checkpoint time).
- Internal IDs (deployment id = instance id, current run id) for debugging /
  log correlation.

### 3.9 Polling cadence

While the deployment status is `starting | running | stopping`, the deployment
page polls `/api/deployments/:id/report` every 2 seconds (matches backtest
in-flight behavior). When `stopped` or `error`, no polling.

---

## 4. Live Data Pipeline

Mirrors the backtest streaming pipeline (`backtest_writer.py` →
`backtest_finalizer.py`) so live results and backtest results are computed by
the same code paths and are therefore directly comparable.

### 4.1 Worker-side: per-tick samples

A new `LiveObserver` in `worker/` implements the same observer interface as
the backtest's `ChunkingObserver`. On every algorithm tick (live mode):

1. Calls broker adapter for current account state (cash, positions value).
2. Computes `portfolio_value = cash + positions_value`.
3. Sends `equity_sample` ws message:
   ```json
   {
     "type": "equity_sample",
     "instance_id": "...",
     "run_id": "...",
     "timestamp": "2026-05-16T14:30:00Z",
     "portfolio_value": 100123.45,
     "cash": 12345.67
   }
   ```
4. On each trade fill, sends `trade_sample` ws message with fields matching
   `_TRADE_SCHEMA` in `backtest_writer.py`:
   ```json
   {
     "type": "trade_sample",
     "instance_id": "...",
     "run_id": "...",
     "timestamp": "...",
     "symbol": "...",
     "asset_type": "...",
     "side": "...",
     "quantity": ...,
     "requested_price": ...,
     "fill_price": ...,
     "slippage_dollars": ...,
     "slippage_bps_applied": ...,
     "fees": ...,
     "fee_breakdown": {...},
     "signal_id": "...",
     "realized_pnl": ...
   }
   ```

Broker-state queries are cached for the duration of a single tick to avoid
duplicate broker calls when an algo emits multiple signals from one tick.

### 4.2 Coordinator-side: `LiveSampleSink`

New service at `coordinator/services/live_sample_sink.py`. Conceptually the
live counterpart to `backtest_writer.ParquetWriterThread`. Consumes
`equity_sample` and `trade_sample` ws messages routed from
`coordinator/api/websocket.py`, buffers in memory, and appends to per-run
parquet files at:

```
data/live/<deployment_id>/<run_id>/equity.parquet
data/live/<deployment_id>/<run_id>/trades.parquet
```

Uses the **same `_EQUITY_SCHEMA` and `_TRADE_SCHEMA`** constants as
`backtest_writer.py` (factor them into a shared module
`coordinator/services/streaming_schemas.py`). Flush triggers:

- Buffer reaches 200 rows, OR
- 10 seconds since last flush, whichever comes first.

On worker disconnect or run stop, a final flush is forced.

### 4.3 Cross-run consolidation

When the finalizer needs to compute lifetime metrics, it reads all per-run
parquets for the deployment in chronological order and concatenates them,
inserting a single `NaN` row between consecutive runs (timestamp = midpoint
between previous run's `stopped_at` and next run's `started_at`). This NaN
row causes the equity chart to break visibly between runs and excludes the
gap from rolling-window calculations. For a single-run case (currently
running, no previous run), no gap rows are inserted. For a currently-running
run that follows a stopped one, the gap row is placed between the previous
run's last sample and the current run's first sample.

`AccountCashFlow` entries whose `timestamp` falls between or during runs are
folded into the daily-resampled series as TWR adjustments using the same logic
as `SnapshotService.compute_twr`.

### 4.4 `LiveFinalizer` service

New service at `coordinator/services/live_finalizer.py`. A background task in
the coordinator that, every 60 seconds, iterates over all deployments whose
status is `running` and for each:

1. Forces a flush of `LiveSampleSink` buffers for that deployment.
2. Builds the consolidated parquet view (Section 4.3).
3. Calls existing helpers from `backtest_finalizer.py`:
   - `resample_to_daily`
   - `_returns_from_pv`
   - `build_drawdown_curve`
   - `build_monthly_matrix`
   - quantstats-based `compute_all` from `backtest_metrics_qs.py`
4. Upserts the resulting payload into the new `AlgorithmDeploymentReport`
   table.
5. Computes per-run summaries and upserts each run's `AlgorithmRun.metrics`
   so the runs-list-at-bottom shows current numbers.

Also runs once **immediately on deployment stop** (Section 5.3 flow) so the
final report reflects the last samples.

The interval is configurable (`QT_LIVE_FINALIZE_INTERVAL_SECONDS`, default
15) — chosen to be small enough that the 2-second dashboard poll usually
sees fresh data within ~15s of a tick, while large enough that the
quantstats compute doesn't dominate the coordinator's CPU under load.

The per-run summary written to `AlgorithmRun.metrics` uses the same shape as
`BacktestRun.key_metrics.strategy` (so the runs list at the bottom of the
deployment page can show consistent numbers): `total_return`, `cagr`,
`sharpe_ratio`, `sortino_ratio`, `max_drawdown`, `volatility`,
`trade_count`, `win_rate`, `profit_factor`, plus the existing
`AlgorithmRun` scalar columns (`starting_equity`, `ending_equity`,
`net_pnl`, `total_fees`, `total_slippage`, `trade_count`) which are written
directly.

### 4.5 New DB table

`AlgorithmDeploymentReport` — one row per deployment, upserted by the
finalizer. Columns mirror the result-blob columns of `BacktestRun` so the
existing report components can render it without modification:

```python
class AlgorithmDeploymentReport(Base):
    __tablename__ = "algorithm_deployment_reports"
    deployment_id: Mapped[str] = mapped_column(
        String, ForeignKey("algorithm_instances.id"), primary_key=True
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    # Same metric scalar columns as BacktestRun (total_return, cagr, sharpe,
    # sortino, calmar, max_drawdown, max_drawdown_date, romad, total_fees_paid,
    # total_slippage_dollars, trade_count, win_rate, profit_factor, avg_win,
    # avg_loss, expectancy, longest_drawdown_days, longest_winning_streak,
    # longest_losing_streak, volatility).
    # Same blob columns: equity_curve, drawdown_curve, key_metrics,
    # rolling_metrics, monthly_returns_matrix, eoy_returns, drawdown_periods.
    runs_index: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    # runs_index: [{run_id, run_number, started_at, stopped_at, status}, ...]
    # — used by the chart to draw run-boundary markers.
```

Migration: a single Alembic revision adds this table. No data migration.

### 4.6 API endpoints

Renamed routes (the existing `coordinator/api/routes/algorithms.py`,
`runs.py`, etc. stay; we add a new `deployments.py` that re-uses the same
ORM/queries):

| Method | Path | Returns |
|---|---|---|
| GET | `/api/deployments` | List of deployments (with `algorithm_name`, `account_name`, `worker_name` populated via join). |
| GET | `/api/deployments/:id` | Single deployment with hydrated names. |
| GET | `/api/deployments/:id/report` | The latest `AlgorithmDeploymentReport` payload. |
| GET | `/api/deployments/:id/report?run_id=<rid>` | A finalizer-computed report against just that run's parquet (computed on demand — cheap because per-run parquets are small). |
| GET | `/api/deployments/:id/trades?limit=&offset=&run_id=` | Trades table, paged. |
| GET | `/api/deployments/:id/runs` | List of runs for this deployment, newest first. |
| POST | `/api/deployments/:id/start` | Optimistic start (Section 5.3). |
| POST | `/api/deployments/:id/stop` | Optimistic stop (Section 5.3). |
| PATCH | `/api/deployments/:id` | Update config_values. |
| DELETE | `/api/deployments/:id` | Delete deployment. |

The old `/api/instances*` and `/api/runs*` routes stay live for one release as
thin redirects/aliases, then are removed.

### 4.7 Storage and traffic

At one tick per minute during market hours, each deployment produces ~390
equity rows/day × ~30 bytes per row ≈ 12 KB/day raw, well under 1 KB/day
after parquet's snappy compression. With trade samples and across multiple
deployments, expected daily growth per Pi is < 100 KB. Retention is not bounded
in v1 — revisit if it grows past a few MB per deployment.

---

## 5. Worker Activity Stream

The "see what the worker is doing" pain point. Adds a new structured log
stream from worker to coordinator and a UI surface for tailing it.

### 5.1 Wire format

Two new ws message types from worker to coordinator:

**`activity_event`** — structured events:
```json
{
  "type": "activity_event",
  "worker_id": "...",
  "instance_id": "...",
  "timestamp": "...",
  "event_type": "trade_executed",
  "severity": "info",
  "payload": { ... }
}
```

Event types:
- Lifecycle: `instance_starting`, `instance_started`, `instance_stopped`, `instance_error`.
- Tick: `tick_processed` (emitted only when `signals_produced > 0` or
  `trades_executed > 0` — otherwise silent), `idle_tick` (heartbeat-style,
  emitted once after 60s of consecutive silent ticks; payload includes the
  silent-tick count and last-seen-price for the most recent symbol).
- Algorithm: `signal_produced`, `position_opened`, `position_closed`,
  `trade_executed`.
- Errors: `broker_error`, `data_error`, `algo_exception`.

Severities: `debug | info | warn | error`.

**`algo_log`** — captured `logging` records from inside the algorithm:
```json
{
  "type": "algo_log",
  "worker_id": "...",
  "instance_id": "...",
  "timestamp": "...",
  "logger_name": "myalgo.signals",
  "level": "INFO",
  "message": "MACD crossed up on AAPL"
}
```

The worker installs a `logging.Handler` scoped to the algorithm's top-level
module that ships records to the coordinator. Default minimum level is `INFO`
per deployment; per-deployment configuration field `log_level` overrides it.

### 5.2 Storage

```python
class WorkerActivity(Base):
    __tablename__ = "worker_activity"
    __table_args__ = (
        Index("ix_worker_activity_worker_ts", "worker_id", "timestamp"),
        Index("ix_worker_activity_instance_ts", "instance_id", "timestamp"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    worker_id: Mapped[str] = mapped_column(String, ForeignKey("workers.id"), nullable=False)
    instance_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("algorithm_instances.id"), nullable=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # "event" | "log"
    event_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    severity: Mapped[str] = mapped_column(String, nullable=False, default="info")
    logger_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
```

Retention: a periodic task in `archival.py` deletes rows older than 7 days.
The retention window is configurable
(`QT_WORKER_ACTIVITY_RETENTION_DAYS`, default 7).

### 5.3 Coordinator forwarding & subscription model

On each incoming `activity_event` or `algo_log`:

1. Persist a `WorkerActivity` row (best-effort; failures logged but don't drop
   the ws message).
2. Broadcast to dashboard ws clients that have subscribed to the relevant
   target.

Dashboard subscription messages:
```json
{ "type": "subscribe", "target": "worker:<worker_id>" }
{ "type": "subscribe", "target": "deployment:<deployment_id>" }
{ "type": "unsubscribe", "target": "..." }
```

The coordinator maintains an in-memory `dict[target_key, set[WebSocket]]`. A
single dashboard ws can hold multiple subscriptions. Broadcasts to a target
are dispatched in O(1) lookup.

### 5.4 API

| Method | Path | Returns |
|---|---|---|
| GET | `/api/workers/:id/activity` | Paged activity, newest first. Query params: `limit` (default 100, max 500), `before` (cursor, ISO timestamp), `severity` (`info|warn|error`), `event_types` (comma-separated), `kind` (`event|log|all`, default `all`). |
| GET | `/api/deployments/:id/activity` | Same shape, scoped to a deployment. |

WS: `subscribe`/`unsubscribe` messages as above.

### 5.5 UI — Activity panel

A new component `dashboard/src/components/ActivityPanel.tsx` used in both the
Worker and Deployment pages. Props: `target: "worker:<id>" | "deployment:<id>"`.

Behavior:

- On mount: fetches the most recent 100 rows via the appropriate REST endpoint,
  then opens a ws subscription and prepends new rows as they arrive.
- Renders a virtualized scrolling list, one row per entry:
  ```
  [12:34:56] info  trade_executed   TrendBot/Alpaca   BUY 10 AAPL @ 175.32
  [12:34:55] info  myalgo.signals    MACD crossed up on AAPL
  ```
- Filters at the top: deployment dropdown (when on a worker page only),
  severity floor selector (defaults to `info`), event-type chips, kind toggle
  (`Events | Logs | Both`).
- Auto-scrolls to bottom unless the user has scrolled up by > 50px; then a
  "Jump to live" pill appears at the bottom.
- "Load older" button at the top of the list pages backward via REST.
- Buffer cap: 500 rows live; older rows are evicted as new ones arrive.

### 5.6 UI placement

- **Worker page**: below the "Running Algorithms" section, taking the full
  width. Default filter: kind=all, severity=info. Default deployment filter:
  all.
- **Deployment page**: below the Metrics + Trades panel, in a collapsible
  section titled **Activity**. Pre-scoped to the deployment.

---

## 6. Bug Fixes & Responsiveness

### 6.1 Heartbeat timestamp serialization

**Root cause.** `worker.last_heartbeat.isoformat()` in
`coordinator/api/routes/workers.py:_to_response` returns a string without a
timezone offset when SQLAlchemy returns naive datetimes (which SQLite tends to
do even when the column is declared `DateTime(timezone=True)`). The browser
interprets the offset-less string as local time. For a user in UTC−7, "ago"
math then produces ≈ −25200 seconds — matching the reported `-25187s`.

**Fix.**

- Add a helper `coordinator/api/serialization.py:to_iso_utc(dt) -> str`:
  ```python
  def to_iso_utc(dt: datetime | None) -> str | None:
      if dt is None:
          return None
      if dt.tzinfo is None:
          dt = dt.replace(tzinfo=timezone.utc)
      return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
  ```
- Replace every `dt.isoformat()` in `coordinator/api/routes/*.py` with
  `to_iso_utc(dt)`. Audit pass: grep for `\.isoformat\(` in `coordinator/api/`.
- Add a unit test that confirms `to_iso_utc` always emits a `Z` suffix or
  `+00:00`-style offset for both naive and aware inputs.

### 6.2 Worker offline transition

**Root cause.** `ConnectionManager.disconnect_worker_by_socket()` in
`coordinator/api/websocket.py` removes the websocket from the in-memory map
but never writes `worker.status = "offline"` to the database. Status stays
`"online"` after disconnect.

**Fix.**

- In `disconnect_worker_by_socket`, when a worker is removed:
  1. Look up the worker by id.
  2. Write `status = "offline"`.
  3. Broadcast `worker_disconnected` to dashboard ws clients.
- Add a periodic sweeper task (every 30 seconds) that marks any worker whose
  `last_heartbeat` is older than 60 seconds as `status = "offline"` if not
  already. Covers the case where the disconnect handler doesn't fire
  (network drop, coordinator restart). The 60s threshold is configurable
  (`QT_WORKER_OFFLINE_TIMEOUT_SECONDS`).

### 6.3 Optimistic & broadcast deployment status

**Root cause.** Today, `start_instance` is forwarded to the worker without any
DB write. The instance.status flips only when the worker's `instance_started`
message arrives. Even after that, the dashboard isn't told — pages only learn
about the change on their next query refetch, and they don't refetch in
sync. Result: three pages show three different statuses for several seconds.

**New flow.**

Start:
1. Dashboard sends `start_instance` (or hits new `POST /api/deployments/:id/start`).
2. Coordinator immediately:
   - Writes `instance.status = "starting"`.
   - Creates a new `AlgorithmRun` row (status `running`, started_at now).
   - Sets `instance.active_run_id = <new run id>`.
   - Broadcasts `deployment_status_changed` to all dashboard ws clients.
3. Coordinator forwards `start_instance` to the worker.
4. On worker `instance_started` ack: coordinator writes
   `instance.status = "running"`, broadcasts `deployment_status_changed`.
5. If worker hasn't acked within 30s (configurable): coordinator writes
   `instance.status = "error"`, marks the run as `error`, broadcasts.

Stop: symmetric — immediate `stopping`, then `stopped` (or `error` on
worker-reported failure or timeout). The run's `stopped_at` is set when the
run terminates.

`deployment_status_changed` payload:
```json
{
  "type": "deployment_status_changed",
  "deployment_id": "...",
  "status": "starting",
  "active_run_id": "..." 
}
```

**Frontend wiring.** Add a handler in `dashboard/src/api/websocket.ts` for
`deployment_status_changed`. The handler invalidates the React Query caches
for `useDeployment(id)`, `useDeployments(algoId)`, `useAllDeployments()`,
`useWorkerDeployments(workerId)`, and `useRuns(deploymentId)`. This forces
all visible pages to refetch the affected deployment in sync.

**Optimistic local cache update.** On Start click, the `useStartDeployment`
mutation writes `status = "starting"` directly into the React Query cache
*before* the API round-trip. Same for Stop. So even network jitter looks
instant in the UI.

### 6.4 Acknowledgement responses

The dashboard's existing `wsManager.send({type: "start_instance", ...})`
call returns no response today, so a failed start was silent. Add an
`ack` message type:
```json
{ "type": "ack", "related_to": "start_instance", "deployment_id": "...", "ok": true }
{ "type": "ack", "related_to": "start_instance", "deployment_id": "...", "ok": false, "error": "..." }
```
Dashboard surfaces an alert on `ok: false`.

---

## 7. Page Polish

### 7.1 WorkerDetail (`/workers/:id`)

- Header: unchanged (worker name + status badge + Edit/Delete buttons).
- Worker info card grid: unchanged, but `Last Heartbeat` now renders correctly
  (Section 6.1).
- **Assigned Instances** section → renamed **Running Algorithms**.
  - Empty state: "No algorithms deployed to this worker."
  - Each row shows:
    | Status | Algorithm | Account | Started | Lifetime P&L |
    - Status: `StatusBadge` with deployment vocabulary.
    - Algorithm: name, linked to `/algorithms/:id`.
    - Account: name, linked to `/accounts/:id`.
    - Started: relative time of current run if running, else "—".
    - Lifetime P&L: dollar amount with color (green/red).
  - Row click → `/deployments/:id`. No GUIDs displayed.
- **Activity** panel below — see Section 5.6.

### 7.2 AlgorithmDetail (`/algorithms/:id`)

- Header, details card, config schema: unchanged.
- **Instances** section → renamed **Running Algorithm Deployments**.
  - Columns change from `[id, status, account_id, worker_id, created_at]` to
    `[status, account, worker, started_at, lifetime_pnl]`.
  - `account` and `worker` are human-readable names (linked).
  - Click → `/deployments/:id`.
- "Create Instance" button → renamed **Deploy**. Modal title becomes
  "Deploy Algorithm", submit label "Deploy". Otherwise unchanged.

### 7.3 Overview

- "Running Instances" widget → renamed **Running Algorithms**.
- Each row: algorithm name · account name · worker name · status badge ·
  lifetime P&L. No GUIDs.
- Clicks navigate to `/deployments/:id`.

### 7.4 Sidebar nav

If there is a top-level "Instances" entry (`/instances`), rename to
**Deployments** and route to `/deployments`. If there is no such entry, no
change.

### 7.5 Hydrated name fields on API responses

Every endpoint that returns deployments populates `algorithm_name`,
`account_name`, `worker_name` via a single joined query — no N+1. Update the
TypeScript `Deployment` type in `dashboard/src/types/` to include these
fields.

### 7.6 Canonical status vocabularies

- **Deployment status**: `stopped | starting | running | stopping | error`.
  This is what the coordinator writes. The dashboard's `StatusBadge` gets
  explicit color + label mappings for each.
- **Worker status**: `offline | online`. Same `StatusBadge` mapping.
- Anything outside the known set renders as a neutral gray pill labeled with
  the raw string (defensive).

---

## 8. Out of Scope (v1)

The following come up naturally but are deliberately deferred so this spec
ships as one cohesive piece:

- Benchmark column on the live `MetricsTable` (the backtest page has it; live
  doesn't in v1).
- Live-tick push channel (Section 3.9 only polls; "watch it breathing" is a
  follow-up).
- Backwards-compatibility shim for the removed `/runs/:id` route beyond a
  simple "deployment moved" message.
- Per-event-type rate limiting for `activity_event` if a misbehaving algo
  spams `signal_produced`. Add if observed in practice.
- Authorization on the new ws subscription model — deferred to whenever auth
  is broadly added.

---

## 9. Implementation Order (suggested)

The plan that follows this spec will split into milestones; rough ordering:

1. **Plumbing fixes & rename surface** (low-risk, high-relief):
   - Section 6.1 (heartbeat tz fix), 6.2 (offline transition), 6.4 (acks).
   - Section 7.5 (hydrated names on list responses).
   - Section 7.6 (status vocabulary in `StatusBadge`).
   - Add `/api/deployments*` aliases; do not yet remove `/api/instances*`.
2. **Optimistic status & websocket broadcast** (Section 6.3) — eliminates
   "slow start" perception. Replace `start_instance` ws verb with a
   coordinator-mediated optimistic flow.
3. **Activity stream** (Section 5) end-to-end: schema → worker emit →
   coordinator persist + broadcast → REST + WS APIs → `ActivityPanel`
   component → worker page integration.
4. **Live data pipeline** (Section 4): worker `LiveObserver` → `LiveSampleSink`
   → `LiveFinalizer` → `AlgorithmDeploymentReport` table → report endpoints.
5. **Deployment page** (Section 3) using existing backtest report
   components.
6. **Page renames & polish** (Section 7) and removal of legacy routes/labels.
7. **Sunset legacy paths** — remove `/api/instances*` and `/instances/:id`
   redirects after one release.
