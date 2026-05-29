# Backtest Engine ↔ Strategy Validation Lab Integration

**Date:** 2026-05-28
**Status:** Reflects shipped state as of commits `5ab29d3` through `f8865f7`
**Supersedes (for the integration surface):** [2026-05-27-crypto-tsmom-research-program-design.md](2026-05-27-crypto-tsmom-research-program-design.md)
**Related research:** [2026-05-27-quant-edge-survey.md](../research/2026-05-27-quant-edge-survey.md)
**Related roadmap:** [2026-05-27-backlog-sprint-plan.md](../roadmaps/2026-05-27-backlog-sprint-plan.md)

## Problem

The Strategy Validation Lab and the backtest engine were specced in isolation. Tier 1 + Tier 2 sprint work and a deep debugging session on the crypto-tsmom strategy surfaced ~15 invariants that the two subsystems must share for results to be correct. Several were violated by the original implementation and produced confidently-wrong results: 95% phantom drawdowns from inflated marks; zero-trade backtests despite live signals; date strings rejected by SQLite at insert time; equity columns named differently in tests vs. production.

This spec captures the **integration contract** between the two subsystems — what flows from the lab into the engine, what the engine returns, and the invariants that any change to either side must preserve.

## Goals

1. Document the as-shipped integration surface so a future engineer (or agent) can navigate it without re-discovering the failure modes that took today to find.
2. Make the invariants enforceable: name them explicitly so they can be tested, reviewed for in PRs, and protected by regression tests.
3. Surface the known follow-ups and explicitly defer them so they don't show up unannounced.

## Non-goals

- Reimplementing either subsystem.
- UI / dashboard work (deferred to a separate spec).
- Strategy-side features (stop-loss, cross-asset extensions) — they live with the strategy package, not the lab/engine.

## Architecture overview

```
┌────────────────────────────────────────────────────────────────────────┐
│                      Strategy Validation Lab                            │
│                                                                         │
│  optimization_session.py    sweep.py             walk_forward.py        │
│   create_session()           run_sweep()          run_walk_forward()    │
│   - DB row creation           - search:grid/      - fold computation    │
│   - pre-registration           random/latin/tpe   - sweep-on-train      │
│                              - parameter_space    - single-run-on-test  │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐ │
│  │ _run_one_backtest(db, runner_factory, *, ...) → {run_id, ...}     │ │
│  │   1. INSERT BacktestRun row (config_overrides, dates coerced)     │ │
│  │   2. db.commit()  ← VISIBLE TO RUNNER VIA SEPARATE CONNECTION     │ │
│  │   3. await runner_factory(run_id)  ← engine runs the backtest     │ │
│  └─────────────────────────────┬─────────────────────────────────────┘ │
└─────────────────────────────────┼───────────────────────────────────────┘
                                  │
                                  ▼
┌────────────────────────────────────────────────────────────────────────┐
│                       BacktestRunner.run(run_id)                        │
│  1. SELECT BacktestRun row (async session)                              │
│  2. Load manifest + preload bars from manifest.assets                   │
│  3. Construct BacktestEngine(config=BacktestConfig(cost_profile=...))   │
│  4. engine.run(algorithm, ctx, ...)                                     │
│  5. backtest_finalizer.finalize_run() — populate metrics on row         │
└────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌────────────────────────────────────────────────────────────────────────┐
│                          BacktestEngine                                 │
│  - Per-bar: update ctx.positions, ctx.cash, ctx.account_value           │
│  - Algorithm: on_tick(ctx) → signals                                    │
│  - Engine: fill pending orders, apply slippage + fees from cost_profile │
│  - Observers: stream fills + equity points to parquet (chunked)         │
└────────────────────────────────────────────────────────────────────────┘
```

### Data flow

| Source | Contains | Consumed by |
|---|---|---|
| `OptimizationSession.parameter_space` | JSON dict of {param_name → list[values] or [low, high]} | sweep.py, walk_forward.py |
| `OptimizationSession.pre_registered_criteria` | JSON kill/deploy thresholds | report.py |
| `BacktestRun.config_overrides` | JSON column (dict): base_config + strategy params + `_fold_index`/`_oos` markers | runner, finalizer, report |
| `BacktestRun.cost_profile` | String pointing at `cost_profiles/*.yaml` (defaults to `"default"`) | engine `_try_fill` |
| `BacktestRun.<flat metric columns>` | Populated at finalization from `key_metrics["strategy"]` + trades.parquet | API, dashboard, walk-forward objective |
| `data/backtests/{run_id}/equity_native.parquet` | Per-bar equity: `timestamp`, `portfolio_value`, `cash` | walk_forward.concatenate_oos_curves, report |
| `data/backtests/{run_id}/trades.parquet` | Per-fill: `symbol`, `side`, `quantity`, `fill_price`, `slippage_dollars`, `fees`, `realized_pnl` | finalizer aggregation, report |

## Integration invariants

These are contracts that **must hold** for results to be correct. Each is paired with the bug it was discovered through, so the rationale doesn't fade.

### I1: Algorithm-canonical symbol form is the single source of truth for positions and orders

The algorithm chooses the canonical symbol form (e.g. `"BTC/USD"`) and uses it for:
- `ctx.market_data(symbol, ...)` lookups
- `SignalLeg.symbol` on emitted signals
- `ctx.positions[symbol]` keys
- Engine internal `positions` dict keys
- Equity / position attribution

**Provenance:** debugging the wild-equity-oscillation bug (commit `5ad1049`). The mismatch between algorithm-canonical and provider-specific symbol forms broke MtM, fills, and signal dispatch in three different places.

### I2: Bars cache is keyed by provider-specific symbol

The backtest_runner preloads bars from `manifest.assets` using each entry's `source` (provider) and `symbol`. The resulting cache is keyed by `(source, provider_symbol, timeframe)` where `provider_symbol` is in the provider's native form (e.g. `BTC-USD` for yfinance, `BTC/USD` for Alpaca crypto stream, `X:BTCUSD` for Polygon).

The preload step writes the parquet path verbatim — there is no symbol translation layer between disk and cache.

### I3: All bars-cache lookups route through `AssetService.resolve_symbol`

Whenever code reads from `ctx._bars`, it must accept both the canonical and provider-specific form for the matching symbol. Three call sites enforce this:

- `BacktestTickContext.market_data` — algorithm's read path (commit `9352d6c`)
- `BacktestEngine._try_fill` fill-bar lookup — order fill path (commit `144494e`)
- `BacktestEngine._lookup_symbol_close` — mark-to-market path (commit `5ad1049`)

Plus `CryptoAssetService.get_price` for the engine's price-discovery side.

**The implementation pattern:**

```python
svc = self._asset_registry.get_service(sym)
for (src, cache_sym, tf), df in ctx._bars.items():
    resolved = svc.resolve_symbol(sym, src)
    if cache_sym != sym and cache_sym != resolved:
        continue
    # use df
```

**Provenance:** without this, ETH positions in a yfinance-data backtest were marked at BTC's price (BTC was the clock symbol, so the fallback lookup landed on BTC's bar). Equity inflated 25-50× until the position liquidated.

### I4: pandas 3.0 datetime64 default is microseconds; nanosecond arithmetic requires explicit casting

`pd.Timestamp` and `pd.to_datetime` produce `datetime64[us]` by default in pandas 3.0+. Any code that does `.view("int64")` on a timestamp column or treats `np.datetime64(...).view("int64")` as nanoseconds will silently produce wrong values 1000× off.

**The two required patterns:**

```python
# 1. For a single Timestamp cutoff:
cutoff_ns = pd.Timestamp(...).value   # always ns regardless of resolution

# 2. For an array of timestamps:
ns = ts_col.values.astype("datetime64[ns]").view("int64")
```

Applied at: `backtest_tick_context.market_data` (commit `1885d01`), `backtest_engine_v2._try_fill` and `_lookup_symbol_close` (commits `144494e`, `5ad1049`).

**Provenance:** without this, a sim_time of 2024-06-01 was compared against a "cutoff" that decoded to 2021-09-05 in the wrong unit. `ctx.market_data(symbol)` returned bars from 2.5 years before the sim time, and the strategy produced zero trades for the entire backtest.

### I5: BacktestRun row must be committed before runner_factory is invoked

Sweep + walk-forward orchestrators create the BacktestRun row from a **sync** SQLAlchemy session, then await a runner_factory that opens an **async** SQLAlchemy session in a separate connection. SQLite serializes writes; a `db.flush()` alone is not enough to make the row visible across connections.

**Required pattern:**

```python
db.add(run_row)
db.flush()
db.commit()   # ← critical; without it, runner sees "no such row"
run_id = run_row.id
await runner_factory(run_id)
db.refresh(run_row)   # pull updated metrics back into the sync session
```

Applied at: `sweep._run_one_backtest`, `walk_forward._run_oos_backtest` (commit `acb85f1`).

### I6: `config_overrides` is a JSON column → returns a dict; date fields need string→date coercion

`BacktestRun.config_overrides` is `Mapped[Optional[dict]]` — SQLAlchemy decodes JSON into a Python dict on read.

`BacktestRun.date_range_start` / `date_range_end` are `Date` columns — SQLite refuses string values at insert.

The orchestrators receive `start` / `end` as ISO date strings (JSON-typeable) from the base_config and must coerce:

```python
def _as_date(v):
    if v is None or isinstance(v, (date, datetime)):
        return v
    if isinstance(v, str):
        return date.fromisoformat(v)
    raise TypeError(...)

run_row = BacktestRun(
    date_range_start=_as_date(merged.get("start")),
    date_range_end=_as_date(merged.get("end")),
    config_overrides=merged,
)
```

**Provenance:** the first walk-forward against real coordinator endpoints returned `500 Internal Server Error` because SQLite rejected `date_range_start='2024-01-01'`.

### I7: `_pick_best_train_config` strips base-config keys before merging into OOS run

When walk-forward identifies the best in-sample config and uses it for the OOS run, it must NOT carry the train-window dates into the OOS merge — those would overwrite the OOS test dates.

```python
_BASE_KEYS = {"algorithm_id", "start", "end", "initial_cash", "symbols",
              "data_source", "cost_profile", "_fold_index", "_oos"}
return {k: v for k, v in full.items() if k not in _BASE_KEYS}
```

**Provenance:** first walk-forward ran every OOS fold on its own train window because the merge collision was silent.

### I8: Equity parquet column is `portfolio_value`, not `equity`

The backtest engine writes `portfolio_value` and `cash` to `equity_native.parquet`. Tests and early lab code used `equity`. Both must be accepted at lab read-sites.

Applied at: `walk_forward.concatenate_oos_curves` (commit `43dac12`).

### I9: OOS folds with overlap require deduplication

`compute_folds` produces overlapping test windows when `step_months < test_years × 12`. The naïve `pd.concat([fold1_eq, fold2_eq, ...])` then raises `cannot reindex on an axis with duplicate labels`.

The convention: **later fold wins on overlapping dates** (it had more recent training data). Implementation trims each fold's series to dates strictly after the prior fold's last timestamp (commit `9b155f4`).

### I10: BacktestRun.id is a UUID string, not an int

Pydantic response models, runner_factory signatures, and validation lab result objects all use `str` (not `int`) for `run_id` (commit `5e4b958`).

### I11: BacktestRun.cost_profile defaults to `"default"`, not `None`

If the row's `cost_profile` is NULL, BacktestRunner substitutes `"default"`. The `default` cost profile applies realistic Alpaca crypto fees (0.25% taker) and slippage (10 bps, flat — not `use_bar_range`). A user that explicitly wants zero-fee modeling must ship a custom YAML profile and reference it by name.

Applied at: `BacktestRunner.run` (commit `16f3a1b`). Profile data: `coordinator/services/validation/cost_profiles/default.yaml`.

### I12: Trade-aggregate metrics live in BOTH `key_metrics` and the flat `BacktestRun` columns

The finalizer computes `win_rate`, `profit_factor`, `avg_win`, `avg_loss`, `expectancy`, `longest_winning_streak`, `longest_losing_streak`, `total_fees_paid`, `total_slippage_dollars` and writes them to:
- `BacktestRun.key_metrics["strategy"]` (JSON blob, read by report.py)
- BacktestRun flat columns (read by API list-view + walk-forward objective lookup)

If you only populate the JSON blob, the dashboard shows `--`. If you only populate the flat columns, `report.py` can't see them. **Both, always.**

Applied at: `backtest_finalizer.finalize_run` (commit `18aaaab`).

### I13: SQLite must be in WAL mode for concurrent validation-lab + coordinator workloads

The validation lab's sync DB writes (creating BacktestRun rows in a sweep) plus the coordinator's async DB writes (tradier polling, worker health sweeps) deadlock under vanilla SQLite serialization. `journal_mode=WAL` + `synchronous=NORMAL` + `busy_timeout=30000ms` set at engine construction.

Applied at: `coordinator/database/connection.py:create_engine` (commit `6fd98ef`).

### I14: Per-provider download semaphores so polygon doesn't block yfinance

The validation lab queues yfinance downloads (crypto historical bars); the coordinator simultaneously runs polygon options-chain downloads. With a single shared semaphore, polygon's 13s rate limit blocks the yfinance queue for hours. Per-provider semaphores: polygon=1, yfinance=4, alpaca=4, tradier=4, coinbase=4. Polygon's value is overridable via Settings (`polygon_concurrency`).

Applied at: `DownloadManager` (commit `5ecf9cd`); settings hook (commit `f8865f7`).

### I15: Symbol normalization extends to download paths

When the validation lab requests yfinance data via `quilt data download --symbol BTC-USD ...`, the download lands at `data/market/yfinance/BTC-USD/1day.parquet`. The lab's strategy declares `assets:` in manifest with provider-specific form (`BTC-USD`) and the algorithm reads with canonical form (`BTC/USD`) via the symbol-normalization seam (I3).

**Provenance:** the initial debugging round produced zero ETH trades because the algorithm requested `BTC/USD` data but the cache only had `BTC-USD` — the literal-match lookup failed.

### I16: Benchmark loading reuses the same download-and-wait path as strategy data

`BacktestRunner.run` loads the benchmark via the module-level helper `_load_benchmark_with_download(*, ds, source, symbol, date_range_start, date_range_end, downloader, on_download_start=None)`. Missing benchmark parquet triggers `_download_and_wait` and one retry; if still empty, the run finalizes without a benchmark (`logger.warning`) — strategy metrics still produced.

The `on_download_start` callback fires once after the first empty-load check and before invoking `downloader`, allowing the runner to set the progress message `f"Downloading benchmark {symbol} from {source}"` only on real cache misses (no spurious "Downloading…" flash when the parquet is already cached).

**Provenance:** P1 implementation (commits `00053bc` and earlier). Removes the silent benchmark drop and cost trap described in the spec's P1 motivation. Validates I17 by extension: if the benchmark source is gated on provider availability, the download path uses the same provider's `DownloadManager` semaphore the strategy uses.

### I17: Provider availability is derived at request time from Settings + Accounts; never hardcoded in API or UI

The single helper `coordinator/api/routes/data.py:_provider_availability(db)` is the canonical source. It is consumed by:
- `GET /api/data/providers` — the dashboard's `RunBacktestModal` reads this via `useProviderAvailability()` on mount and renders the dropdown from `available=true` entries (it defaults to the first available provider when none is selected).
- `POST /api/backtest-runs` — the create handler validates `body.benchmark_source` against the matrix and returns `422 {"detail": "benchmark_source 'X' is not available: <reason>"}` when the picked source has `available=false` or is missing entirely.

Availability rules (alphabetical order, fixed):
- `alpaca` available iff at least one Account with `broker_type='alpaca'` exists.
- `coinbase` available iff at least one Account with `broker_type='coinbase'` exists.
- `polygon` available iff the `polygon_api_key` Setting is set.
- `theta` available iff both `theta_data_username` AND `theta_data_password` Settings are set.
- `tradier` available iff at least one Account with `broker_type='tradier'` exists.
- `yfinance` always available.

The pre-existing `GET /api/data/providers/timeframes` (provider→supported-timeframes mapping, formerly at `/providers`) was renamed to free the `/providers` path. Existing `BacktestRun` rows referencing an unavailable source remain viewable on detail pages — only the create form gates.

**Provenance:** P1 implementation (commits `2f1af4d`, `fab516b`, `36057a8`, `6c1cb13`, `d813309`, `3de8c27`).

## Backtest Engine ↔ Validation Lab API contract

### What the lab guarantees the engine

- A valid `BacktestRun.id` in the DB with `config_overrides` populated and dates coerced to `date` objects (I6).
- The row is committed before runner_factory is awaited (I5).
- Manifest path on disk resolves to a valid `quilt.yaml` whose `assets:` entries match the providers wired into `DownloadManager`.

### What the engine guarantees the lab

- `BacktestRun.status` transitions through `pending → downloading_data → running → completed | failed`.
- On completion, `equity_native.parquet` and `trades.parquet` exist with the columns specified in I8 and the trade schema documented above.
- `BacktestRun.<flat metric columns>` are populated per I12.
- `BacktestRun.key_metrics["strategy"]` is populated per I12.
- All position MtM uses correctly-resolved symbols per I3.
- Orphan rows from prior crashes are auto-marked `failed` at coordinator startup (commit `5ab29d3`).

### Failure modes the lab should detect

- `runner_factory` raises → record run as `failed` with `error_message`, continue the sweep.
- Lab dispatched a config that the strategy rejects → finalizer marks the run failed; sweep keeps going.
- Walk-forward fold's train window has no winning config (all NaN sharpe) → `_pick_best_train_config` raises; surface to user.

## Cost profile semantics

The active cost profile applies per-(venue, asset_type, symbol) at fill time. Resolution order:

1. `venue:asset_type:symbol` — most specific
2. `venue:asset_type` — venue + asset type
3. `venue` — venue only
4. `asset_type` — asset type only
5. `fallback` — final fallback bundle

`coordinator/services/validation/cost_profiles/default.yaml` ships these bundles:

| Venue:Type | Fees | Slippage (bps) | use_bar_range |
|---|---|---|---|
| alpaca:crypto | 25 bps taker | 10 | false |
| alpaca:equity | 0 | 2 | false |
| coinbase:crypto | 60 bps taker | 10 | false |
| tradier:options | $0.67/contract flat | 50 | false |
| (fallback) | 0 | 5 | false |

## Search strategies in `run_sweep`

| `search=` | Behavior | Parallelism |
|---|---|---|
| `grid` | Cross-product of `parameter_space[k]` values, capped at `max_trials` | yes (`parallelism` semaphore) |
| `random` | Uniform draws from per-key `(lo, hi)` bounds per `distributions` | yes |
| `latin` | Latin-hypercube samples with same bounds + distributions | yes |
| `tpe` | Optuna's TPE; each trial conditioned on prior results | **no** (sequential) — `parallelism` ignored |

For `tpe`, the orchestrator reads `objective` (default `sharpe_ratio`) off the just-completed BacktestRun row and reports it back to Optuna via `study.tell`. Failed runs are FAIL-stated and skipped.

## Validation-lab metrics surface

| Metric | Module | Inputs | Output |
|---|---|---|---|
| Bootstrap CI (Sharpe, Sortino, CAGR, MaxDD, Calmar) | `bootstrap.py` | equity series | `MetricCI(point, lower, upper, confidence)` |
| Regime-conditional metrics | `regime.py` | equity series + regime tags | per-regime dict of {sharpe, total_return, win_rate, n_days} |
| Multi-test correction (Bonferroni, BH) | `multi_test.py` | list of p-values | per-hypothesis `CorrectedResult` |
| Reality check / SPA significance | `multi_test.py` | N×T returns matrix | `SPAResult(best_idx, best_mean, p_value)` |
| Regime taggers (trailing-return, VIX, custom) | `regime.py` | reference price series | regime-string Series |

## Backlog coverage

This section maps backlog items to their disposition under this spec. Use it as the canonical source of truth — the `backlog.md` entries cross-reference back here.

### Backlog items **shipped and codified as invariants**

Each of these was an open Backtesting-section backlog item that was resolved during the work the spec captures. The invariant column tells you where the contract now lives.

| Backlog item | Invariant | Shipping commit(s) |
|---|---|---|
| Orphan backtest cleanup on coordinator startup | (no invariant; behavior documented under "What the engine guarantees the lab") | `5ab29d3` |
| Realistic Alpaca crypto slippage profile | I11 (default profile content) | `a9089a0` |
| Default cost profile not auto-applied | I11 | `16f3a1b` |
| Trade-aggregate metrics not persisted on BacktestRun | I12 | `18aaaab` |
| Sweep/walk-forward DI smell — magic config keys | (not an invariant; addressed by `RunnerFactory` callable parameter) | (validation lab phase work) |
| Sync vs async DB session split | I5 (separate sync orchestrator session + async runner session, commit boundary) | (validation lab phase work) |
| BacktestScheduler not wired to cost_profile | I11 (scheduler also auto-applies default) | (validation lab phase work) |
| `quilt research *` should be a thin client | (not an invariant; HTTP endpoint pattern) | (validation lab phase work) |
| SPA / White's Reality Check significance test | (documented under "Validation-lab metrics surface") | `bb616dd` |
| Pluggable regime taggers | (documented under "Validation-lab metrics surface") | `cbce415` |
| Bayesian / TPE search in sweep | (documented under "Search strategies in `run_sweep`") | `385a446` |
| Per-provider download semaphores | I14 | `5ecf9cd` |
| Multi-consumer `on_download_complete` listener registry | (not an invariant; download manager API) | `450efcb` |
| Paid-tier polygon concurrency setting | I14 (extended to allow override) | `f8865f7` |
| Validate `Algorithm.assets` shape at install time | (install-time validator) | `2556fd1` |
| Algorithm install: handle existing package dir | (install-time recovery) | `2422a54` |
| `on_disconnect` callback on broker stream handles | (broker-side, separate from lab/engine) | `7ed6268` |
| `add_symbols` / `remove_symbols` on stream handles | (broker-side, separate from lab/engine) | `dbe7917` |

### Backlog items **in scope for this spec — planned implementation**

These items have been promoted from the deferred list into the spec's active scope. When the spec's implementation plan is written, it covers all three. Each adds or modifies invariants as noted; the changes are documented in the "Planned additions" section below.

| Planned item | What it does | Invariants affected |
|---|---|---|
| **P1 — Benchmark source expansion** | Expose all 5 wired providers in the benchmark dropdown; validate against availability matrix at request time; reuse runner's download-and-wait for missing benchmark data | Adds I16, I17 |
| **P2 — Async-job model for `quilt research sweep` / `walk-forward`** | Endpoints return `202 + job_id`; new polling endpoint; CLI shows progress bar; job state machine | Adds I18 |
| **P3 — Union-of-symbol-timelines backtest clock** | Two-pass execution: discover symbols on first pass, replay on real union clock on second pass | Simplifies I3 (resolution layer becomes unnecessary) |

### Backlog items **deferred from this spec** — kept as Tier 3 follow-ups

These were considered in scope to address but explicitly deferred. Each gets its own dated spec when picked up. Backlog entries should cross-reference this spec as the deferral source.

| Tier 3 item | Why deferred | Relationship to this spec |
|---|---|---|
| **Timezone-aware backtest engine** | Manifest schema change; affects `BacktestTickContext.timestamp` semantics. Independent of lab/engine integration but affects any algorithm with time-of-day logic. | Not currently an invariant. When shipped, this spec should add `I19: ctx.timestamp respects manifest timezone declaration`. |
| **Strategy-side stop-loss / portfolio circuit breaker** | Strategy-side feature, not lab/engine contract. The lab provides the A/B test infrastructure (sweep with/without stop). | Not part of this spec — the lab's sweep + walk-forward IS the methodology for evaluating it. Belongs in a strategy-specific spec. |
| **Manifest `data:` block for custom data dependencies** | Scrapers, CSVs. Affects the contract in I2 (bars cache key shape). | If shipped, add an `I20: ctx.data(custom_source)` invariant alongside the existing I2/I3 bars-cache contracts. |

### Backlog items **out of scope for this spec**

These belong to other subsystems and aren't bridged by the lab/engine contract:

- Bulk "close all" positions action (Positions domain)
- Daily/weekly option expiration data (Backtesting data layer, but specifically about Polygon data tier and contract discovery — orthogonal to the lab)
- Per-attempt scraper run history (Scrapers domain)
- Push test-algo `quilt.yaml` upstream (one-shot housekeeping)

### Backlog domain re-categorizations recommended

Reading the backlog through the lens of this spec surfaced two miscategorized items:

1. **"`quilt research walk-forward` CLI needs async-job model"** is currently under the Backtesting section but is purely a validation-lab orchestration concern. Move it to the Validation Lab section.
2. **"Replace synthetic backtest clock with union-of-symbol-timelines"** is currently under the Live data feeds section but is squarely a Backtesting engine concern (it surfaced through backtest engine edge cases and would be implemented in `backtest_engine_v2.py`). Move it to the Backtesting section.

After those moves, the open Backtesting section reads as a focused list of engine-correctness work (timezone, options data, synthetic clock) and the Validation Lab section captures all lab-orchestration debt.

## Planned additions to this spec

Three pieces of in-scope work to ship under this spec's implementation plan. Each is fully designed below; collectively they extend the existing as-shipped contract with three new invariants (I16, I17, I18) and simplify one (I3).

### P1 — Benchmark source expansion

#### Motivation

The dashboard's `RunBacktestModal` hardcodes `polygon` and `theta` as the only two benchmark sources, but the coordinator wires five providers into `DownloadManager` (polygon, theta, yfinance, alpaca, tradier — coinbase is stream-only). The backend's benchmark loader is provider-agnostic: it just calls `data_service.load_market_data(source, symbol, "1day")`, which reads `data/market/<source>/<symbol>/1day.parquet`. The dropdown is the lie. Three failure modes today:

1. **Cost trap.** A crypto strategy benchmarked against SPY can't be benchmarked using free yfinance — user is forced into a paid polygon plan.
2. **Silent benchmark drop.** If the user picks a (symbol, source) pair where no parquet exists on disk, `bdf` is empty and the run silently produces no benchmark line in the report. No download attempted, no warning.
3. **Confusing failure.** A user with no theta credentials picks "theta" in the dropdown and gets a confusing mid-run error.

#### Design

Three touch points:

```
Dashboard (RunBacktestModal)
  • On mount: GET /api/data/providers → list of {name, available, reason}
  • Filter to available=true; render dropdown
  • Default to first available

Coordinator API (coordinator/api/routes/data.py)
  • NEW: GET /api/data/providers
    Returns availability matrix derived from Settings + Accounts
  • MODIFIED: POST /api/backtest-runs validates benchmark_source
    against the same availability matrix

Backtest runner (backtest_runner.py)
  • When benchmark_symbol+source set and data missing on disk:
    reuse existing _download_and_wait(source, symbol, "1day")
  • Run flips through downloading_data → running just like
    strategy data downloads already do
```

#### API surface

**New endpoint** `GET /api/data/providers`:

```json
[
  {"name": "alpaca",   "available": true,  "reason": null},
  {"name": "polygon",  "available": true,  "reason": null},
  {"name": "theta",    "available": false, "reason": "theta credentials not configured"},
  {"name": "tradier",  "available": false, "reason": "no tradier account configured"},
  {"name": "yfinance", "available": true,  "reason": null}
]
```

Order: alphabetical, stable. `reason` is `null` when `available` is `true`; explanatory string otherwise.

**Modified endpoint** `POST /api/backtest-runs`:
- If `benchmark_source` is set, validate it appears with `available: true` in the availability matrix.
- On failure: `422 {"detail": "benchmark_source 'theta' is not available: theta credentials not configured"}`.
- Existing rows with unavailable sources stay viewable on detail pages — only the **create** form gates.

#### Provider availability rules

| Provider | Available when |
|---|---|
| `yfinance` | always (no creds required) |
| `polygon` | `polygon_api_key` Setting is set |
| `theta` | both `theta_data_username` and `theta_data_password` Settings are set |
| `alpaca` | at least one `Account` row with `broker_type='alpaca'` exists |
| `tradier` | at least one `Account` row with `broker_type='tradier'` exists |

Logic lives in `coordinator/api/routes/data.py:_provider_availability(db)` — a single async helper that the new GET endpoint and the modified POST validator both consume.

#### Runner behavior on missing benchmark data

In `BacktestRunner.run()`, after reading `r.benchmark_source` + `r.benchmark_symbol`:

```python
if bench_symbol and bench_source:
    bdf = self._ds.load_market_data(bench_source, bench_symbol, "1day")
    if bdf is None or bdf.empty:
        await self._download_and_wait(
            symbols=[bench_symbol],
            date_start=date_range_start,
            date_end=date_range_end,
            provider=bench_source,
            timeframe="1day",
            data_type="bars",
            phase_label=f"benchmark {bench_symbol}",
        )
        bdf = self._ds.load_market_data(bench_source, bench_symbol, "1day")
    if bdf is not None and not bdf.empty:
        benchmark_bar_df = bdf
```

Failure path: log warning, finalize without a benchmark — strategy metrics still produced. Progress message during the wait: `"Downloading benchmark SPY from yfinance"`.

#### New invariants

- **I16: Benchmark loading reuses the same download-and-wait path as strategy data; no separate sync-only loader.**
- **I17: Provider availability is derived at request time from Settings + Accounts; never hardcoded in API or UI.**

#### Out of scope

- Smart-defaults / asset-class-aware source picker (deferred — can be added later as a UI-only refinement).
- Coverage badges on dropdown options (deferred — adds UI complexity without changing the contract).

### P2 — Async-job model for `quilt research sweep` / `walk-forward`

#### Motivation

`POST /api/research/sessions/{id}/sweep` and `.../walk-forward` are synchronous: the request handler awaits the full sweep / walk-forward before responding. Default CLI HTTP timeout is 600s (bumped from 30s as a band-aid; commit `8fccfcc`). Larger sweeps still time out the request even though server-side work continues. The CLI returns an obscure `ReadTimeout` and no run_ids.

The download manager already solved this pattern: `create_download` returns `{"id": ..., "status": "queued"}` immediately and the work runs as an `asyncio.create_task`. The validation lab should mirror.

#### Design

```
POST /api/research/sessions/{id}/sweep
  → returns 202 Accepted with {"job_id": "...", "session_id": N, "status": "queued"}
  → spawns asyncio.create_task that runs run_sweep
  → task registered in a new ResearchJobManager (similar to download_manager
    _active_tasks dict + DB-backed row for persistence across restarts)

POST /api/research/sessions/{id}/walk-forward
  → same pattern

GET /api/research/sessions/{id}/jobs/{job_id}
  → returns {"job_id", "session_id", "kind": "sweep|walk-forward",
             "status": "queued|running|completed|failed",
             "progress_pct": 0.0..1.0,
             "progress_message": "Trial 12 of 24",
             "run_ids": [...],     # populated as runs complete
             "error_message": null,
             "started_at", "completed_at"}

GET /api/research/sessions/{id}/jobs
  → list all jobs for a session

DELETE /api/research/sessions/{id}/jobs/{job_id}
  → cancel a running job (sets stop flag; task observes between trials)
```

#### CLI surface

`quilt research sweep ...` and `quilt research walk-forward ...`:
1. POST to the endpoint, receive `job_id`
2. Print "queued: <job_id>"
3. Poll `GET /api/research/sessions/{id}/jobs/{job_id}` every 2 seconds
4. Render progress bar (using existing `click` progress patterns; if installed, `rich` is preferred but not required)
5. On terminal state, print final summary (n_configs, run_ids)

Optional `--no-wait` flag to fire and exit immediately, returning the job_id for later polling.

#### DB schema

New table `research_jobs`:

| Column | Type | Notes |
|---|---|---|
| `id` | String (uuid) | PK |
| `session_id` | int FK | → optimization_sessions.id |
| `kind` | String | "sweep" or "walk-forward" |
| `status` | String | queued / running / completed / failed / cancelled |
| `progress_pct` | Float | 0.0–1.0 |
| `progress_message` | Text | "Trial N of M" or "Fold N of M" |
| `request_payload` | JSON | Original request body |
| `run_ids` | JSON | List of completed BacktestRun ids |
| `error_message` | Text | null unless status=failed |
| `started_at`, `completed_at`, `created_at` | DateTime | |

Alembic migration adds the table.

#### Orphan recovery

At coordinator startup, `ResearchJobManager.recover_orphaned_jobs()` marks any `queued` / `running` row as `failed` with `error_message="Orphaned by coordinator restart"` — mirrors the pattern already in `DownloadManager` and `BacktestRunner.recover_orphaned_runs`.

#### New invariant

- **I18: Research orchestration endpoints (`sweep`, `walk-forward`) are fire-and-poll. The request thread never holds a backtest sweep open; large sweeps live in a job tracked by `ResearchJobManager` with its own DB row.**

#### Out of scope

- Parallel jobs per session (one at a time per session is fine for v1; the existing `OptimizationSession` model isn't designed for concurrent overlapping sweeps yet).
- Job priority / queue depth limits (a deployed sweep is rate-limited by the underlying backtest runner already).

### P3 — Union-of-symbol-timelines backtest clock

#### Motivation

`BacktestEngine.run` is constructed with a `clock_series` — a single DataFrame of timestamps that the engine ticks through. For multi-asset algorithms (e.g. crypto-tsmom on BTC/ETH), the clock is whichever single symbol the runner chose first. For scraper-only algorithms (no `assets:`), the clock is `_build_synthetic_clock` — a business-day-business-time series with all-zero OHLCV.

Three workarounds emerged from this:

1. **`BacktestTickContext.market_data`** — symbol-normalization fallback when bars cache misses (the lookup loop in I3).
2. **`BacktestEngine._try_fill`** — fill-bar resolution loop that falls back to the clock bar when the symbol's own bar isn't found (also I3).
3. **`BacktestEngine._lookup_symbol_close`** — MtM lookup that falls back to clock bar's close (the I3 path that caused the wild-equity bug on 2026-05-27).

Each was patched individually. The structural fix replaces the synthetic / single-symbol clock with a real union clock.

#### Design

Two-pass execution:

**Pass 1 (discovery):**
- Run `algorithm.on_start(config, restored_state)` to initialize.
- Run `algorithm.on_tick(ctx)` ONCE with a synthetic warmup bar.
- Inspect `ctx._bars` after the tick — every symbol the algo loaded via `market_data()` appears there.
- Discard pass-1 fills, equity snapshots, observer calls (it was a warmup, no real engine state).

**Pass 2 (replay):**
- Build the real clock: `union(timestamps from each (src, symbol, tf) the algo touched)`, deduped + sorted.
- Re-create a fresh `BacktestTickContext` with the same bars cache (carried over from pass 1 — the data is already loaded).
- Re-run from bar 0 with the real clock. Every bar in the clock has a real timestamp; every symbol the algo cares about has a real bar at most of those timestamps.

#### Implications

1. **`_lookup_symbol_close` simplifies dramatically.** When the clock bar is the symbol bar (or a sibling with the same timestamp), no fallback resolution is needed. The function becomes a direct dict lookup.

2. **`_try_fill` fill-bar resolution simplifies.** Same reason — the symbol's bar is found by direct lookup at the current timestamp.

3. **I3 simplifies.** Currently: "Every cache read must accept canonical OR provider-specific form via `resolve_symbol`." After P3: "Cache key matches lookup key directly; the resolution layer is only needed at the boundary (manifest preload → cache key)."

4. **Pass-1 cost.** One extra `on_tick` call per backtest. For a 6-year crypto-tsmom backtest with daily bars (~2200 ticks), this is 1/2200 = 0.05% overhead — negligible.

5. **Pass-1 observability.** Observers are NOT called during pass 1. Avoids double-counting fills and equity points.

#### Edge cases

- **Algorithm uses no `market_data()` calls** (pure scraper-driven algo): pass-1 leaves `ctx._bars` unchanged from preload. Fall back to the synthetic clock as today.
- **Algorithm requests a NEW symbol on a later tick** that wasn't requested on pass 1: that symbol's data is loaded into the bars cache lazily (existing behavior); its timestamps merge into the clock at next tick boundary — slightly subtle. Document this as a known limitation: discovery-pass symbols are the canonical clock contributors; later symbols don't extend the clock.
- **Algorithm has different lookback windows per pass.** Pass 1 should pass a "warmup" bar (e.g. one at `date_range_start`) so the algo's `if len(md) < min_history: return` short-circuit fires without producing trade signals.

#### Modified invariant

- **I3 (simplified): Bars cache is preloaded under provider-specific symbols (I2). At lookup time inside the tick loop, the clock bar's symbol matches the cache key — no `resolve_symbol` indirection needed. Resolution layer survives only at the manifest-preload boundary and in `ctx.market_data` for algorithm convenience.**

#### Out of scope

- Lazy-discovery: extending the clock when an algorithm requests a new symbol on a later tick. Documented as a known limitation; if it becomes painful, ship a third-pass invalidation in a follow-up.
- Multi-frequency clocks (an algo using 1day + 1hour data simultaneously). Engine still ticks at a single frequency.

## Known follow-ups (Tier 4 features that warrant full roadmap-level design)

Each is a multi-week feature that ships under its own brainstorm → spec → plan cycle.

- **Validation Lab dashboard UI** — sessions browser, sweep heatmaps, walk-forward fold viewer, report renderer. **This is the highest-leverage Tier 4 — without it, the lab is invisible to humans and future agents.**
- **Live deployment automation when a session passes kill criteria** — paper-trade cutover, deploy button.
- **Crypto perpetual futures venue integration** — Hyperliquid DEX or CME micros; required if funding-carry strategies are pursued.
- **Equity VRP defined-risk strategy** (Phase 2 of research roadmap).
- **MTUM cross-sectional momentum strategy** (Phase 3 of research roadmap).

## Test coverage summary

The integration contract is protected by these test suites:

| Suite | Verifies |
|---|---|
| `tests/coordinator/services/validation/test_*.py` | Lab orchestration: sweep, walk-forward, bootstrap, regime, multi-test, report, e2e smoke |
| `tests/coordinator/services/test_symbol_normalization.py` | I3 — `ctx.market_data` resolves canonical → provider symbol |
| `tests/coordinator/services/test_backtest_engine.py` + `tests/coordinator/test_backtest_engine.py` | Engine fill model, options handling |
| `tests/coordinator/services/validation/test_walk_forward.py` | I7, I8, I9 (concatenation, fold dedup), `_pick_best_train_config` schema |
| `tests/coordinator/test_download_manager.py` | I14 — per-provider semaphores |
| `tests/coordinator/test_polygon_tier_settings.py` | Polygon rate-limit overrides |
| `tests/coordinator/services/validation/test_cost_model.py` | Cost profile loading, default profile content |

Total: 80+ tests across the integration surface.

## Cross-references

- Original spec: [2026-05-27-crypto-tsmom-research-program-design.md](2026-05-27-crypto-tsmom-research-program-design.md)
- Literature: [2026-05-27-quant-edge-survey.md](../research/2026-05-27-quant-edge-survey.md)
- Sprint plan: [2026-05-27-backlog-sprint-plan.md](../roadmaps/2026-05-27-backlog-sprint-plan.md)
- Backlog: [backlog.md](../backlog.md)
- Strategy package: https://github.com/ElectricJack/quilt-crypto-tsmom
