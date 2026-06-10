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

### Tick context `pd.to_datetime` crashes on mixed-tz string timestamps
- **Surfaced by:** algorithm portfolio audit 2026-06-01.
- **RESOLVED** (2026-06-02, commit `eb67772`): both call sites in `backtest_tick_context.py` (disk-load + on_miss paths) now use `pd.to_datetime(col, utc=True).dt.tz_convert("UTC").dt.tz_localize(None)`. New unit test `test_market_data_loads_mixed_tz_string_timestamps_without_crash` pins the regression.

### `ctx.market_data` source fallback ignores bars cache when default_source is set
- **Surfaced by:** algorithm portfolio audit 2026-06-01. Manifest declares `VIX source:yfinance` so bars cache key is `(yfinance, VIX, 1day)`. Algorithm calls `ctx.market_data("VIX", "1day", 1)` without `source=` kwarg. Tick context defaults to `_default_source` (the manifest's first asset's source, e.g. `polygon`) and tries to load polygon VIX, missing the yfinance entry in the cache.
- **Why deferred:** algorithms can work around by passing `source=` explicitly.
- **What's needed:** in `BacktestTickContext.market_data`, if `(default_source, symbol, tf)` is not in `self._bars`, search by `(symbol, tf)` for any source before falling through to disk. This matches the manifest declarative intent: "if I asked for VIX, find VIX wherever it's cached."

### Option-chain pre-download is unbounded and uncancellable
- **Surfaced by:** algorithm portfolio audit 2026-06-01. `_download_option_contracts` iterates every monthly 3rd-Friday expiration in the date range and downloads each contract found by polygon's discovery endpoint (~13s/contract). A single backtest can trigger 100+ contract downloads. Cancelling the job marks it cancelled in DB but does not abort the inflight download loop.
- **Why deferred:** workable for now if test windows are picked carefully (must hit already-cached expirations).
- **What's needed:** (a) pass the cancel-token into `_download_option_contracts` and check between contracts; (b) consider bounding contract discovery to N-deltas-from-ATM rather than the whole chain; (c) defer contract download until the algorithm actually requests bars for the contract (lazy load) rather than pre-downloading the whole monthly chain.

### Algorithm SDK should expose ET wall-clock helpers
- **Surfaced by:** algorithm portfolio audit 2026-06-01.
- **RESOLVED** (2026-06-02, commits `0807a3a` + `70e8f60`): `ctx.market_time()` (tz-aware datetime in the manifest's `market_timezone`) and `ctx.is_market_open()` (NYSE calendar via `pandas_market_calendars`, including holidays) implemented on `BacktestTickContext` and `LiveTickContext`. Manifest `market_timezone:` field with smart defaults per `asset_types` shipped in commit `a1392c5`. 3 affected algorithms (`options-rolling-calls`, `options-ema-spreads`, `options-condor-martingale`) migrated and merged via the 6/03 algorithm-PR rollout.

### Separate `downloads:` block from `assets:` in manifest
- **Surfaced by:** algorithm portfolio audit 2026-06-01. The `assets:` block currently does triple duty: declares what to pre-download, what to pre-warm in the bars cache, and what the `default_source` should resolve to. Algorithms with dynamic universes (options chain underlyings, multi-symbol momentum) have to choose between (a) listing every symbol to control downloads but ballooning the manifest, or (b) listing fewer and relying on on-demand `ctx.market_data` to lazy-load.
- **Why deferred:** workable today by listing all required symbols.
- **What's needed:** new manifest block `downloads:` (or rename `assets:` → `downloads:` and add `cache_warm:` separately). Migrate existing algorithms. Algorithm SDK docs updated to explain the distinction.

### SQLite write contention on parallel sweep trials
- **Surfaced by:** algorithm portfolio audit 2026-06-01. Even `parallelism: 1` sweeps occasionally produced "database is locked" errors on the second trial because the BacktestRunner shares the SQLite connection pool with ApScheduler polling jobs (Alpaca/Tradier activity sync). The first trial always completed; the second sometimes lost the race.
- **Why deferred:** the lock is transient and a retry would resolve it; workaround was to use `parallelism: 1` and retry the sweep.
- **What's needed:** either (a) configure SQLite with `PRAGMA busy_timeout = 30000` and a longer-tolerance write lock, (b) move write-heavy sweep trial inserts to a dedicated connection with retry-on-busy, or (c) bite the bullet and migrate to Postgres for any deployment running parallel sweeps.

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

### Push canonical-symbol manifest+algorithm fixes to ~17 upstream algorithm repos
- **Surfaced by:** algorithm portfolio audit (2026-06-01).
- **RESOLVED** (2026-06-03): `scripts/push_algorithm_patches.py` opened 19 draft PRs across the `ElectricJack/quilt-algo-*` repos with templated bodies linking the two specs. All 19 were squash-merged on 2026-06-03 evening. Subsequent sizing-cap fixes for 6 vulnerable options/spread algos (post `options-ema-spreads` -21,132% diagnosis) were pushed to the same branches and merged in the same batch.

### Equity curve doesn't reflect MTM during long-running positions
- **Surfaced by:** 2026-06-03 options-ema-spreads diagnosis. A backtest that lost $10.67M on a single expiry showed `portfolio_value = $50,000` flat for the entire 6-month window in `equity_curve`, then jumped to `-$10,516,024` only on the last bar. Equity snapshots only book realized cash; open positions are not marked to market.
- **Why deferred:** the framework's margin-check fix (commit `33b140e`) blocks the underlying overexposure path going forward, so the misleading equity curve no longer hides catastrophic drawdowns in practice. Still worth fixing for diagnostic visibility on legitimate strategies.
- **What's needed:** add `position_mv` (sum of `position.market_value` across open positions) to each equity-snapshot record. The engine already computes `account_value = cash + positions_market_value` inline for the tick context — surface that into the snapshot. Existing chart code can fall back to `cash` if `position_mv` absent.

### Three algorithms produce zero trades despite clean pipeline runs
- **Surfaced by:** algorithm portfolio audit (2026-06-01). All three install cleanly under the canonical-symbol contract and run end-to-end without errors, but their algorithm logic never signals under tested parameters. These are algorithm-internal issues, not framework issues.
- **Why deferred:** the canonical-symbol refactor's contract is intact; these are pre-existing algorithm bugs/strategy choices unrelated to the refactor. Worth treating as one-off algorithm fixes when the user wants those specific strategies to trade.
- **What's needed (per algo):**
  - **`options-ema-spreads-v2`:** entry filter chain (EMA + delta-match + bid<ask all-must-pass) rejects every candidate in tested windows. Investigate whether the filter chain is over-restrictive or whether parameter ranges weren't tested wide enough. Could be a genuine "no opportunities in test window" or a bug.
  - **`options-rolling-calls`:** algorithm hardcodes ET-naive `9:44 ≤ hour ≤ 15:30` window but compares against `ctx.timestamp` (UTC). Fires only in the UTC slice that happens to overlap (roughly UTC 14:44–20:30 when DST is active), which often misses NY trading hours entirely. Fix: convert `ctx.timestamp` to ET before comparing, OR adopt the `ctx.market_time()` SDK helper proposed in the Backtesting backlog item above.
  - **`options-condor-martingale`:** default `martingale_quantities="0,2,5,15,45"` starts with quantity 0 (skip first cycle). Combined with strict ADX/IV/bid-ask/gamma pre-trade filters, no entries fire in the tested 1-year window. Either the default config needs revision, the filters need relaxation, or the algo needs a longer historical window. Worth a strategy review.

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

### Faster coverage prewarm (column projection + parallel scans)
- **Deferred from:** [2026-06-09-data-snapshot-cache-design.md](specs/2026-06-09-data-snapshot-cache-design.md)
- **Why deferred:** the snapshot-cache design makes warm reads ~5 ms but the *cold* coordinator-restart path still pays the full prewarm cost (~12 s on ~250 non-option symbols). The snapshot design treats this as out-of-scope and addresses it only by ensuring readers wait cleanly on the ready event instead of racing. Reducing the prewarm itself is a follow-up.
- **What's needed:** two independent wins in `coordinator/services/coverage_index.py::_scan` —
  1. **Column projection:** `pd.read_parquet(path, columns=["timestamp"])` instead of loading all OHLCV columns. Measured ~60 ms → ~35 ms per file on SPY's 421k-row 1-min parquet; ~halving per-symbol cost.
  2. **Parallel scans:** the prewarm currently iterates symbols sequentially in one `asyncio.to_thread` call. A `concurrent.futures.ThreadPoolExecutor` with 4–8 workers would cut the wall-clock further (pandas/pyarrow release the GIL during parquet decode).
  Together these should drop prewarm from ~12 s to ~1–2 s on this disk, getting the cold-restart page load into the same "couple hundred ms" range as warm navigation.

### `DatasetGoal` declarative model
- **Deferred from:** [2026-05-28-datasets-framework-design.md](specs/2026-05-28-datasets-framework-design.md)
- **Why deferred:** v1 ships explicit per-download queueing (CLI / REST `POST /api/datasets/downloads`). A declarative goal model parallel to `DataGoal` ("keep all house disclosures from 2020 onward fresh") is real value but couples the datasets lane to a second new subsystem before we've used the first one in anger.
- **What's needed:** `DatasetGoal` DB model (`(dataset_name, params_json, status, last_progress)`), a `DatasetGoalProcessor` running every 60s (mirroring `GoalProcessor`), and goal-vs-on-disk diffing to compute the next `DatasetDownload` to queue.

### `DatasetRefreshScheduler` for live mode
- **Deferred from:** [2026-05-28-datasets-framework-design.md](specs/2026-05-28-datasets-framework-design.md)
- **Why deferred:** live algorithms need datasets refreshed without manual queueing, but the right scheduler shape depends on knowing real usage patterns (cron-like? per-dataset cadence on the spec? quota-aware backoff?). v1 expects external scheduling (a cron job hitting REST, or manual CLI runs).
- **What's needed:** a `DatasetRefreshScheduler` alongside the existing `GoalProcessor` that periodically queues `DatasetDownload` refreshes per dataset's configured cadence. Refresh strategy: for datasets with native "since" cursors, fetch only `knowledge_date > max(on_disk)`; for full-history endpoints (e.g. income_statement), accept periodic full re-fetch.

### Shared `AsyncJobManager` base for ResearchJob + DatasetDownload
- **Surfaced by:** [2026-05-28-datasets-framework-design.md](specs/2026-05-28-datasets-framework-design.md)
- **Why deferred:** `ResearchJobManager` (shipped in the backtest-lab merge) and the new `DatasetJobDispatcher` implement the same shape — DB row → `asyncio.create_task` → poll → cancel-flag → progress callback → orphan recovery. The datasets spec aligns column names and status vocabulary so a future extraction is mechanical, but doing the refactor mid-spec introduces risk to two subsystems for no immediate user value.
- **What's needed:** extract a shared `AsyncJobManager` base class. Subclasses provide: model class, dispatcher map (or single execute callable), recovery query. Migrate `ResearchJobManager` and `DatasetJobDispatcher` to inherit.

### Retry-with-backoff for adapter 5xx
- **Deferred from:** [2026-05-28-datasets-framework-design.md](specs/2026-05-28-datasets-framework-design.md)
- **Why deferred:** v1 fails the job on 5xx; user re-queues. Adding bounded retry (e.g. 3 attempts with exponential backoff) is cheap but only worth doing if 5xx noise proves frequent in practice with real FMP traffic.

### Per-dataset quota budgets
- **Deferred from:** [2026-05-28-datasets-framework-design.md](specs/2026-05-28-datasets-framework-design.md)
- **Why deferred:** v1 enforces only a per-provider daily limit. Per-dataset budgets ("spend at most 50 calls/day on house_disclosures") are useful when multiple goals compete, but moot until `DatasetGoal` ships.

### Lazy / streaming `DataFrame` returns
- **Deferred from:** [2026-05-28-datasets-framework-design.md](specs/2026-05-28-datasets-framework-design.md)
- **Why deferred:** v1 reads full parquet into pandas. Polars `LazyFrame` or a DuckDB query layer becomes valuable when a single dataset's file exceeds ~1GB or when an algorithm wants pushdown filters server-side. Not the case yet.

### Year-partitioned parquet for huge datasets
- **Deferred from:** [2026-05-28-datasets-framework-design.md](specs/2026-05-28-datasets-framework-design.md)
- **Why deferred:** v1 stores one parquet per (dataset, symbol or firehose). Upsert helper warns at 500MB. Year-partitioning (`<name>/<year>.parquet`) lets us skip irrelevant years on read, but adds a path-resolution layer that's not worth it until a real dataset crosses the threshold.

### Bulk operations on datasets in UI
- **Deferred from:** [2026-05-28-datasets-framework-design.md](specs/2026-05-28-datasets-framework-design.md)
- **Why deferred:** Market data has Compare / Fill Gaps / Delete; datasets v1 is view-only. Each operation has different semantics for bitemporal data (e.g. "delete" — only later knowledge_date rows? all amendments?) that need their own thinking.

### Auto-discovery of FMP endpoints
- **Deferred from:** [2026-05-28-datasets-framework-design.md](specs/2026-05-28-datasets-framework-design.md)
- **Why deferred:** intentionally NOT a v1 feature — every dataset is explicitly registered with a deliberate bitemporal mapping. Auto-discovery (generate `DatasetSpec` from an FMP endpoint catalog) would let users add datasets without thinking about which column is the knowledge timestamp, defeating the framework's safety guarantee. Revisit only with a strong opinion on safe defaults.

---

## Research Lab dashboard

### Phase 3 — Walk-Forward submission UI
- **Deferred from:** [2026-05-30-research-lab-dashboard-design.md](specs/2026-05-30-research-lab-dashboard-design.md)
- **Why deferred:** Phases 1+2 deliver session creation + sweep submission. Walk-forward submission is a sister form to `NewSweepModal` with additional fields (train_years, test_years, step_months, objective). The backend (`POST /sessions/{id}/walk-forward`) is fully shipped — only the form is missing.
- **What's needed:** `NewWalkForwardModal.tsx` mirroring `NewSweepModal.tsx`'s structure, with the four extra fields. Add a "New Walk-Forward" button alongside "New Sweep" on `ResearchSessionDetail.tsx`. Possibly differentiate the job row rendering by `kind` field.

### Phase 4 — Sweep results matrix
- **Deferred from:** [2026-05-30-research-lab-dashboard-design.md](specs/2026-05-30-research-lab-dashboard-design.md)
- **Why deferred:** Phases 1+2 link out from each completed sweep's job row to individual `BacktestRunDetail` pages. The matrix is the "compare 50 trials at once" surface — sortable metric columns, per-config-parameter columns, click-through to single-run detail, possibly faceted filtering. Substantially more product work than per-row linking.
- **What's needed:** new region on `ResearchSessionDetail.tsx` (third stacked region under header + jobs list) rendering a TanStack table built from the union of all completed runs' metrics + config_overrides. Likely an endpoint extension to bulk-fetch metric summaries for a session's runs in one call.

### Phase 4 or 5 — Walk-forward stitched OOS equity chart
- **Deferred from:** [2026-05-30-research-lab-dashboard-design.md](specs/2026-05-30-research-lab-dashboard-design.md)
- **Why deferred:** Walk-forward jobs produce concatenated out-of-sample equity (`concatenate_oos_curves()` already on the backend, I9 invariant). Rendering it inline with per-fold boundary markers is its own visualization scope.
- **What's needed:** chart component using `lightweight-charts` (already a dashboard dep — used by existing equity views). Endpoint to fetch the stitched curve + per-fold boundary timestamps for a completed walk-forward job.

### Phase 5 — In-browser markdown/HTML report viewer
- **Deferred from:** [2026-05-30-research-lab-dashboard-design.md](specs/2026-05-30-research-lab-dashboard-design.md)
- **Why deferred:** Phases 1+2 surface the file paths from `POST /sessions/{id}/report` in a toast; the user opens them out-of-band. An in-app viewer needs (a) a way for the coord to serve the generated HTML files (currently they're written to `data/research_reports/` and not exposed), (b) a markdown renderer, (c) decisions about navigation back to the session.
- **What's needed:** static file route serving `data/research_reports/*`, plus a `<ReportViewer>` component that takes the session id, fetches the report HTML, and renders it in a scrollable pane.

### Manifest-derived structured form for JSON config fields
- **Deferred from:** [2026-05-30-research-lab-dashboard-design.md](specs/2026-05-30-research-lab-dashboard-design.md)
- **Why deferred:** v1 uses `JsonTextField` (textarea + JSON parse validation) for `base_config`, `parameter_space`, and `pre_registered_criteria`. A structured form derived from each algorithm's `config_schema` would render typed inputs — sliders for numeric ranges, dropdowns for enums, multi-select for arrays — eliminating the JSON typing entirely for `base_config`. Significant product work; only partially applicable to `parameter_space` (which references config keys but values are search ranges, not config values).
- **Swap-in target locked:** [2026-05-30-session-experiment-binding-design.md](specs/2026-05-30-session-experiment-binding-design.md) introduces `<ExperimentConfigEditor>` — a wrapper that today renders three `<JsonTextField>`s side-by-side (base_config | parameter_space | criteria). The follow-up work replaces ONLY this component's internals with per-field rows (each row has a fix-vs-sweep toggle, schema-typed input for the fix mode, range/list editor for the sweep mode). The session modal, the hooks, the API payload, and the entire backend are unchanged. JSON-textarea fallback remains for algorithms whose `config_schema` is unpopulated.
- **What's needed:** `config_schema → JSON-schema` mapper (or use the schema directly if it's already JSON Schema), per-type input components (number/select/multi-select/bool/string), the fix-vs-sweep toggle UX, range/list editors, validation that every required schema field appears in either base_config or parameter_space.

### Session deletion / archive
- **Deferred from:** [2026-05-30-research-lab-dashboard-design.md](specs/2026-05-30-research-lab-dashboard-design.md)
- **Why deferred:** Sessions are immutable pre-registrations of an experiment. There's intentionally no edit/delete in v1. When the session list grows enough to want tidy-up, "archive" (hide from default list, retain the row) is the right pattern — hard delete should probably never exist for research records.

### Session list filters / search
- **Deferred from:** [2026-05-30-research-lab-dashboard-design.md](specs/2026-05-30-research-lab-dashboard-design.md)
- **Why deferred:** With <50 sessions, browse-by-scrolling is fine. Build when the list gets unwieldy.

### Bulk job operations
- **Deferred from:** [2026-05-30-research-lab-dashboard-design.md](specs/2026-05-30-research-lab-dashboard-design.md)
- **Why deferred:** Cancel-all-running, retry-failed, etc. — only worth building when someone hits the friction.

### Compare-runs view
- **Deferred from:** [2026-05-30-research-lab-dashboard-design.md](specs/2026-05-30-research-lab-dashboard-design.md)
- **Why deferred:** Pick N runs and render their metrics + equity curves side-by-side. The natural Phase 6 once the Phase 4 results matrix exists — the matrix is "all runs", compare-view is "this specific subset".

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
