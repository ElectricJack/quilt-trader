# Deferred-Work Backlog

Items intentionally cut from a shipped spec. Consult this file before starting any new spec — if a deferred item is now in scope, lift the entry here rather than re-deferring it. When a new spec defers something, add it here with a link back.

---

## Positions

### Multi-leg / spread-aware position close
- **Deferred from:** [2026-05-18-close-positions-design.md](specs/2026-05-18-close-positions-design.md)
- **Why deferred:** v1 closes single-leg market orders only. Multi-leg positions (options spreads) need coordinated closing across legs and use a different broker call (`submit_multileg_order` with inverted sides).
- **What's needed:** a position model that knows when broker rows belong to a single user-intent (e.g. an iron condor) and closes them atomically; UI that shows the *strategy*, not just the legs.
- **RESOLVED** (2026-05-22): `close_position_by_id` endpoint supports atomic multileg close via `submit_multileg_order` with inverted sides, with sequential single-leg fallback. Position model tracks `strategy_type` and multi-leg `legs` array.

### Partial position close
- **Deferred from:** [2026-05-18-close-positions-design.md](specs/2026-05-18-close-positions-design.md)
- **Why deferred:** v1 closes the full quantity shown on the row. Partial close needs a quantity input + validation against current broker quantity.
- **RESOLVED** (2026-05-22): `close_position_by_id` accepts optional `quantity` parameter for partial close. Validates against `remaining_quantity`, decrements on fill, keeps position `open` until fully closed. Cost basis lots track each partial close independently.

### Limit / stop close orders
- **Deferred from:** [2026-05-18-close-positions-design.md](specs/2026-05-18-close-positions-design.md)
- **Why deferred:** v1 submits market orders only. Limit needs price input, unfilled-state handling, and an order-management view to cancel/replace.
- **RESOLVED** (2026-05-22): Both `close_position_by_id` and the legacy `close_position` endpoints accept `order_type` (market/limit/stop) with `limit_price` and `stop_price` parameters. Validation enforces required price fields per order type. Position enters `closing` status when fill price is null (pending limit/stop).

### Bulk "close all" action
- **Deferred from:** [2026-05-18-close-positions-design.md](specs/2026-05-18-close-positions-design.md)
- **Why deferred:** confirmation UX and error aggregation (one leg fails out of N) need design.

### Coordinate manual close with running algorithm
- **Deferred from:** [2026-05-18-close-positions-design.md](specs/2026-05-18-close-positions-design.md)
- **Why deferred:** v1 close endpoint doesn't notify the algo. The algo sees the position disappear on its next broker sync but may attempt to re-open it.
- **What's needed:** a coord→worker signal "this position was force-closed by user, treat as final"; algo SDK API to receive it.
- **RESOLVED** (2026-05-22): `_notify_owner_of_position_close` sets `state_stale=True` on the owning instance and sends a `position_closed` WebSocket message (with `reason: manual_close`) to the worker. Positions track `owner_instance_id` for attribution.

### `open_position` doesn't forward `asset_type` to the broker adapter
- **Surfaced by:** crypto-close fix on 2026-05-18 (commits `784ca9c` / `416252c` / `1a52a9b`).
- **Why deferred:** the close-position fix threaded `asset_type` through `submit_order` so AlpacaAdapter picks `TimeInForce.GTC` for crypto. The `open_position` route's sequential-fallback path at `coordinator/api/routes/accounts.py` still calls `adapter.submit_order(...)` without `asset_type`, so opening a crypto position via the dashboard will hit the same Alpaca `invalid crypto time_in_force` error.
- **What's needed:** in the open-position handler's sequential fallback, pass `asset_type=leg.asset_type` (or the appropriate leg field) to each `submit_order` call. Add a regression test mirroring `test_close_passes_asset_type_to_adapter`.
- **RESOLVED** (2026-05-22): The `open_position` sequential fallback now passes `asset_type=leg.asset_type` to each `submit_order` call (visible in the current `accounts.py` at the fallback path, line ~916).

### Holistic position-tracking model
- **Deferred from:** [2026-05-18-close-positions-design.md](specs/2026-05-18-close-positions-design.md) (implicit — surfaces as common dependency above)
- **Why deferred:** today positions live in two places — broker's `get_positions` and Quilt's internal `Position` table — with no canonical join. Each new feature (multi-leg, partial, limit, manual-vs-algo attribution, lot tracking) hits this seam.
- **What's needed:** a roadmap spec covering: position identity across legs, ownership (manual vs which algo run), lot-level cost basis, reconciliation against broker truth, and what the data model should look like. **Promote this to a `docs/superpowers/roadmaps/position-tracking.md` once 2-3 more position features have accumulated deferred work here** — the shape will be clearer then than it is today.
- **PARTIALLY RESOLVED** (2026-05-22): Position model now has `owner_instance_id` for ownership tracking, `cost_basis_lots` for lot-level cost basis, `remaining_quantity` for partial close tracking, and a reconciliation endpoint (`/positions/reconcile`) using `PositionReconciler` to compare broker vs internal state. Full roadmap spec still needed for the remaining seam between broker and internal position identity.

---

---

## Backtesting

> **Integration contract** for the engine ↔ validation lab boundary is documented in [specs/2026-05-28-backtest-and-validation-lab-integration.md](specs/2026-05-28-backtest-and-validation-lab-integration.md). Open Backtesting items that affect that contract are marked **[covered by spec]** below, with a pointer to which invariant or deferred section captures them.

### Timezone-aware backtest engine
- **Surfaced by:** options-spreads-martingale backtests producing 0 trades (2026-05-25). The algo checks `now.time() < time(15, 45)` meaning 3:45 PM ET, but sim_time is UTC. The 5-minute entry window (15:45–15:50 config) silently misaligns.
- **Why deferred:** workaround exists (adjust config times to UTC), but every new algo with time-of-day logic will hit the same trap.
- **What's needed:** add `timezone` field to manifest (e.g., `timezone: America/New_York`). Engine converts sim_time to algo's declared timezone before calling on_tick. `ctx.timestamp` returns tz-aware datetime. Default to UTC if not specified. Validate at install: reject manifests with time-based configs that don't declare a timezone.
- **[covered by spec]:** Listed as Tier 3 deferred work in `2026-05-28-backtest-and-validation-lab-integration.md` § Backlog coverage. Spec notes that when shipped this becomes a new invariant `I16: ctx.timestamp respects manifest timezone declaration`.

### Replace synthetic backtest clock with union-of-symbol-timelines clock
> **Recategorized 2026-05-28** from Live data feeds → Backtesting. Surfaced as a backtest engine concern (synthetic clock in `backtest_engine_v2`), only filed under Live feeds because of where the original discovery happened. Implementation lives in the engine.

- **Surfaced by:** backtest engine edge cases on alpha-picks-rebalancer (2026-05-19). The synthetic clock (all-zeros business-day series) broke fills ($0 prices), position valuation ($0 market value), and position snapshots. Each was patched individually with per-symbol lookups, but the root cause remains.
- **Why deferred:** the per-symbol lookup patches work for v1. The structural fix requires rethinking the engine's clock construction.
- **RESOLVED** (2026-05-28, commits `cea6951` through `b25bfc6`): Shipped under P3 of `2026-05-28-backtest-and-validation-lab-integration.md`. Two-pass execution in `BacktestEngine.run`: pass 1 (`on_start` + one warmup `on_tick`) populates `ctx._bars`; pass 2 builds a real union clock from the cache via `_build_union_clock`, calls `ctx.reset_for_replay()`, then `_run_internal` with the canonical clock. Observers fire only in pass 2.  Deviation from the original design: pass 2 preserves the caller's `clock_source`/`clock_symbol` (using `"_union"` markers broke single-symbol fill-bar resolution). The per-tick lookup loop survives but the no-match fallback is hardened — `_lookup_symbol_close` returns `0.0` instead of falling back to the clock bar's close, eliminating the cross-symbol bug class. I3 updated to its simplified form in the integration spec.

### Expand benchmark source list beyond polygon + theta
- **Surfaced by:** crypto-tsmom backtests on 2026-05-28. The dashboard's `RunBacktestModal` hardcodes `polygon` and `theta` as the only two benchmark sources, but 5 providers are wired into `DownloadManager` (polygon, theta, yfinance, alpaca, tradier). The backend's benchmark loader is provider-agnostic — it just calls `data_service.load_market_data(source, symbol, "1day")`. Three problems: (a) the dropdown lies (3 providers missing); (b) silent benchmark drop when (symbol, source) has no data on disk; (c) user can pick a provider they have no credentials for and get a confusing mid-run failure.
- **Why deferred:** UI dropdown fix is small, but doing it right (provider availability matrix + reuse runner's download-and-wait for missing benchmark data) is moderate scope.
- **[IN SCOPE of integration spec — planned implementation]** (2026-05-28): Promoted to in-scope as P1 in `2026-05-28-backtest-and-validation-lab-integration.md` § Planned additions. Design: new `GET /api/data/providers` returns availability matrix derived from Settings + Accounts; `POST /api/backtest-runs` validates `benchmark_source` against the matrix; runner reuses `_download_and_wait` for missing benchmark data. Adds new invariants I16, I17 to the spec.

### Daily/weekly option expiration data
- **Surfaced by:** options-spreads-martingale and options-condor-martingale backtests (2026-05-25). These are 0DTE/1DTE strategies that need contracts expiring every trading day. Current contract discovery only finds monthly expirations (3rd Friday), so the algo's `_next_expiration()` returns tomorrow's date but no chain exists for it → 0 trades.
- **Why deferred:** downloading daily expirations increases data volume ~20x. Polygon free tier rate limits make this impractical without a paid plan.
- **What's needed:** extend contract discovery to find daily/weekly expirations. Consider making expiration frequency configurable in the manifest (`expiration_frequency: daily | weekly | monthly`). May require Polygon paid tier for reasonable download times.
- **[out of scope of integration spec]:** Data-layer / Polygon-tier concern; doesn't affect the engine ↔ lab contract. Independent feature.

### Orphan backtest cleanup on startup
- **Surfaced by:** coordinator restarts leaving backtests stuck in "running" status (2026-05-25).
- **Why deferred:** manual SQL cleanup works. Similar to download manager's existing `recover_orphaned_downloads()`.
- **RESOLVED** (2026-05-27, commit `5ab29d3`): `BacktestRunner.recover_orphaned_runs()` added; called at coordinator startup from `main.py`. Marks any 'queued'/'downloading_data'/'running' rows as 'failed' with `error_message="Orphaned by coordinator restart"`.

### Realistic Alpaca crypto slippage profile
- **Surfaced by:** crypto-tsmom backtest analysis on 2026-05-27 (run `f56339ca`). Observed slippage median 70 bps, max 400 bps over 78 trades — for retail-size BTC/ETH on Alpaca, realistic is 5–30 bps. The strategy paid ~16% of starting capital in slippage over one year.
- **Why deferred:** default backtest slippage uses `use_bar_range=True` which samples uniformly between bar's low/high — fine for thinly-traded equities, much too pessimistic for liquid crypto. The fix is data + config: ship a more realistic per-symbol slippage profile.
- **RESOLVED** (2026-05-27, commit `a9089a0`): `cost_profiles/default.yaml` `alpaca:crypto` bundle switched from `use_bar_range=True`+`market_bps=15` to `use_bar_range=False`+`market_bps=10`. Combined with the existing 25 bps taker fee, round-trip cost is now ~70 bps total — matches real Alpaca crypto trading. Added `coinbase:crypto` bundle too. Per-symbol overrides for long-tail alts still pending (low priority).

### Default cost profile not auto-applied to ad-hoc backtests
- **Surfaced by:** crypto-tsmom backtests via dashboard / API show `total_fees_paid: 0` and `cost_profile: null` (2026-05-27). The `default` cost profile (alpaca crypto 25 bps taker, etc.) only applies if the run's `cost_profile` field is explicitly set.
- **Why deferred:** existing backtests still work; cost modeling is purely additive.
- **RESOLVED** (2026-05-27, commit `16f3a1b`): `BacktestRunner.run()` now defaults `cost_profile` to `"default"` when the column is `NULL`, rather than falling through to legacy empty fee lists. Users who want zero-fee modeling can ship a custom YAML profile and reference it explicitly.

### Trade-aggregate metrics not persisted to BacktestRun
- **Surfaced by:** crypto-tsmom backtest API responses on 2026-05-27. `win_rate`, `profit_factor`, `avg_win`, `avg_loss`, `expectancy`, `longest_winning_streak`, `longest_losing_streak`, `total_fees_paid`, `total_slippage_dollars` all return `None` even though they're trivially computable from `trades.parquet` (verified: win rate 55.7%, profit factor 1.50, total slippage $324.79 for run `5c249922`).
- **Why deferred:** the UI works around it by computing from the trades endpoint on demand; the data isn't lost, just not persisted.
- **RESOLVED** (2026-05-27, commit `18aaaab`): `backtest_finalizer.finalize_run` now mirrors `win_rate`, `profit_factor`, `avg_win`, `avg_loss`, `expectancy`, `longest_winning_streak`, `longest_losing_streak` from key_metrics to the flat BacktestRun columns. Also sums `total_fees_paid` and `total_slippage_dollars` from `trades.parquet`.

### Strategy-side stop-loss / portfolio circuit breaker
- **Surfaced by:** user question on 2026-05-27 ("what mechanism is preventing a complete stop loss?"). The crypto-tsmom strategy has no explicit stop-loss — positions exit only when the signal flips negative or realized vol explodes. In a fast crash where the signal hasn't yet rolled, drawdowns are unbounded (capped only at -100%).
- **Why deferred:** debatable whether a stop-loss helps or hurts a momentum strategy. Adding one whipsaws out of legitimate drawdowns; not adding one accepts tail risk. Worth A/B testing before shipping.
- **What's needed:** EITHER (a) add a `max_drawdown_stop` config to the crypto-tsmom algorithm (and any future strategy) that closes all positions if running drawdown exceeds a threshold (e.g., 20%), keeps the account in cash until signal turns positive AND drawdown recovers. OR (b) add a portfolio-level circuit breaker as a framework feature consumed by all algorithms. Either way, A/B test the resulting Sharpe/CAGR vs no-stop baseline before adopting as the default.
- **[covered by spec]:** Listed as Tier 3 deferred in `2026-05-28-backtest-and-validation-lab-integration.md` § Backlog coverage. Spec notes this is strategy-side, not engine/lab contract — the validation lab's sweep + walk-forward IS the methodology for evaluating it (run with/without stop, compare bootstrap Sharpe CIs). Belongs in a strategy-specific spec when picked up.

---

## Live data feeds

### Per-stream `on_disconnect` callback wired into broker handles
- **Surfaced by:** [2026-05-18-unified-live-subscriptions-design.md](specs/2026-05-18-unified-live-subscriptions-design.md)
- **Why deferred:** `_stale_stream_sweep` detects disconnects via a heuristic (no tick for N seconds). A first-class `on_disconnect` callback wired directly into `_AlpacaStreamHandle` and `_TradierStreamHandle` would detect drops instantly and with less false-positive risk.
- **RESOLVED** (2026-05-27, commit `7ed6268`): `MarketDataStreamHandle` now exposes `set_on_disconnect(callback)` and `_fire_on_disconnect()`. `_AlpacaStreamHandle` fires it on thread exit (if not intentionally closed); `_TradierStreamHandle` fires it on server-clean end-of-stream AND on exception before backoff reconnect. `LiveFeedAggregator` wiring to consume the callback (replacing the no-tick heuristic) is the follow-up — the heuristic still catches drops within N seconds, so this is a refinement, not a blocker.

### `add_symbols` / `remove_symbols` on stream handles
- **Surfaced by:** [2026-05-18-unified-live-subscriptions-design.md](specs/2026-05-18-unified-live-subscriptions-design.md)
- **Why deferred:** today, adding or removing a symbol from a running subscription tears down and restarts the whole stream. Both `_AlpacaStreamHandle` and `_TradierStreamHandle` need `add_symbols` / `remove_symbols` methods so multi-symbol updates are surgical rather than restart-from-scratch.
- **RESOLVED** (2026-05-27, commit `dbe7917`): `MarketDataStreamHandle.add_symbols/remove_symbols` declared on base class (raise NotImplementedError by default). `_AlpacaStreamHandle` overrides with native `subscribe_trades/quotes` + `unsubscribe_*`. `_TradierStreamHandle` overrides with a force-reconnect flow (closes current chunked-HTTP response so `_run` re-opens with new symbol list). LiveFeedAggregator wiring is the follow-up consumer — `start_subscription`/`stop_subscription` can now call `add_symbols`/`remove_symbols` on existing handles instead of tear-down + recreate.

### Validate `Algorithm.assets` shape at install time
- **Surfaced by:** unified-live-subscriptions feature (2026-05-18).
- **Why deferred:** the `assets` field on `Algorithm` is freeform JSON. An algorithm installed with a malformed assets list silently skips subscription wiring.
- **RESOLVED** (2026-05-27, commit `2556fd1`): `_validate_assets` in `coordinator/api/routes/algorithms.py` now requires `broker`, `symbol`, and `asset_class`; rejects unknown `broker` values (only `alpaca`/`tradier`/`coinbase`/`polygon` accepted) and unknown `asset_class` values. yfinance is intentionally rejected (data-source-only). 4 new tests cover the rejection cases.

### Algorithm install fails opaquely when package dir is orphaned
- **Surfaced by:** post-migration install attempt on 2026-05-18.
- **Why deferred:** `install_from_url` calls `pm.clone_repo` which fails with `fatal: destination path '...' already exists and is not an empty directory` whenever a previous install's on-disk package wasn't cleaned up. Common after DB migrations that drop algorithm rows without touching `data/packages/`, or after a partially-failed prior install.
- **RESOLVED** (2026-05-27, commit `2422a54`): `PackageManager.clone_repo` now detects existing dirs and either (a) `git fetch origin && git reset --hard origin/<default-branch>` if it's a clone of the same repo, or (b) raises `PackageError` with a clear "remove it manually" message if it's not a clone or is a clone of a different repo. 3 new tests cover the rejection / recovery paths.

### Manifest `data:` block for custom data dependencies (scrapers, CSVs)
- **Surfaced by:** backtest failure on alpha-picks-rebalancer (2026-05-19). The algo called `ctx.data("alpha-picks-scraper")` but the backtest runner couldn't find the file.
- **Why deferred:** the immediate fix (smarter path resolution in `StandaloneDataProvider`) unblocks the user. The structural fix — declaring custom data deps in the manifest — requires design work on how scrapers + custom CSVs fit into the manifest schema alongside `assets:`.
- **What's needed:** add a `data:` block to the manifest: `[{source: "alpha-picks-scraper", type: "scraper"}]`. The backtest runner pre-checks that all declared data sources exist before starting. The deploy flow ensures the scraper is registered and has run at least once. The system surfaces "missing data dependency" errors clearly instead of failing mid-backtest.

### Push updated `quilt.yaml` for `simple-ma-crossover` to upstream GitHub repo
- **Surfaced by:** unified-live-subscriptions feature (2026-05-18).
- **Why deferred:** `data/packages/quilt-trader-test-algo/quilt.yaml` was updated locally to the new `assets:` format, but `data/packages/` is gitignored. A re-install from the upstream GitHub repo will revert to the old format.
- **What's needed:** open a PR on the upstream `quilt-trader-test-algo` repo updating `quilt.yaml` to include the `assets:` block in the new schema.

---

## Validation Lab

> **Status:** lab module shipped 2026-05-27 (commits `c349ec8` through `0b66649`). Deferred items below remain open. The three implementation-time concerns below were discovered during execution and are also open.

### Sweep / walk-forward need proper dependency injection for BacktestRunner
- **Surfaced during:** Task C3 (`run_sweep`) and D2 (`run_walk_forward`) implementation on 2026-05-27.
- **Why deferred:** magic config keys worked under heavy mocking; fixing it required introducing a new public-API parameter on the orchestrators.
- **RESOLVED** (2026-05-27): Sweep and walk-forward orchestrators now accept an explicit `runner_factory: Callable[[int], Awaitable[None]]` parameter. CLI constructs a real factory via `coordinator/services/runner_bootstrap.py:bootstrap_runner_services` which mirrors the part of `coordinator/main.py` startup needed to run backtests. `_run_one_backtest` and `_run_oos_backtest` now `db.commit()` before invoking the factory so the async runner sees the BacktestRun row.

### Sync vs async DB session split in validation lab
- **Surfaced during:** Task G1 CLI implementation on 2026-05-27.
- **Why deferred:** the validation lab is sync (consumes `sqlalchemy.orm.Session`); the coordinator HTTP API is async; the CLI started by inlining a sync engine, which worked but duplicated config logic.
- **RESOLVED** (2026-05-27): Added `coordinator/database/session.py:get_session_factory()` — a proper cached sync sessionmaker reading `QUILT_DB_URL` (stripping the `aiosqlite` driver prefix when present). CLI now imports this instead of inlining the engine. The lab stays sync; the coordinator HTTP API stays async; both share the same `QUILT_DB_URL` config.

### `BacktestScheduler` not wired to `cost_profile`
- **Surfaced during:** Task A4b on 2026-05-27.
- **Why deferred:** A4b focused on wiring `BacktestRunner`; the scheduler comparison path was orthogonal.
- **RESOLVED** (2026-05-27): `backtest_scheduler.py` now constructs `BacktestEngine(config=BacktestConfig(cost_profile=...))` instead of `BacktestEngine()`. Reads `cost_profile` from `instance.cost_profile` or `algorithm.cost_profile` if those columns exist (neither does today; the hook is ready for when they do). Default behavior — legacy flat fee/slippage — unchanged.

### `quilt research *` should be a thin HTTP client like `quilt data *`
- **Surfaced during:** crypto-tsmom strategy work on 2026-05-27.
- **Why deferred:** initial validation lab shipped with the CLI bootstrapping services locally via `runner_bootstrap.py`. Functional but architecturally inconsistent with the rest of the CLI, which goes through coordinator HTTP endpoints (`/api/data/...`, `/api/backtest-runs/...`, etc.). The local-bootstrap path also doesn't get the coordinator's wired providers (yfinance, polygon, tradier, alpaca), forcing users into pre-download workflows.
- **RESOLVED** (2026-05-27, commits `c6eea90` + `28cf283`, merged in `d3bfa70`): Added `coordinator/api/routes/research.py` with 6 endpoints (POST/GET sessions, POST sweep, POST walk-forward, POST report). Rewrote `sdk/cli/commands/research.py` as a thin client mirroring `sdk/cli/commands/data.py`. Coordinator restart required to load the new routes. `runner_bootstrap.py` stays as a library for programmatic users — docstring now says "LIBRARY USE ONLY".
- **Design principle (preserved going forward):** Any framework capability an agent (or human) needs should be exposed through the CLI as a thin client to the coordinator HTTP API. Bespoke scripts and local bootstrapping should be the exception, not the rule.

### `quilt research walk-forward` CLI needs async-job model, not synchronous HTTP
> **Recategorized 2026-05-28** from Backtesting → Validation Lab. This is purely a lab-orchestration concern: long sweeps inside `POST /api/research/sessions/{id}/sweep` block the request thread. Doesn't affect the engine.

- **Surfaced by:** validation-lab walk-forward runs on 2026-05-27 timing out the CLI's HTTP client even though server-side work continued (bumped CLI default timeout to 600s as band-aid; commit `8fccfcc`).
- **Why deferred:** band-aid works for sessions up to ~30 backtests; larger sweeps still time out.
- **[IN SCOPE of integration spec — planned implementation]** (2026-05-28): Promoted from Tier 3 deferred → in-scope as P2 in `2026-05-28-backtest-and-validation-lab-integration.md` § Planned additions. Design: `POST /api/research/sessions/{id}/sweep` and `.../walk-forward` return `202 + job_id`; new `GET /api/research/sessions/{id}/jobs/{job_id}` for polling; CLI shows progress bar. New `research_jobs` table + alembic migration. Orphan recovery at coordinator startup. Adds new invariant I18 to the spec.

### SPA / White's Reality Check significance test
- **Deferred from:** [2026-05-27-crypto-tsmom-research-program-design.md](specs/2026-05-27-crypto-tsmom-research-program-design.md)
- **Why deferred:** v1 ships Bonferroni and Benjamini-Hochberg corrections. SPA is more powerful for dependent hypothesis sets (which is what parameter sweeps produce) but the implementation requires bootstrap-of-bootstraps and is meaningfully more involved.
- **RESOLVED** (2026-05-27, commit `bb616dd`): `spa_test(returns_matrix, n_resamples, block_size, seed)` ships in `multi_test.py`. Uses stationary block-bootstrap of centered returns; returns the best strategy's index, sample mean, and data-mining-corrected p-value. White (2000) version — conservative compared to Hansen but easier to reason about. Hansen's studentized refinement deferred.

### Pluggable regime taggers
- **Deferred from:** [2026-05-27-crypto-tsmom-research-program-design.md](specs/2026-05-27-crypto-tsmom-research-program-design.md)
- **Why deferred:** v1 ships only a BTC-trailing-return tagger, which is circular for crypto strategies (the regime measures what the strategy trades). Acknowledged limitation noted in spec.
- **RESOLVED** (2026-05-27, commit `cbce415`): `regime.py` refactored to expose a `RegimeTagger` Protocol. Three taggers ship: `tag_regimes_by_trailing_return` (existing BTC-style), `tag_regimes_by_vix` (low/mid/high vol from VIX percentiles for equity strategies), `FunctionTagger` (wraps arbitrary callables for research-notebook one-offs). `tag_regimes()` preserved as backward-compat alias.

### Bayesian / TPE search in sweep
- **Deferred from:** [2026-05-27-crypto-tsmom-research-program-design.md](specs/2026-05-27-crypto-tsmom-research-program-design.md)
- **Why deferred:** grid + random + latin-hypercube cover the v1 needs. TPE / Bayesian search would be more sample-efficient for large parameter spaces.
- **RESOLVED** (2026-05-27, commit `385a446`): `run_sweep(search="tpe", ...)` added via Optuna. Sequential (parallelism ignored — each trial conditioned on prior results). Reads `objective` column from each BacktestRun after dispatch, reports back to Optuna via `study.tell`. Failed-objective trials are FAIL-stated to optuna and skipped. Mixes discrete-grid + continuous-bounds parameters via per-key `distributions` dict.

### Dashboard UI for OptimizationSession browsing
- **Deferred from:** [2026-05-27-crypto-tsmom-research-program-design.md](specs/2026-05-27-crypto-tsmom-research-program-design.md)
- **Why deferred:** v1 reports are static markdown + HTML files. A dashboard page would let users browse sessions, drill into folds, compare parameter sets visually.
- **What's needed:** new dashboard route `/research/sessions/:id`, fetched via a `GET /api/optimization-sessions/:id` endpoint. Render walk-forward equity curves, fold-level fills, parameter heatmaps.

### Live deployment automation hook after a session passes
- **Deferred from:** [2026-05-27-crypto-tsmom-research-program-design.md](specs/2026-05-27-crypto-tsmom-research-program-design.md)
- **Why deferred:** v1 keeps the deploy step manual after the kill criteria pass; safer while methodology is being shaken out.
- **What's needed:** a "promote to live" action on a passing OptimizationSession that creates a deployment with the winning parameter set, paper-traded first, then a CLI-confirmed live cutover.

### Crypto perpetual futures venue integration
- **Deferred from:** [2026-05-27-crypto-tsmom-research-program-design.md](specs/2026-05-27-crypto-tsmom-research-program-design.md)
- **Why deferred:** the funding-carry edge has decayed since 2024 per BIS WP 1087; US retail access to Binance/Bybit/OKX is blocked; Hyperliquid is DEX-only.
- **What's needed:** if Phase 2+ wants funding carry or true L/S crypto, evaluate Hyperliquid DEX adapter (no KYC, perps available) vs. CME micro BTC futures. Both have nontrivial integration cost.

### Equity VRP defined-risk strategy (Phase 2 of research roadmap)
- **Deferred from:** [2026-05-27-crypto-tsmom-research-program-design.md](specs/2026-05-27-crypto-tsmom-research-program-design.md)
- **Why deferred:** Phase 2 of the broader edge-discovery roadmap. Consumes the same validation lab.
- **What's needed:** strategy package at `data/packages/equity-vrp/` implementing put credit spreads / iron condors on SPY with defined risk. Cost profile for Tradier options. Pre-registered hypothesis around CBOE PUT-equivalent Sharpe.

### Cross-sectional momentum via MTUM (Phase 3 of research roadmap)
- **Deferred from:** [2026-05-27-crypto-tsmom-research-program-design.md](specs/2026-05-27-crypto-tsmom-research-program-design.md)
- **Why deferred:** Phase 3 of the broader edge-discovery roadmap.
- **What's needed:** strategy package implementing monthly-rebalanced MTUM-anchored momentum with TSMOM overlay. Same lab.

---

## Scrapers

### Per-attempt run history
- **Deferred from:** [2026-05-27-scraper-catchup-design.md](specs/2026-05-27-scraper-catchup-design.md)
- **Why deferred:** v1 stores only `last_success`, `last_attempt_at`, and a daily attempts counter on the existing `scrapers` row — enough to answer "did we run today, should we retry." A full per-attempt history (timestamp, status, error, duration) isn't needed until something actually consumes it (e.g. a scraper-health dashboard or alerting on N consecutive failures).
- **What's needed:** a `scraper_runs` history table with one row per attempt and a small read API. Probably a UI surface to make it worth the schema.

---

## Data acquisition

### Multi-consumer `on_download_complete` listener registry
- **Deferred from:** [2026-05-27-options-goal-incremental-download-design.md](specs/2026-05-27-options-goal-incremental-download-design.md)
- **Why deferred:** `DownloadManager.on_download_complete` is currently a single `Callable`. The new options-goal design adds a second consumer (the goal processor) alongside the existing portfolio tracker. v1 wraps both in a fan-out function in `coordinator/main.py`; converting the slot to a proper `list[Callable]` with `add_listener` / `remove_listener` methods is a small structural cleanup that's only worth doing if a third consumer appears.
- **RESOLVED** (2026-05-27, commit `450efcb`): `DownloadManager` now exposes `add_completion_listener(cb)` / `remove_completion_listener(cb)`. Legacy `_on_download_complete` attribute kept as a property (for backward compat with main.py's direct assignment). Exceptions in one listener are caught and logged, never block others.

### Paid-tier polygon concurrency setting
- **Surfaced by:** [2026-05-27-options-goal-incremental-download-design.md](specs/2026-05-27-options-goal-incremental-download-design.md)
- **Why deferred:** the user is on polygon's free tier (1 concurrent / ~13s latency). A paid-tier upgrade will allow higher concurrency. The new download design uses `concurrency + 1` as the goal's in-flight cap, so raising the setting automatically scales the queue.
- **RESOLVED** (2026-05-27, commit `f8865f7`): Two new Settings keys `polygon_min_request_interval_s` and `polygon_concurrency` loaded at coordinator startup and passed to `PolygonProvider` + `DownloadManager.provider_concurrency`. `PUT /api/settings/polygon-tier` to set, `DELETE` to clear. Requires coordinator restart to apply (provider construction happens at startup).

---

## How to use this file

When **deferring work** in a new spec:
1. Add a section under the relevant domain (or create one).
2. Link back to the spec that deferred it (`specs/YYYY-MM-DD-...md`).
3. State *why* (the actual constraint, not just "v1").
4. Sketch *what's needed* if you can — it's easier now than later.

When **starting a new spec**:
1. Skim the relevant domain section.
2. If a deferred item now falls in scope, *lift* its entry into the new spec rather than re-deferring it.
3. If you keep re-deferring the same items, that's the signal to promote them into a roadmap spec.
