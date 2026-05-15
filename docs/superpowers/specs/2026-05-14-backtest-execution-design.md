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

## Non-Goals (v1)

- **Replacing** the existing `BacktestComparison` table or its REST surface. That feature stays. **But the *engine* must be shared** — see §11. The `BacktestComparator` ingests two decision streams; until now nothing produced the "backtest" stream. After this spec, the same `BacktestEngine` that runs long one-shot backtests also feeds the parallel short-window backtests that `BacktestComparator` compares against live.
- Tick-level backtesting. The smallest unit is the smallest bar timeframe declared by the algorithm.
- Options-strategy fill simulation in v1. See §12: the engine must not foreclose this — abstractions must accommodate options later (multi-leg legs, `asset_type` field, expiration, contract multiplier). For v1, a signal containing an `asset_type == "options"` leg is rejected with a clear error.
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

### Design goal: persistence-free, observable, reusable

`BacktestEngine` is a pure simulation service. It accepts inputs (algorithm class, configured `BacktestTickContext`, slippage/fee config) and emits structured events through an `EngineObserver` interface. It **does not** know about the `BacktestRun` table, `DecisionLog`, the dashboard, or any other persistence destination. Two thin wrappers consume the events:

- **`BacktestRunner`** (for Spec D's one-shot runs) — accumulates events into an in-memory `BacktestResult`, writes everything to the `BacktestRun` row when the run finishes.
- **`ParallelBacktestFeeder`** (for `BacktestComparison`) — writes each `signals_emitted` event as a `DecisionLog` row with `mode="backtest"` so `BacktestComparator` can compare it against the live decision stream. No equity-curve persistence; comparison-only consumer.

```python
class EngineObserver(Protocol):
    def on_tick(self, sim_time: datetime, ctx_snapshot: dict) -> None: ...
    def on_signals_emitted(self, sim_time: datetime, signals: list[Signal]) -> None: ...
    def on_fill(self, fill: FillRecord) -> None: ...
    def on_signal_rejected(self, sim_time: datetime, signal: Signal, reason: str) -> None: ...
    def on_equity_point(self, sim_time: datetime, portfolio_value: float, cash: float, positions: list[dict]) -> None: ...
    def on_complete(self, summary: EngineSummary) -> None: ...
    def on_error(self, exc: Exception) -> None: ...


class BacktestEngine:
    def run(
        self,
        *,
        algorithm: QuiltAlgorithm,           # already instantiated by the wrapper
        ctx: BacktestTickContext,
        clock_series: pd.DataFrame,          # the smallest-timeframe bars
        clock_timeframe: str,
        slippage: SlippageModel,
        buy_fees: list[TradingFee],
        sell_fees: list[TradingFee],
        initial_cash: float,
        observer: EngineObserver,
        cancel_token: CancelToken,           # checked each iteration; allows clean stop
    ) -> None:
        ...
```

Same engine, two callers. `BacktestRunner` is from Spec D; `ParallelBacktestFeeder` is wired into `BacktestSchedulerJob` (§11).

### Tick-as-bar: forward-compatible event model

The engine treats every event as a **bar** with `{timestamp, open, high, low, close, volume}`. A tick is the degenerate case: `open == high == low == close == last_price`, `volume == trade_size`. So the same iteration loop, the same `market_data()` filter, and the same fill simulation work identically whether the algorithm is clocked on 1day bars or 1tick events. v1 ships with bar-frequency strategies; ticks are forward-compatible without a rewrite.

Concretely:

- **Storage schema is uniform.** Both bar parquets and tick parquets carry the columns `timestamp, open, high, low, close, volume`. For ticks: `open == high == low == close == last_price`, `volume == trade_size`. No conditional logic in the engine.
- **Timeframe vocabulary.** v1 supports `1min, 5min, 15min, 1hour, 1day` (matches the existing `polygon.py:TIMEFRAME_MAP`). The `1tick` timeframe is a planned addition — the type-string is reserved; `_timeframe_to_seconds("1tick") = 0` so the look-ahead filter `df["timestamp"] + 0 <= sim_time_now` reduces to `df["timestamp"] <= sim_time_now` (tick available the instant its timestamp ≤ now). No special-casing.
- **Smallest-timeframe clocking generalizes.** If a future algorithm declares `data_dependencies` of `1tick + 1min`, the simulation clock is the tick stream; `on_tick` fires per tick; `market_data("SPY", "1min", ...)` still returns only fully-closed minute bars at the current sim-time. Same rule, no changes to the engine.
- **Fill model at tick frequency.** "Next bar's open + slippage" remains meaningful: at tick frequency, the next tick's `open` (= the next tick's print price) is what you fill against, biased by `market_bps` of slippage. Conservative-by-default rules still apply: no same-tick fill, strict-cross for limits.
- **Storage and downloads.** Spec B's live subscription aggregator already produces tick parquets (`data/market/{broker}_live/{symbol}/ticks/trades-{date}.parquet`). The download manager will need a `data_type=ticks` flow when polygon-tick history is wanted; that's the scope of the future tick-history spec, not v1.

The key promise: the v1 engine is correctness-tested at bar frequency, and the same engine handles tick events when we extend timeframes downward. No fork in the simulation code.

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

#### Conservative-by-default principle

**The biggest source of backtest-to-live divergence is optimistic fill assumptions.** This engine is conservative by default and never produces a fill at the bar where the signal was emitted. Specifically:

1. **No same-bar fills.** A signal emitted on bar `T` (where `on_tick` was called at the close time of `T`) can fill at the **earliest at bar `T+1`'s open**. There is no path through the engine that allows a signal-bar fill — the `pending_orders` queue is processed AFTER `on_tick` returns, against the NEXT iteration's bar. Tested explicitly in §9.3.
2. **Market orders eat slippage.** Defaults to 5 bps adverse to fill direction (buys pay more, sells receive less). Never zero by default. The user can dial it down for idealized backtests but the system biases toward realism.
3. **Limit orders require strict touch.** A buy limit only fills if the next bar's `low < limit_price` (price had to *strictly cross* below the limit). Equality is not enough — a bar that exactly touches the limit price doesn't fill, because in reality the order would queue behind any orders already at that price. Same conservative rule for sell limits (`next_bar.high > limit_price`).
4. **Stop orders trigger then market-fill.** Stop only triggers if the next bar's range crosses the stop price, then becomes a market order filled at the next-next bar's open + slippage (TWO bars after signal). This deliberately doubles the latency for stops because real stops route to the broker, await trigger, then submit a market order — a process that's almost never single-bar-fast.
5. **Bracket / OCO / multi-leg complexity:** v1 treats every leg in a `Signal` independently for fill purposes. If you submit a 2-leg spread, each leg fills (or doesn't) on its own next-bar timeline. **Real broker multi-leg tickets** (Spec A's `submit_multileg_order`) fill atomically, which is *more favorable* than the v1 backtest model. v1 over-penalizes spread fills, which is intentional — better to be pessimistic in backtest. A future spec can add atomic multi-leg fill simulation for spreads.

These five rules collectively bias the backtest toward **understating performance** when live trading is even marginally better than expected. The aim is "no nasty surprises in live", not "pretty backtest numbers".

#### Per-order-type fill rules

For each `PendingOrder` resolving in the current bar:

**Market order:**
1. Fill price = `bar.open` (next bar after signal was emitted — never the signal-bar itself).
2. Apply slippage:
   - `slip = bar.open * (slippage_model.market_bps / 10000) * (+1 if buy else -1)`
   - `fill_price = bar.open + slip`
   - If `use_bar_range`: instead, `fill_price = uniform(bar.low, bar.high)` (uses `random.Random(seed=run_id)` so backtests are reproducible).
   - If `volume_impact_bps_per_pct > 0` AND `bar.volume > 0`: `extra_bps = (qty / bar.volume * 100) * volume_impact_bps_per_pct`; apply on top of `market_bps`.
3. Compute fees: sum applicable `buy_trading_fees` (taker rules apply) on buys, `sell_trading_fees` on sells.
   - `fee = sum(tf.flat_fee + fill_price * qty * tf.percent_fee for tf in applicable_fees)`
   - Special case: `tradier-options` preset's per-contract flat fee → `flat_fee × qty` when `asset_type == "options"` (post-v1).
4. Cash impact: `cash -= side_sign(side) * (fill_price * qty) + fee`.
5. Record `requested_price = bar.open` (the un-slipped value), `slippage_dollars = abs(fill_price - requested_price) * qty`, `slippage_bps_applied`.

**Limit order:**
1. Filled if **strictly crossed**:
   - **Buy limit** (signal_type `buy`): fills only if `next_bar.low < limit_price`. The limit must be *strictly* in the bar's interior or below — a bar that merely touches the limit at its low doesn't fill.
   - **Sell limit** (signal_type `sell`): fills only if `next_bar.high > limit_price`.
2. Fill price = `limit_price`. By definition, limit orders can't fill worse than their limit, but they can fail to fill entirely.
3. Apply `limit_bps` slippage if non-zero (usually 0 — limit fills at limit).
4. Fees use maker rules.
5. Record `requested_price = limit_price`, `slippage_dollars = 0` (limit, no adverse slippage).

**Stop order (market on trigger):**
1. Trigger condition: stop_price falls within `[next_bar.low, next_bar.high]`. (Stop-loss: BUY stop triggered when price rises through; SELL stop triggered when price falls through.)
2. On trigger: converts to a market order scheduled for `next_bar + 1` (one MORE bar after trigger, two bars total after signal). Apply market slippage at that next-next bar's open.
3. If the bar where the stop would have triggered closes inside the range, but the stop wasn't crossed at any point... wait — we only have OHLC, not tick. Conservative assumption: trigger when `next_bar.low <= stop_price <= next_bar.high` (the bar's range encompasses the stop). This may over-trigger relative to live, but stops over-triggering in backtest is the safer direction.

**Stop-limit order:**
1. Trigger same as stop. On trigger, converts to a LIMIT order at `limit_price`, scheduled for `next_bar + 1`.
2. Apply limit-order fill rules from there (strict cross required).

**Order not filled in current bar:**
- Default: remains pending up to a configurable timeout (default: **1 bar** for v1 — fills next bar or expires). GTC / DAY semantics are a follow-up.
- An unfilled order produces a `signal_rejected` event (with `reason="no_fill_within_timeout"`) so the algorithm's `on_signal_rejected` callback fires — matches live broker behavior.

#### Tracked follow-ups (intentionally NOT in v1)

- **Partial fills.** Modeled as a fraction of order quantity executed at fill price, with remainder expiring or remaining pending depending on `time_in_force`. Requires per-bar volume modeling for the partial split (e.g., max(qty, alpha × bar.volume) filled, remainder queued).
- **Market impact modeling.** A live market order moves the price; our `volume_impact_bps_per_pct` is a crude first approximation. A proper model would use Almgren-Chriss or a square-root-of-volume impact function.
- **Queue-position modeling for limits.** A real limit at the bid joins a queue and may take time to fill even when the price hovers there. v1's "must strictly cross" rule approximates this conservatively.
- **Bid-ask spread modeling on every fill.** v1 uses bar OHLC; doesn't simulate bid-ask explicitly. Crypto and thin-volume equities behave worse than our model suggests.
- **Stop slippage past the stop price** (gap-down scenarios where the live fill is far worse than the stop). v1 fills stops at next-next-bar's open + slippage, which approximates this but not perfectly.

These are all tracked as future spec work. The v1 engine is intentionally simple-but-conservative; making it more realistic only ever makes backtest results *worse*, never better.

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

`BacktestRunner` is the Spec-D consumer of `BacktestEngine`. It owns persistence to the `BacktestRun` row. The engine itself remains persistence-free per §3.

```python
class BacktestRunner:
    async def run(self, run_id: str) -> None:
        # 1. Load BacktestRun row + Algorithm row
        # 2. Load manifest from data/packages/{algo.name}/quilt.yaml
        # 3. Resolve data_dependencies + benchmark to (source, symbol, timeframe) tuples
        # 4. For each, check DataService whether the parquet exists with coverage >= [date_range_start, date_range_end]
        # 5. For missing/short ones, call container.download_manager.create_download(...) and track the resulting download_id
        # 6. status="downloading_data", progress_message="Downloading SPY 1day from polygon (1/3)..."
        # 7. Poll the downloads' status; advance progress_message per completion
        # 8. When all downloads are 'completed', load the parquets, build BacktestTickContext
        # 9. Load the algorithm class (via PackageManager's installed venv path), instantiate it
        # 10. status="running"
        # 11. Construct a `RunObserver` (implements EngineObserver) that accumulates events into in-memory
        #     lists: equity_curve, trades, signals_log; tracks progress_pct as fraction of clock_series consumed.
        # 12. Invoke BacktestEngine.run(algorithm, ctx, ..., observer=run_observer, cancel_token=...)
        # 13. On engine completion (observer.on_complete): compute aggregate metrics from accumulated data,
        #     persist everything to the BacktestRun row, optionally generate quantstats tearsheet, status="completed".
        # 14. On engine error: status="failed", error_message=str(e) + traceback (via observer.on_error).
        # 15. On cancel_token tripped: engine exits cleanly, runner sets status="cancelled".
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

## 9. Correctness — required tests

This area is correctness-critical. The plan must include the following dedicated tests; if any regress, the feature is broken at its core.

### Look-ahead prevention

1. **`test_market_data_filters_future_bars`**: build a `BacktestTickContext` with a 1day bar series spanning Jan 1-30; set `sim_time_now = 2026-01-15 14:30 UTC`; call `ctx.market_data("SPY", "1day", 100)`; assert the returned df's max timestamp is `2026-01-14` (the most recent fully-closed daily bar).
2. **`test_multi_timeframe_no_lookahead`**: algorithm has SPY 1day + SPY 1min deps. At sim-time mid-day, asserting `ctx.market_data("SPY", "1day")` does NOT include today's bar even though today's 1min bars are accessible.

### Conservative fill model

3. **`test_market_order_fills_at_next_bar_open_with_slippage_never_signal_bar`**: an algorithm emits a market BUY signal on `on_tick` at sim-time = close of bar T. The fill timestamp MUST equal bar T+1's timestamp, NEVER bar T's. The fill_price MUST equal `bar(T+1).open * (1 + market_bps/10000)`.
4. **`test_no_path_to_same_bar_fill`**: regression guard — even if an algorithm tries to short-circuit (e.g., a hypothetical algorithm that returns a signal AND mutates engine state), the engine's pending_orders queue MUST process AFTER `on_tick` returns and AGAINST the next iteration. Build a malicious mock algorithm and verify it can't force a same-bar fill.
5. **`test_limit_order_requires_strict_cross`**: a buy limit at $100. Test cases:
   - `next_bar.low = 100.0` (exact touch) → NO FILL.
   - `next_bar.low = 99.99` (strict cross) → FILLS at $100.00.
   - `next_bar.low = 100.01` (no cross) → NO FILL.
   Mirror tests for sell limits.
6. **`test_stop_market_two_bar_delay`**: BUY stop at $100. At bar T+1 the range crosses $100 (low=99, high=101). The fill MUST happen at bar T+2's open, not bar T+1's open. (Two bars between signal and fill for stops; one for plain market.)
7. **`test_stop_limit_strict_cross_after_trigger`**: BUY stop-limit triggered at $100 with limit $99.50. After trigger at T+1, the limit waits one more bar; at T+2, fill only if `next_bar.low < 99.50`. Otherwise expires.

### Fee + slippage accounting

8. **`test_fee_breakdown_per_trade`**: a single fill incurs the sum of all matching `TradingFee` rows (maker/taker rules respected). `trade.fee_breakdown` records per-fee contribution.
9. **`test_slippage_recorded_in_dollars_and_bps`**: a market fill with 5 bps slippage records `slippage_dollars = abs(slipped - requested) * qty` and `slippage_bps_applied = 5.0`.
10. **`test_total_fees_and_slippage_aggregates`**: sum of all per-trade fees and slippages equals the `total_fees_paid` and `total_slippage_dollars` metrics on the BacktestRun row.

### Options forward-compat

11. **`test_options_leg_in_signal_fails_run_cleanly`**: an algorithm emits a signal with an `asset_type="options"` leg. The engine must halt with `status="failed"` and a clear error message containing "options backtest not yet supported". No partial trade record, no silent equity-style fill.

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

---

## 11. Shared engine with `BacktestComparison`

The existing `BacktestComparator` (`coordinator/services/backtest_engine.py`) takes two streams of `DecisionLog` rows and produces a `ComparisonResult`. Today, the periodic `BacktestSchedulerJob` reads live decisions (mode=`"live"`) and backtest decisions (mode=`"backtest"`) from `DecisionLog` and compares. **Problem:** nothing in the system has ever written `mode="backtest"` decision rows. The comparison feature has been dormant because there's no parallel-backtest stream.

This spec fixes that by reusing the new `BacktestEngine` as the source of those rows.

### `ParallelBacktestFeeder`

New service `coordinator/services/parallel_backtest_feeder.py`. Implements `EngineObserver`:

```python
class ParallelBacktestFeeder:
    def __init__(self, instance_id: str, session_factory):
        self._instance_id = instance_id
        self._sf = session_factory

    def on_signals_emitted(self, sim_time, signals):
        # Write a DecisionLog row with mode="backtest", matching the live-side write pattern.
        async with self._sf() as session:
            session.add(DecisionLog(
                instance_id=self._instance_id,
                timestamp=sim_time,
                mode="backtest",
                signals_produced=[s.to_dict() for s in signals],
                # ... tick_data / reasoning / data_sources_used populated similarly
            ))
            await session.commit()

    def on_tick(self, ...): pass        # no-op for comparison purposes
    def on_fill(self, ...): pass         # comparison is at signal-emission granularity, not fill
    def on_signal_rejected(self, ...): pass
    def on_equity_point(self, ...): pass
    def on_complete(self, ...): pass
    def on_error(self, ...): pass
```

The comparison cares about whether the live and backtest streams produce the same signals at the same timestamps. Fills, equity, and metrics are irrelevant in this mode — the observer ignores them.

### Updated `BacktestSchedulerJob`

The existing job (`coordinator/services/backtest_scheduler.py`) is extended:

1. For each running `AlgorithmInstance`, look up its `Algorithm` + manifest.
2. Build a `BacktestTickContext` over the last `lookback_hours` of historical data (sourced from the same `DataService` parquets the engine uses for one-shot runs).
3. Construct a `ParallelBacktestFeeder` observer for the instance.
4. Invoke `BacktestEngine.run(...)` with that observer. Engine writes one `DecisionLog(mode="backtest")` per emitted signal as it walks the historical window.
5. After the engine finishes, the existing `_compare_instance` flow runs: load live + backtest `DecisionLog` rows for the same window, pass them to `BacktestComparator.compare`, persist the `BacktestComparison` row.

So `BacktestComparison` keeps its existing shape and its existing endpoints — but the parallel-backtest decision stream it depends on is now actually produced, by the same engine that powers Spec D's one-shot runs.

### Configuration for periodic backtests

The `BacktestSchedulerJob` runs each instance's backtest with **defaults**:
- `slippage`: `SlippageModel()` (5 bps market default)
- `fees`: empty lists (zero fees) — comparing pure decision streams, fees don't affect signal generation
- `initial_cash`: 100_000 (irrelevant for signal-only comparison)
- `clock_timeframe`: smallest in the manifest

These aren't user-configurable yet. If we later want per-instance comparison configs, that's a future spec.

### Why this matters

This is the user's most important integration point: the engine that runs your hour-long historical backtests is **the same code** that runs every 24h alongside each live algorithm to detect divergence. If a bug in the engine causes look-ahead leakage, both surfaces are wrong in the same way — and they're discoverable in the same place. We get bug-fix amplification.

---

## 12. Forward-compatibility with options

Options-strategy backtesting is **out of scope for v1 implementation** but **must not be foreclosed by v1 architecture**. Specific guarantees:

### 1. `BacktestTickContext` implements the full `TickContext` interface

The abstract base (`sdk/context.py`) already declares:
- `market_data(symbol, timeframe, bars, source=None)` — equities/crypto, used in v1.
- `option_chain(symbol, expiration=None)` — declared but not implemented by `BacktestTickContext` in v1. Raises `NotImplementedError` with the message: `"option_chain not yet available in backtest contexts; tracked as a follow-up."` Future implementation will need: historical chain snapshots (would require new `DataService.load_option_chain_history(...)` and a corresponding data download flow).

When option support lands, the `BacktestTickContext` changes are additive — no algorithm currently using v1 needs modification.

### 2. Engine fills handle multi-leg signals already

`Signal.legs: list[SignalLeg]` exists. Each `SignalLeg` has `asset_type`. The fill loop already iterates over legs. The v1 implementation rejects any leg with `asset_type == "options"` at fill time:

```python
def _validate_leg_for_fill(self, leg, sim_time):
    if leg.asset_type == "options":
        raise UnsupportedAssetTypeError(
            f"Options backtest not yet supported (leg: {leg.symbol}). "
            f"This will be supported in a future spec. Track: options-backtest follow-up."
        )
```

The error halts the run with `status="failed"` and a clear, search-friendly error message in `BacktestRun.error_message`. No silent equity-style fill of an options leg.

### 3. Position storage handles options legs structurally

`Position.legs` is JSON with per-leg `asset_type`, `expiry`, `strike`, `right`. The engine's internal `positions` dict is keyed by `(symbol, expiry, strike, right)` for options and `(symbol,)` for equities/crypto — code already structured to support both. v1 only populates the equities/crypto branch; options branch is unreachable until §12.2's guard is lifted.

### 4. Fill simulation primitives are asset-type-aware

`fill_price` calculation, slippage, and fee application all branch on `leg.asset_type`. v1's branches:

```python
if leg.asset_type in ("equities", "crypto"):
    # current implementation
elif leg.asset_type == "options":
    raise UnsupportedAssetTypeError(...)  # v1 guard
```

When options support lands, the `options` branch implements: contract multiplier (×100 for US options), per-contract flat fees from `TradingFee` (already handled by the `tradier-options` preset), assignment at expiry (engine checks expired options at each step and processes exercises against the underlying's price), bid/ask spread modeling.

### 5. Fee presets already include options

`tradier-options` preset is already in the fee preset dropdown. Selecting it on an equities-only algorithm has no effect; selecting it on an options algorithm (post-v1) automatically applies the per-contract fee.

### 6. UI

The `RunBacktestModal`'s fee preset dropdown already lists `tradier-options`. The benchmark selector defaults to the underlying when an options strategy is being backtested (post-v1). For v1, no special UI changes — options paths are dead code, but visible in the data model.

### Summary

The v1 engine treats options as a **fast-fail** asset class with clear errors. The data model, fee model, and context interface are **already shaped** to support options. The future options spec adds: historical option chain data flow + a `BacktestTickContext.option_chain()` implementation + the options-side branches of fill simulation. None of that breaks the v1 contract.
