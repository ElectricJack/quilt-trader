---
title: Dashboard-Driven Backtest Execution
status: design
date: 2026-05-14
---

# Dashboard-Driven Backtest Execution — Design

Run any installed algorithm against historical data from the dashboard. The coordinator inspects the algorithm's `data_dependencies`, fetches missing data via the existing `DownloadManager`, simulates fills with configurable fees + slippage, computes Lumibot-grade metrics, and presents results (including an optional quantstats HTML tearsheet) on a dedicated backtest detail page.

## Goals

1. One-click backtest from `AlgorithmDetail.tsx`: pick date range + initial cash + fee/slippage config + benchmark → submit.
2. Coordinator auto-downloads any missing data declared in `data_dependencies`.
3. Realistic fill simulation: fees (Lumibot's `TradingFee` shape — flat + percent + maker/taker) and slippage (basis-points-from-requested-price, configurable per run; default 5 bps for market orders).
4. **Framework-enforced no-look-ahead**: `ctx.market_data()` only returns bars whose close time ≤ current simulation time.
5. Multi-timeframe algorithms are clocked at the smallest declared timeframe.
6. Per-fill trade log + per-bar equity curve recorded into the DB.
7. Aggregate metrics surfaced: total return, CAGR, Sharpe, Sortino, Calmar, max drawdown, RoMaD, total fees, total slippage, win rate, profit factor, expectancy.
8. Optional quantstats HTML tearsheet (Lumibot-style) downloadable from the detail page.
9. Backtest runs visible in the existing Backtests tab (extended).

## Non-Goals

- Replacing the existing `BacktestComparison` (live-vs-parallel-backtest divergence). That's a different feature; leave it intact. The dashboard's Backtests page splits into two tabs: "Runs" (new) and "Comparisons" (existing).
- Tick-level backtesting. The smallest unit is the smallest bar timeframe declared by the algorithm.
- Options-strategy backtesting in v1. Equities + crypto bar data only. Options chains for historical IV are out of scope (algorithms can reference historical option prices via their own data dependencies, but the engine doesn't simulate options-specific fills like assignment).
- Walk-forward optimization / parameter sweeps. Single run per submission. Multiple-run kickoffs are allowed (user can submit N runs with different configs); the system runs them concurrently up to a coordinator-level concurrency cap.

---

## 1. Database

### New table `backtest_runs`

```python
class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    algorithm_id: Mapped[str] = mapped_column(String, ForeignKey("algorithms.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    # queued | downloading_data | running | completed | failed | cancelled

    # Inputs
    date_range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    date_range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    initial_cash: Mapped[float] = mapped_column(Float, nullable=False, default=100_000.0)
    config_overrides: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    buy_trading_fees: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)   # list[TradingFee dicts]
    sell_trading_fees: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    slippage_model: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)     # SlippageModel dict
    benchmark_symbol: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # e.g. "SPY"
    benchmark_source: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # e.g. "polygon"

    # Progress
    progress_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    progress_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Results (populated on completion)
    total_return: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cagr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volatility: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sharpe_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sortino_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    calmar_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_drawdown_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    romad: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_fees_paid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_slippage_dollars: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trade_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    win_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    profit_factor: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_win: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    expectancy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longest_drawdown_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    longest_winning_streak: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    longest_losing_streak: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Large blobs
    equity_curve: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)   # list[{timestamp, portfolio_value, cash, positions[]}]
    trades: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)         # list[trade dicts]
    drawdown_periods: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)  # top-N worst periods

    # Side artifacts
    tearsheet_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # data/backtests/{id}/tearsheet.html
    download_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)     # MarketDataDownload.id refs for dependent downloads

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

The `equity_curve` and `trades` columns are JSON for simplicity (one row per backtest, no separate child tables). For long backtests (~100k bars) they get large — acceptable for now since SQLite handles large JSON cells fine and read-side rendering is paginated. If we later hit a wall, split into child tables.

### Migration

One alembic migration that creates `backtest_runs`. No other schema changes.

---

## 2. Configuration shapes

### `TradingFee` (matches Lumibot's API)

```python
class TradingFee(BaseModel):
    flat_fee: float = 0.0       # currency per order
    percent_fee: float = 0.0    # decimal, 0.001 = 0.1%
    maker: bool = True          # applies to limit / stop_limit
    taker: bool = True          # applies to market / stop
```

Per-order fee = sum of all applicable `TradingFee` rows. A run's `buy_trading_fees` and `sell_trading_fees` are stored separately so users can model asymmetric fee schedules.

**Defaults:** both lists empty (no fees), matching Alpaca + Tradier's $0-commission stock policy.

**Presets** (frontend dropdown selectable):
- `none` — empty lists (default)
- `alpaca-equities` — empty lists (Alpaca's policy is genuinely $0)
- `tradier-options` — `flat_fee=0.35` per contract (Tradier's per-contract fee). Note: applies per-contract, not per-order — the engine multiplies by `quantity` when `asset_type == "options"`.
- `custom` — user edits fields directly

### `SlippageModel`

```python
class SlippageModel(BaseModel):
    market_bps: float = 5.0           # bps from requested for market orders (buys add, sells subtract)
    limit_bps: float = 0.0            # bps for limit orders (usually 0 — limit is the worst price by definition)
    use_bar_range: bool = False       # if True, market fills sampled uniformly from next bar's [low, high]
    volume_impact_bps_per_pct: float = 0.0  # additional bps per % of bar's volume consumed
```

**Defaults:** `market_bps = 5.0`, `use_bar_range = False`, `volume_impact_bps_per_pct = 0.0` (idealized but with realistic floor).

When the run's `slippage_model` is null, we apply the defaults.

---

## 3. Engine

### Clock and look-ahead enforcement

The simulation maintains a single `sim_time_now: datetime`. At each step, `sim_time_now` is advanced to the close time of the next bar in the **smallest-timeframe** dependency, and `algorithm.on_tick(ctx)` is called.

`BacktestTickContext.market_data(symbol, timeframe, bars=100, source=None)` enforces look-ahead prevention:

```python
def market_data(self, symbol, timeframe="1min", bars=100, source=None):
    df = self._load_cached(source, symbol, timeframe)
    duration = self._timeframe_to_seconds(timeframe)
    # "Available" = bar's close time is in the past (or equal to now)
    cutoff = self._sim_time_now
    df = df[df["timestamp"] + pd.Timedelta(seconds=duration) <= cutoff]
    return df.tail(bars)
```

So a 1min-clocked algorithm asking for SPY's 1day bar at sim-time 2024-01-15 14:30 ET only sees through 2024-01-14's close — never the in-progress 2024-01-15 day. This is **non-negotiable at the framework level** and isn't bypassable by algorithm code.

### Step loop

```python
clock_series = primary_series_for_smallest_timeframe(manifest.data_dependencies)
algorithm.on_start(config_overrides, restored_state=None)

for bar in clock_series:
    sim_time_now = bar.timestamp + timeframe_duration(clock_series.timeframe)
    ctx.set_sim_time(sim_time_now)
    signals = algorithm.on_tick(ctx)
    for signal in signals:
        for leg in signal.legs:
            # Schedule fill for NEXT bar's open (market) or check intrabar touch (limit) on NEXT bar
            pending_orders.append(PendingOrder(signal_id=signal.id, leg=leg, scheduled_for=next_bar_timestamp))

    # Apply any pending orders scheduled at-or-before this bar
    process_pending_orders(pending_orders, current_bar=bar, slippage_model, trading_fees, ctx)

    # Mark-to-market portfolio value
    portfolio_value = cash + sum(qty * current_close for symbol, qty in positions.items())
    equity_curve.append({"timestamp": sim_time_now, "portfolio_value": portfolio_value, "cash": cash, "positions": [...]})

state = algorithm.on_stop()  # we discard state for backtest, but call it for symmetry with live
```

### Fill simulation

For each `PendingOrder` resolving in the current bar:

**Market order:**
1. Fill price = `bar.open` (next bar after signal was emitted).
2. Apply slippage:
   - `slip = bar.open * (slippage_model.market_bps / 10000) * (+1 if buy else -1)`
   - `fill_price = bar.open + slip`
   - If `use_bar_range`: instead, `fill_price = uniform(bar.low, bar.high)` (uses random.uniform with a seeded RNG so backtests are reproducible).
   - If `volume_impact_bps_per_pct > 0` AND `bar.volume > 0`: `extra_bps = (qty / bar.volume * 100) * volume_impact_bps_per_pct`; apply on top of `market_bps`.
3. Compute fees: sum applicable `buy_trading_fees` (taker rules) on buy, `sell_trading_fees` on sell.
   - `fee = sum(tf.flat_fee + fill_price * qty * tf.percent_fee for tf in applicable_fees)`
   - Special case: `tradier-options` preset's per-contract flat fee → flat_fee × qty when `asset_type == "options"`.
4. Cash impact: `cash -= side_sign(side) * (fill_price * qty) + fee`.

**Limit order:**
1. Filled if `bar.low <= limit_price <= bar.high` for buys, or `bar.low <= limit_price <= bar.high` for sells (limit can be filled either direction within range).
2. Fill price = `limit_price` (limit ≠ next bar open — limit fills AT the limit if touched).
3. Apply limit_bps slippage if non-zero (usually 0).
4. Fees use maker rules from `TradingFee`.

**Order not filled in current bar:**
- Default: remains pending up to a configurable timeout (default: end of trading day for day algorithms, or N bars for intraday). Cancelled if unfilled by deadline. For v1, **default is 1 bar** — keep it simple. If you want GTC behavior we can extend later.

### Position tracking

Internal dict: `positions: dict[str, {qty, avg_price, asset_type}]`. On fill:
- Buy adds to qty, updates avg_price (weighted).
- Sell reduces qty (or goes negative for shorts — only if algo allows shorts in the manifest, otherwise reject). Realized PnL recorded against the closed portion.
- Closing the full position emits a "round-trip trade" record used for win-rate/expectancy calculations.

### Per-tick stop conditions

- Cancel signal from API → engine notices on next iteration and exits cleanly, status → `cancelled`.
- Unhandled exception in `algorithm.on_tick` → caught, status → `failed`, `error_message` populated with traceback.

---

## 4. Orchestration (`coordinator/services/backtest_runner.py`)

```python
class BacktestRunner:
    async def run(self, run_id: str) -> None:
        # 1. Load BacktestRun row + Algorithm row
        # 2. Load manifest from data/packages/{algo.name}/quilt.yaml
        # 3. Resolve data_dependencies + benchmark to (source, symbol, timeframe) tuples
        # 4. For each, check DataService whether the parquet exists with coverage >= [date_range_start, date_range_end]
        # 5. For missing/short ones, call container.download_manager.create_download(...) and track the resulting download_id
        # 6. Update status="downloading_data", progress_message="Downloading SPY 1day from polygon (1/3)..."
        # 7. Poll the downloads' status; advance progress_message per completion
        # 8. When all downloads are 'completed', load the parquets, build BacktestTickContext
        # 9. Load the algorithm class (via PackageManager's installed venv path)
        # 10. status="running", invoke BacktestEngine.run() — engine streams progress via a callback
        # 11. On engine finish: compute aggregate metrics, persist, optionally generate quantstats tearsheet, status="completed"
        # 12. On engine exception: status="failed", error_message=str(e) + traceback
```

The runner is launched as a coordinator-process asyncio task when the user POSTs to create a run. Multiple concurrent runs allowed up to `MAX_CONCURRENT_BACKTESTS = 4` (per coordinator config); excess queue.

### Download dedup

The runner checks `MarketDataDownload` for any rows matching `(provider, symbol, data_type, timeframe, date_range_overlapping)`. If one exists in `queued` or `running`, just `await` its completion (subscribe to its task). If it's `completed`, skip download entirely.

### Coverage check

```python
def has_coverage(svc, source, symbol, timeframe, start, end) -> bool:
    df = svc.load_market_data(source, symbol, timeframe)
    if df is None or df.empty:
        return False
    return df["timestamp"].min() <= start and df["timestamp"].max() >= end
```

If `False`, enqueue a download from `df["timestamp"].max()` (or `start`) to `end`. The `DataService.save_market_data` already handles overlap merges.

---

## 5. Metrics

All metrics computed at run finalization, after the equity curve is complete. Stored on the `BacktestRun` row.

```python
def compute_metrics(equity_curve, trades, initial_cash, risk_free_rate=0.04):
    ec = pd.DataFrame(equity_curve).set_index("timestamp")
    ec["return"] = ec["portfolio_value"].pct_change().fillna(0)
    daily = ec.resample("D").last().dropna()
    daily["return"] = daily["portfolio_value"].pct_change().fillna(0)

    return {
        "total_return": daily["portfolio_value"].iloc[-1] / initial_cash - 1,
        "cagr": _cagr(daily),
        "volatility": daily["return"].std() * sqrt(252),
        "sharpe_ratio": _sharpe(daily, risk_free_rate),
        "sortino_ratio": _sortino(daily, risk_free_rate),
        "calmar_ratio": _calmar(daily),
        "max_drawdown": _max_drawdown(daily)["drawdown"],
        "max_drawdown_date": _max_drawdown(daily)["date"],
        "romad": _romad(daily),
        "total_fees_paid": sum(t["fees"] for t in trades),
        "total_slippage_dollars": sum(t["slippage_dollars"] for t in trades),
        "trade_count": _round_trip_count(trades),
        "win_rate": _win_rate(trades),
        "profit_factor": _profit_factor(trades),
        "avg_win": _avg_win(trades),
        "avg_loss": _avg_loss(trades),
        "expectancy": _expectancy(trades),
        "longest_drawdown_days": _longest_drawdown(daily),
        "longest_winning_streak": _longest_streak(trades, win=True),
        "longest_losing_streak": _longest_streak(trades, win=False),
        "drawdown_periods": _top_n_drawdowns(daily, n=5),
    }
```

Implementations live in `coordinator/services/backtest_metrics.py` (new). Each metric is its own function, tested individually with reference values (Hull / standard finance textbook examples).

### Win-rate, profit-factor, etc. — "trade" definition

Computed against **round-trip closed positions**, not individual fills. A round-trip is: position opens at time T1, closes at time T2, realized_pnl is the difference. `_round_trip_count(trades)` walks the trade log and pairs opens with closes (FIFO within a symbol).

---

## 6. quantstats HTML tearsheet

Add `quantstats` to `pyproject.toml`. At run finalization:

```python
import quantstats as qs

returns = daily["return"]
returns.name = algo.name

if backtest_run.benchmark_symbol:
    benchmark_df = svc.load_market_data(benchmark_source, benchmark_symbol, "1day")
    bench_returns = benchmark_df["close"].pct_change().dropna()
    bench_returns.name = benchmark_symbol
else:
    bench_returns = None

out_path = f"data/backtests/{run_id}/tearsheet.html"
os.makedirs(os.path.dirname(out_path), exist_ok=True)
qs.reports.html(returns, benchmark=bench_returns, output=out_path,
                title=f"{algo.name} backtest", rf=0.04)
backtest_run.tearsheet_path = out_path
```

If quantstats raises (e.g. too few data points for a meaningful tearsheet), log + skip; `tearsheet_path` stays null. Don't fail the run for that.

---

## 7. API surface

New router `coordinator/api/routes/backtest_runs.py`, prefix `/api/backtest-runs`:

| Method | Path | Notes |
|---|---|---|
| `POST` | `/` | Body: `{algorithm_id, date_range_start, date_range_end, initial_cash, config_overrides?, buy_trading_fees?, sell_trading_fees?, slippage_model?, benchmark_symbol?, benchmark_source?}`. Creates the row + spawns the runner task. Returns the row (status: `queued`). |
| `GET` | `/` | List, with optional `?algorithm_id=...` filter, paginated `?limit=&offset=`. |
| `GET` | `/{id}` | Detail — all fields. |
| `DELETE` | `/{id}` | If status is `queued`/`downloading_data`/`running`, set a cancel flag; engine notices and exits cleanly, status → `cancelled`. Then delete row. If `completed`, just delete row + tearsheet file. |
| `GET` | `/{id}/tearsheet` | Streams the HTML file from `tearsheet_path`, 404 if missing. |
| `GET` | `/{id}/equity-curve` | Returns the equity_curve JSON (kept separate so list endpoints don't fetch the blob). |
| `GET` | `/{id}/trades` | Same for trades. Supports `?limit=&offset=` for paging. |

The existing `/api/backtests/` endpoints (for `BacktestComparison`) stay untouched.

---

## 8. UI changes

### `AlgorithmDetail.tsx` — Run Backtest button

Header gets a new **"Run Backtest"** button next to Update / Delete. Opens `RunBacktestModal`:

- **Date range**: two date pickers, default = (today minus 1 year, today).
- **Initial cash**: number input, default 100000.
- **Config overrides**: auto-populated from manifest `config.parameters`; each parameter rendered with its declared type + default. User can override.
- **Fees**: preset dropdown (`none` / `alpaca-equities` / `tradier-options` / `custom`) + collapsible "Custom fee editor" showing the two `TradingFee[]` arrays as editable rows.
- **Slippage**: collapsible. Defaults to `market_bps: 5.0`. Sliders for `market_bps` and `volume_impact_bps_per_pct`; toggle for `use_bar_range`.
- **Benchmark**: dropdown of available historical symbols (queries `/api/data/available`), defaults to SPY if available, else first equities symbol.
- **Submit** → POSTs, navigates to `/backtest-runs/{id}`.

### `Backtests.tsx` — split into tabs

Tabs at the top:
- **Runs** — new. Lists `BacktestRun` rows. Columns: created_at, algorithm name, status badge, date range, total_return %, sharpe, trade_count. Click row → `BacktestRunDetail`.
- **Comparisons** — existing. Lists `BacktestComparison` rows (the live-vs-parallel-backtest divergence reports).

### `BacktestRunDetail.tsx` — new page at `/backtest-runs/:id`

Sections, top-to-bottom:

1. **Header**: algorithm name, date range, status badge with progress bar (when not terminal). Cancel button if status is in-flight.
2. **Metrics grid**: all stored metrics in a clean grid. Color-coded (green positive, red negative). Mouse-over each metric shows a one-line definition.
3. **Equity curve chart**: uses `lightweight-charts`, two series (strategy portfolio_value, optional benchmark normalized to initial_cash). Drawdown shading underneath.
4. **Drawdown periods table**: top 5 worst drawdowns with start/end dates + recovery duration.
5. **Trades table**: paginated, columns = timestamp, symbol, side, qty, requested_price, fill_price, slippage_$, fees, realized_pnl. Click row → expand to show the source signal/decision log if available.
6. **"Download tearsheet"** button — direct link to `/api/backtest-runs/{id}/tearsheet`.

Polling: while status is non-terminal, the page polls `/api/backtest-runs/{id}` every 2s to update progress + flip to results when complete.

---

## 9. Look-ahead enforcement — test coverage

This is a correctness-critical area. The plan must include dedicated tests:

1. **`test_market_data_filters_future_bars`**: build a `BacktestTickContext` with a 1day bar series spanning Jan 1-30; set `sim_time_now = 2026-01-15 14:30 UTC`; call `ctx.market_data("SPY", "1day", 100)`; assert the returned df's max timestamp is `2026-01-14` (the most recent fully-closed daily bar).
2. **`test_multi_timeframe_no_lookahead`**: algorithm has SPY 1day + SPY 1min deps. At sim-time mid-day, asserting `ctx.market_data("SPY", "1day")` does NOT include today's bar even though today's 1min bars are partly accessible.
3. **`test_pending_order_fills_next_bar_not_current`**: a signal emitted on bar T causes a fill at bar T+1's open, not at bar T's close.
4. **`test_signal_with_future_strike_rejected`**: an algorithm trying to read forward via any other context method also can't (the abstract `TickContext` enforces it).

If any of these regress, the feature is broken at its core.

---

## 10. Cross-cutting concerns

### Dependencies

Add `quantstats` to `pyproject.toml`. Note it pulls in `matplotlib`, `statsmodels`, `seaborn`, `tabulate` — non-trivial. Acceptable cost for the tearsheet payoff. Pin `quantstats>=0.0.62`.

### Compatibility

- Existing `BacktestComparison` table + endpoints unchanged.
- Existing `sdk/cli/backtest.py` (the stub Lumibot CLI wrapper) — leave it alone; it's not used by this feature. Optionally delete in a later cleanup.
- The new `BacktestTickContext` implements `sdk/context.py:TickContext` so any algorithm written against the live SDK works in backtest with no changes.

### Operational

- Concurrent backtests: capped at 4 per coordinator. Excess `queued` until a slot frees.
- Long-running backtests: a 1min-clocked 1-year backtest is ~100k iterations × `on_tick` work — large algorithms may take minutes. Engine yields control periodically (every 1000 iterations: `await asyncio.sleep(0)`) so other coordinator work doesn't starve.
- Disk: `data/backtests/{run_id}/tearsheet.html` per completed run, typically 200KB-2MB. Optional cleanup (out of scope) when rows are deleted.

### Testing strategy

- **Engine fill semantics**: unit tests with a hand-crafted bar series + fixed RNG seed. Cover: market buy at next-open + slippage, market sell at next-open + slippage, limit fill (range crosses), limit no-fill (range doesn't cross), use_bar_range mode, volume_impact tier, fees applied correctly per maker/taker, asymmetric buy/sell fees.
- **Look-ahead enforcement**: the 4 tests in §9.
- **Metrics**: every function in `backtest_metrics.py` tested against textbook values (Hull's worked examples for Sharpe; standard win-rate / profit-factor against known trade logs).
- **Orchestrator**: API test that creates a run, mocks `DownloadManager.create_download` to return immediately, mocks the engine, asserts status transitions queued → downloading_data → running → completed and that the result fields populate.
- **Integration smoke**: install the test algorithm (`ElectricJack/quilt-trader-test-algo`), run a backtest against SPY 1day from 2024-01-01 to 2024-12-31, assert it completes with a non-null total_return.

### Implementation order

1. `BacktestRun` model + migration.
2. `BacktestTickContext` + look-ahead enforcement + the 4 dedicated tests.
3. `BacktestEngine` (fill simulator + step loop + position tracker) + unit tests.
4. `backtest_metrics.py` + tests.
5. `BacktestRunner` orchestrator + API endpoints + tests.
6. quantstats tearsheet generation.
7. UI: `RunBacktestModal`, `BacktestRunDetail.tsx`, `Backtests.tsx` tabs split.
8. Integration smoke run.

Each step lands in its own PR. The engine + look-ahead test (steps 2 + 3) are the highest-risk; they should be the most carefully reviewed.
