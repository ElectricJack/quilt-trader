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

## Known follow-ups (deferred)

Listed in priority order. Each is tracked in `docs/superpowers/backlog.md`.

### Tier 3 (each needs its own short spec)

- **Async-job model for `quilt research walk-forward`** — long sweeps still time out the CLI; need 202-Accepted + job-id polling.
- **Strategy-side stop-loss / portfolio circuit breaker** — A/B test needed before adopting as default.
- **Manifest `data:` block for custom data dependencies** — scrapers, CSVs.
- **Replace synthetic backtest clock with union-of-symbol-timelines** — eliminates the per-symbol-lookup patches in `_try_fill`/`_lookup_symbol_close` once the clock IS real.
- **Timezone-aware backtest engine** — manifest `timezone:` field for time-of-day algorithms.

### Tier 4 (needs full roadmap design)

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
