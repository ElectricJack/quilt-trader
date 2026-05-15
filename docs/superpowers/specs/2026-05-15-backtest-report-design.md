# Backtest Report — Native Dashboard Design

**Status:** Design
**Date:** 2026-05-15
**Replaces:** the quantstats HTML tearsheet currently produced by `coordinator/services/backtest_tearsheet.py`

## Goal

Replace the downloadable QuantStats HTML tearsheet with a native dashboard report page that captures the same analytical depth, integrates with the dark theme, supports interactive drill-down (zoom-to-resolution), and is the single source of truth for "how did this backtest do?".

The reference for analytical depth is the Lumiwealth QuantStats tearsheet (e.g. `OptionsButterflyCondor_2024-06-13_14-20-28_tearsheet.html` — header KPIs, ~50 metrics paired Strategy vs Benchmark, ~13 charts, parameters block, EOY table, top drawdowns table). We curate that down to a focused set without losing the story.

## Non-goals (v1)

- Smart Sharpe / Smart Sortino / Sortino-√2 variants, Treynor Ratio, R², Recovery Factor, Serenity Index, MTD/3M/6M/5Y/10Y/All-time annualized period returns, Avg Up/Down Month, Win Quarter/Year, Information Ratio, Prob. Sharpe — easy to add later, not warranted day one.
- Side-by-side comparison of multiple backtest runs (separate spec).
- Server-side caching of the report payload (the row's denormalized fields *are* the cache).

## Approach summary

- Drop QuantStats HTML output entirely. Use QuantStats only as a **math library** (`qs.stats.*`) so we know our metrics are computed correctly and don't have to maintain bespoke implementations.
- Bake all analytical results at run completion (immutable). The DB row holds everything the report page needs for instant first paint; on-disk parquet pyramid holds the high-resolution series for zoom drill-down.
- Producer-consumer threading inside the runner: the engine thread streams chunks to a writer thread, which appends to parquet. Bounded memory regardless of backtest length.
- Frontend: 4 chart slots (each with view/overlay toggles), stacked-section page layout, dark theme.

---

## Page layout

Stacked sections (single column at narrow widths; 2-column or grid at wide widths):

```
┌─────────────────────────────────────────────────────────┐
│ Header: "Backtest Run" · status badge · Delete button   │
├─────────────────────────────────────────────────────────┤
│ In-flight progress bar (only while running, existing)   │
├─────────────────────────────────────────────────────────┤
│ KPI row:  ┌─CAGR─┐  Total Return | Max DD | RoMaD | DDD │
│           │ HERO │   (4 sub-cards in a row)             │
│           └──────┘                                      │
├─────────────────────────────────────────────────────────┤
│ Charts (2x2 grid at wide widths):                       │
│   ┌─Slot 1: Equity──┐  ┌─Slot 2: Drawdown────┐          │
│   │                 │  │                     │          │
│   └─────────────────┘  └─────────────────────┘          │
│   ┌─Slot 3: Returns─┐  ┌─Slot 4: Rolling─────┐          │
│   │ distribution    │  │ metrics             │          │
│   └─────────────────┘  └─────────────────────┘          │
├─────────────────────────────────────────────────────────┤
│ Side tables (3-column at wide widths):                  │
│   ┌─Parameters──┐  ┌─EOY Returns─┐  ┌─Worst-10 DDs─┐    │
│   └─────────────┘  └─────────────┘  └──────────────┘    │
├─────────────────────────────────────────────────────────┤
│ Strategy vs Benchmark — Key Performance Metrics table   │
│   (~28 metrics in 8 grouped rows, 3-column: Metric │    │
│    Strategy │ Benchmark)                                │
├─────────────────────────────────────────────────────────┤
│ Trades table (existing, paginated)                      │
└─────────────────────────────────────────────────────────┘
```

### Chart slots

Each slot uses `lightweight-charts` (existing dependency) except where noted.

#### Slot 1 — Equity

- Default series: Strategy equity
- Overlay toggles: Benchmark, Cash, Trade markers (existing toggles), plus **Log scale**, **Vol-matched curve** (benchmark scaled to match strategy volatility — `qs.stats.compsum` of a vol-matched series).
- Progressive zoom: starts at 1day. On zoom past `~60 visible days` → fetch 1hour series for the visible window. On zoom past `~3 days` → fetch native (e.g. 1min). Replaces the visible series with the higher-resolution slice.
- Trade markers always rendered at exact intra-day timestamps regardless of equity series resolution.

#### Slot 2 — Drawdown

- View toggle: **Underwater plot** (default) ↔ **Top drawdown periods** (bar chart of depth × duration).
- Underwater: line chart of cumulative drawdown from running peak (data: `drawdown_curve`, daily).
- Top periods: horizontal bar chart of the top-10 drawdown periods (depth on x-axis, label = "YYYY-MM-DD → YYYY-MM-DD").

#### Slot 3 — Returns distribution

- View toggle: **Monthly heatmap** (default) ↔ **EOY returns bar** ↔ **Daily returns histogram** ↔ **Daily returns scatter** (time series).
- Monthly heatmap: custom CSS-grid component (year rows × 12 month columns), color-graded green (gain) / red (loss). Tooltip shows exact return.
- EOY bar: vertical bars per year, Strategy vs Benchmark side by side.
- Histogram: distribution of daily returns, ~30 bins, computed client-side from the daily equity curve.
- Scatter: time series of daily returns as dots (lightweight-charts histogram series).

#### Slot 4 — Rolling metrics

- Single time-series chart with multi-line toggles: **Rolling Sharpe** (default), **Rolling Sortino**, **Rolling Volatility**, **Rolling Beta** (vs benchmark).
- Window: 90 trading days (configurable later; v1 fixed).
- Data source: `rolling_metrics` column from the row.

### KPI cards

- **Hero**: CAGR (Annual Return)
- **Sub-cards** (in order): Total Return, Max Drawdown, RoMaD, Longest DD Days

Each card pulls from `key_metrics.strategy.{cagr, total_return, max_drawdown, romad, longest_dd_days}`.

### Strategy vs Benchmark metrics table

| Group | Metrics |
|---|---|
| Returns | Total Return · CAGR (Annual) · Volatility (ann.) |
| Risk-adjusted | Sharpe · Sortino · Omega |
| Drawdown | Max Drawdown · Longest DD Days · Avg Drawdown · Avg DD Days · Ulcer Index |
| Tail risk | Daily VaR (95%) · Daily cVaR · Skew · Kurtosis |
| Period returns | YTD · 1Y · 3Y (annualized) |
| Distribution | Best Day · Worst Day · Best Month · Worst Month |
| Win rates | Time in Market · Win Days % · Win Month % |
| vs Benchmark *(strategy-only column)* | Beta · Alpha · Correlation |

28 metrics. Each row is `(label, strategy_value, benchmark_value)`; vs-Benchmark group has an em-dash in the benchmark column.

### Side tables

- **Parameters Used** — rendered from `BacktestRun.config_overrides` (the user-supplied param dict). 2-column key/value table. If empty, hide the card.
- **EOY Returns vs Benchmark** — from `eoy_returns`: `[{year, strategy_pct, benchmark_pct, multiplier, won}]`.
- **Worst-10 Drawdowns** — from `drawdown_periods` (expanded from current top-5 to top-10): `[{started, recovered, depth_pct, days}]`.

---

## Backend

### Schema changes — `BacktestRun`

Add (all `JSON` columns, populated at run completion):

| Column | Shape |
|---|---|
| `key_metrics` | `{ strategy: {sharpe, sortino, ...}, benchmark: {sharpe, sortino, ...} }` |
| `rolling_metrics` | `{ window_days: 90, points: [{ts: ISO, sharpe, sortino, vol, beta}, ...] }` |
| `monthly_returns_matrix` | `{ years: [int], cells: [[year, month, ret_pct], ...] }` |
| `eoy_returns` | `[{year, strategy_pct, benchmark_pct, multiplier, won}]` |
| `benchmark_equity_curve` | `[{ts, value}]` (daily, normalized to `initial_cash`) |
| `drawdown_curve` | `[{ts, drawdown_pct}]` (daily underwater curve) |

Modify:

- `equity_curve`: now stored at **daily resolution** (resampled from native at run end). Existing column, semantics change.
- `drawdown_periods`: expand from top-5 to **top-10**. Existing column, schema unchanged but quantity increases.

Remove:

- `tearsheet_path` — drop the column. (Migration: alembic revision drops the column; existing rows lose nothing observable in the new UI.)

### On-disk parquet pyramid

In `data/backtests/{run_id}/`:

```
equity_native.parquet      # raw observer output, written incrementally during run
equity_1hour.parquet       # resampled at finalize, only if native < 1h
equity_1day.parquet        # resampled at finalize, always
benchmark_native.parquet   # if benchmark configured + native < 1day
benchmark_1hour.parquet    # if benchmark configured + native < 1h
benchmark_1day.parquet     # if benchmark configured
trades.parquet             # all trades, full detail
```

Schema (equity files): `timestamp: timestamp[ns]`, `portfolio_value: float64`, `cash: float64`.
Schema (trades file): all FillRecord fields.

The 1day series is **also mirrored** in the `BacktestRun.equity_curve` and `benchmark_equity_curve` JSON columns so the report's first paint is one DB query — no parquet read on initial load. Higher resolutions are only read by the windowed-zoom endpoint.

### Producer-consumer ingestion

Replace the current "accumulate everything in `_RunObserver` then flush at end" with a streaming pipeline.

#### Threads

- **Engine thread** — existing `loop.run_in_executor(engine.run, ...)`. Calls `observer.on_equity_point()` and `observer.on_fill()` per tick.
- **Writer thread** — new `threading.Thread`. Drains a `queue.Queue(maxsize=8)` of chunks; appends each chunk to the open `pyarrow.parquet.ParquetWriter` for the native file.
- **Main asyncio task** — orchestrates both. Existing progress-pump task continues to write `progress_pct` to the DB every 2s, and now also writes the latest daily-resampled equity slice (read from the writer thread's daily aggregate) so the live chart grows during the run.

#### Chunk granularity

Time-based, adaptive. At run start:

```python
total_days = (clock_series['timestamp'].iloc[-1] - clock_series['timestamp'].iloc[0]).days + 1
avg_ticks_per_day = max(1, len(clock_series) / total_days)
days_per_chunk = clamp(ceil(5_000 / avg_ticks_per_day), 1, 30)
```

The `ChunkingObserver` accumulates rows for `days_per_chunk` consecutive simulated days, then emits a `{equity: [...], trades: [...], window_start, window_end}` chunk to the queue. Day boundary is detected by comparing the date portion of `sim_time` across consecutive `on_equity_point` calls. At the end of the run, any partial chunk is flushed.

Constants:
- `TARGET_TICKS_PER_CHUNK = 5_000`
- `MIN_DAYS_PER_CHUNK = 1`
- `MAX_DAYS_PER_CHUNK = 30`

#### Backpressure

Queue's `maxsize=8` blocks the engine thread on `queue.put()` if the writer falls behind. Memory ceiling: `chunk_size × maxsize × bytes_per_row` ≈ a few MB.

#### Failure handling

- Writer thread wraps its loop in try/except. On exception it sets a shared `error: Optional[Exception]` and drains the queue without writing.
- Engine thread checks `observer.writer_error` between bars; if set, raises to abort the engine.
- `runner.run`'s `try/except Exception` catches either side; the existing `failed` status path applies.
- `finally:` always puts the sentinel on the queue and `writer.join(timeout=30)` so the parquet writer is closed cleanly.

#### Finalize (after both threads done)

1. Read `equity_native.parquet` → resample to 1day (and 1hour if native < 1h) → write `equity_1day.parquet` (and `equity_1hour.parquet`).
2. Same for benchmark.
3. Compute all metrics via `qs.stats.*` on the daily series (Strategy + Benchmark).
4. Compute `monthly_returns_matrix` via `qs.stats.monthly_returns()`.
5. Compute `drawdown_periods` (top-10) via `qs.stats.drawdown_details()`.
6. Compute `rolling_metrics` (90d window): rolling Sharpe / Sortino / Volatility via `qs.stats.rolling_*` where available; rolling Beta computed by `pandas.Series.rolling(window=90).apply(beta_fn)` against the daily benchmark series. Exact `qs` API to use is an implementation-plan detail.
7. Compute `eoy_returns` from the daily series.
8. Single DB transaction: write `equity_curve` (daily), `benchmark_equity_curve` (daily), `key_metrics`, `rolling_metrics`, `monthly_returns_matrix`, `eoy_returns`, `drawdown_periods`, `drawdown_curve`. Mark `status=completed`.

### Quantstats wrapper

Replace `coordinator/services/backtest_metrics.py`'s bespoke implementations with a thin wrapper that calls `qs.stats.*` for each metric. Keep the existing function signatures (`total_return`, `cagr`, `sharpe_ratio`, ...) so the runner doesn't need to change. New file: `coordinator/services/backtest_metrics_qs.py` containing the qs-backed implementations; switch the runner's import. Once green in production, delete the old `backtest_metrics.py`.

Existing tests in `tests/coordinator/services/test_backtest_metrics.py` get re-run against the new wrapper. Tolerance for floating-point discrepancies needs to be lifted slightly (qs and our hand-rolled versions can differ by 1-2 in the trailing decimal place); concretely, change `assert x == pytest.approx(y)` to `pytest.approx(y, rel=1e-3)`.

---

## API

### `GET /api/backtest-runs/{id}/report`

Returns everything the report page needs for first paint. Read directly from the DB row — no parquet I/O.

```jsonc
{
  "id": "...",
  "algorithm_id": "...",
  "status": "completed",
  "date_range_start": "...",
  "date_range_end": "...",
  "initial_cash": 100000.0,
  "config_overrides": { ... },         // for Parameters table
  "key_metrics": { strategy: {...}, benchmark: {...} },
  "equity_curve": [...],               // daily, mirrored from parquet
  "benchmark_equity_curve": [...],     // daily
  "drawdown_curve": [...],             // daily
  "rolling_metrics": { window_days: 90, points: [...] },
  "monthly_returns_matrix": { years: [...], cells: [...] },
  "eoy_returns": [...],
  "drawdown_periods": [...]            // top-10
}
```

### `GET /api/backtest-runs/{id}/equity?from=ISO&to=ISO&resolution=1min|1hour|auto`

Reads the appropriate parquet file from disk, slices to the window, returns only the points in range. `resolution=auto` picks the highest resolution available for the given window size that produces ≤5000 points.

```jsonc
{
  "resolution": "1hour",
  "items": [{ "ts": "...", "portfolio_value": ..., "cash": ... }]
}
```

The frontend chart calls this endpoint when the visible window crosses the 60d / 3d zoom thresholds.

### `DELETE /api/backtest-runs/{id}` (existing — extend)

Add: `shutil.rmtree(Path("data/backtests")/run_id, ignore_errors=True)` before deleting the row. Replaces the existing single-file `tearsheet_path` cleanup.

### Removed

- `GET /api/backtest-runs/{id}/tearsheet` — delete the route.
- `GET /api/backtest-runs/{id}/equity-curve` — delete the route. The frontend hook (`useBacktestEquityCurve`) is also deleted; the daily series is served as part of `/report`.

---

## Frontend

### Components

`BacktestRunDetail.tsx` (existing) stays as the page; its body is rewritten to render the new components below. The header (title, status badge, Delete button), in-flight progress bar, and trades table are kept as-is. The current `metrics grid` and `equity curve` sections are removed in favor of the new component tree.

New components in `dashboard/src/components/report/`:
- `KpiCard.tsx` — hero + sub-card variants.
- `MetricsTable.tsx` — Strategy vs Benchmark grouped table.
- `EquitySlot.tsx` — wraps the existing `BacktestChart` with new toggle UI (log scale, vol-matched) + progressive-zoom data fetcher.
- `DrawdownSlot.tsx` — view toggle: underwater ↔ top-N bars.
- `ReturnsDistributionSlot.tsx` — view toggle: heatmap ↔ EOY bar ↔ histogram ↔ scatter.
- `RollingMetricsSlot.tsx` — multi-line chart with line toggles.
- `MonthlyHeatmap.tsx` — custom CSS-grid year × month component.
- `DrawdownsTable.tsx`, `EoyTable.tsx`, `ParametersTable.tsx` — small side tables.

Note: the EOY data appears in two places (Slot 3 EOY-bar view and the EOY side table). Intentional duplication — chart-vs-table is a usability choice, both pull from the same `eoy_returns` field.

### Hooks (new)

- `useBacktestReport(id, opts?)` — fetches `/report`. Polls while in-flight (existing pattern).
- `useBacktestEquityWindow(id, from, to, resolution)` — fetches `/equity` for a window; called by `EquitySlot` on zoom.

### Removed

- `useBacktestEquityCurve` — replaced by data inside `/report`. Delete the hook.
- `tearsheet_path` references in `BacktestRunDetail.tsx`. Delete the "Download tearsheet" anchor.

### Progressive zoom (Slot 1)

`lightweight-charts` exposes `subscribeVisibleTimeRangeChange`. The `EquitySlot` component:

1. Initial: render `equity_curve` (daily) from the report payload.
2. On visible-range change, compute window in days. If `< 3 days` → request `resolution=1min`; if `< 60 days` → `1hour`; else stay at daily.
3. Debounce 300ms, fetch via `useBacktestEquityWindow`, replace the visible series with the higher-resolution slice.
4. Trade markers always rendered from the run's `trades` data (intra-day timestamps preserved).

---

## Removals (recap)

- `coordinator/services/backtest_tearsheet.py` — delete.
- `quantstats` dependency — **keep** (math library use). Remove only the `qs.reports.html` call.
- `BacktestRun.tearsheet_path` column — alembic migration drops it.
- `GET /api/backtest-runs/{id}/tearsheet` route + `data/backtests/{id}/tearsheet.html` files (existing files cleaned up by next delete; new runs don't generate one).
- "Download tearsheet" button in `BacktestRunDetail.tsx`.

---

## Migrations

Single alembic revision:

1. `ADD COLUMN` six new JSON columns on `backtest_runs`: `key_metrics`, `rolling_metrics`, `monthly_returns_matrix`, `eoy_returns`, `benchmark_equity_curve`, `drawdown_curve`.
2. `DROP COLUMN tearsheet_path`.

Existing rows: new columns default `NULL`. The report page renders an "incomplete data — re-run this backtest" banner for any row missing `key_metrics` (the canonical "is this a new-format run?" sentinel).

---

## Testing

- `tests/coordinator/services/test_backtest_metrics.py` — re-run against the qs wrapper; loosen tolerance to `rel=1e-3`.
- `tests/coordinator/services/test_backtest_runner.py` — add: chunk emission on day boundary, writer thread parquet output, finalize step writes all expected columns, failure in writer thread aborts engine cleanly.
- New: `tests/coordinator/services/test_backtest_writer.py` — unit tests for the writer thread in isolation (queue → parquet append → daily aggregate).
- New: `tests/coordinator/test_backtest_report_api.py` — `/report` endpoint shape, `/equity` resolution selection, `DELETE` cleans up the parquet directory.
- Frontend: extend `dashboard/src/pages/Accounts.test.tsx` pattern for the new `BacktestReport` page — render with mocked `/report` payload, check each chart slot mounts.

---

## Open / deferred

- **Streaming-write resilience**: if the coordinator process dies mid-run, parquet writers leak. Acceptable for v1; followup is to recover by reading the partial parquet on coordinator restart and marking the run failed.
- **Smart Sharpe / Smart Sortino**: easy to add to the metrics table later if desired.
- **Multi-run comparison**: separate page (existing `/backtests` Comparisons tab is the placeholder).
- **Custom rolling window**: v1 fixes window at 90 days; could become a UI toggle.
- **Heatmap color scale**: v1 uses fixed thresholds (e.g. ±10% per month); could auto-scale to dataset.
- **Orphaned tearsheet.html files**: pre-existing `data/backtests/{run_id}/tearsheet.html` from old runs aren't proactively deleted. They're harmless (no longer linked from the UI) and get removed naturally when those runs are deleted via the existing delete handler. A one-time cleanup is not in scope.
