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

### Timezone-aware backtest engine
- **Surfaced by:** options-spreads-martingale backtests producing 0 trades (2026-05-25). The algo checks `now.time() < time(15, 45)` meaning 3:45 PM ET, but sim_time is UTC. The 5-minute entry window (15:45–15:50 config) silently misaligns.
- **Why deferred:** workaround exists (adjust config times to UTC), but every new algo with time-of-day logic will hit the same trap.
- **What's needed:** add `timezone` field to manifest (e.g., `timezone: America/New_York`). Engine converts sim_time to algo's declared timezone before calling on_tick. `ctx.timestamp` returns tz-aware datetime. Default to UTC if not specified. Validate at install: reject manifests with time-based configs that don't declare a timezone.

### Daily/weekly option expiration data
- **Surfaced by:** options-spreads-martingale and options-condor-martingale backtests (2026-05-25). These are 0DTE/1DTE strategies that need contracts expiring every trading day. Current contract discovery only finds monthly expirations (3rd Friday), so the algo's `_next_expiration()` returns tomorrow's date but no chain exists for it → 0 trades.
- **Why deferred:** downloading daily expirations increases data volume ~20x. Polygon free tier rate limits make this impractical without a paid plan.
- **What's needed:** extend contract discovery to find daily/weekly expirations. Consider making expiration frequency configurable in the manifest (`expiration_frequency: daily | weekly | monthly`). May require Polygon paid tier for reasonable download times.

### Orphan backtest cleanup on startup
- **Surfaced by:** coordinator restarts leaving backtests stuck in "running" status (2026-05-25).
- **Why deferred:** manual SQL cleanup works. Similar to download manager's existing `recover_orphaned_downloads()`.
- **What's needed:** on startup, mark any "running"/"downloading_data" backtest rows as "failed" with message "Orphaned by coordinator restart".

---

## Live data feeds

### Per-stream `on_disconnect` callback wired into broker handles
- **Surfaced by:** [2026-05-18-unified-live-subscriptions-design.md](specs/2026-05-18-unified-live-subscriptions-design.md)
- **Why deferred:** `_stale_stream_sweep` detects disconnects via a heuristic (no tick for N seconds). A first-class `on_disconnect` callback wired directly into `_AlpacaStreamHandle` and `_TradierStreamHandle` would detect drops instantly and with less false-positive risk.
- **What's needed:** add an optional `on_disconnect` param to `MarketDataStreamHandle.close` (or as a callback on the handle itself); wire it in each broker adapter so the aggregator is notified immediately when the underlying WS connection closes.

### `add_symbols` / `remove_symbols` on stream handles
- **Surfaced by:** [2026-05-18-unified-live-subscriptions-design.md](specs/2026-05-18-unified-live-subscriptions-design.md)
- **Why deferred:** today, adding or removing a symbol from a running subscription tears down and restarts the whole stream. Both `_AlpacaStreamHandle` and `_TradierStreamHandle` need `add_symbols` / `remove_symbols` methods so multi-symbol updates are surgical rather than restart-from-scratch.
- **What's needed:** implement `add_symbols(syms)` / `remove_symbols(syms)` on each handle class; update `LiveFeedAggregator.start_subscription` / `stop_subscription` to call them when a handle already exists for that broker.

### Validate `Algorithm.assets` shape at install time
- **Surfaced by:** unified-live-subscriptions feature (2026-05-18).
- **Why deferred:** the `assets` field on `Algorithm` is freeform JSON. An algorithm installed with a malformed assets list silently skips subscription wiring.
- **What's needed:** add a Pydantic validator (or JSON Schema) that checks each entry has `broker`, `symbol`, and `asset_class`; reject installs that fail validation with a clear 422.

### Algorithm install fails opaquely when package dir is orphaned
- **Surfaced by:** post-migration install attempt on 2026-05-18.
- **Why deferred:** `install_from_url` calls `pm.clone_repo` which fails with `fatal: destination path '...' already exists and is not an empty directory` whenever a previous install's on-disk package wasn't cleaned up. Common after DB migrations that drop algorithm rows without touching `data/packages/`, or after a partially-failed prior install.
- **What's needed:** when the destination dir exists, detect that it's a valid git clone of the same repo and `git fetch origin && git reset --hard origin/<default-branch>` to bring it to the latest commit instead of cloning. If the dir exists but is NOT a clone of the expected repo, return a clear 409 with a message telling the user to remove it. Update `PackageManager.clone_repo` (or wrap it at the route layer).

### Manifest `data:` block for custom data dependencies (scrapers, CSVs)
- **Surfaced by:** backtest failure on alpha-picks-rebalancer (2026-05-19). The algo called `ctx.data("alpha-picks-scraper")` but the backtest runner couldn't find the file.
- **Why deferred:** the immediate fix (smarter path resolution in `StandaloneDataProvider`) unblocks the user. The structural fix — declaring custom data deps in the manifest — requires design work on how scrapers + custom CSVs fit into the manifest schema alongside `assets:`.
- **What's needed:** add a `data:` block to the manifest: `[{source: "alpha-picks-scraper", type: "scraper"}]`. The backtest runner pre-checks that all declared data sources exist before starting. The deploy flow ensures the scraper is registered and has run at least once. The system surfaces "missing data dependency" errors clearly instead of failing mid-backtest.

### Replace synthetic backtest clock with union-of-symbol-timelines clock
- **Surfaced by:** backtest engine edge cases on alpha-picks-rebalancer (2026-05-19). The synthetic clock (all-zeros business-day series) broke fills ($0 prices), position valuation ($0 market value), and position snapshots. Each was patched individually with per-symbol lookups, but the root cause remains.
- **Why deferred:** the per-symbol lookup patches work for v1. The structural fix requires rethinking the engine's clock construction: instead of pre-building the clock before the first tick, discover which symbols the algo actually uses after the first tick, merge their timestamps into a real clock, then replay.
- **What's needed:** after the first `on_tick` call, inspect `ctx._bars` for all symbols the algo loaded via `market_data()`. Build the clock from the UNION of all those timelines (deduped + sorted). Re-run from bar 0 with the real clock. This eliminates the synthetic clock entirely — every bar in the clock corresponds to a real price in at least one symbol, so fills, valuation, and snapshots all use real data without special-case lookups. The per-symbol lookup code can then be simplified back to using the clock bar directly.

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

### SPA / White's Reality Check significance test
- **Deferred from:** [2026-05-27-crypto-tsmom-research-program-design.md](specs/2026-05-27-crypto-tsmom-research-program-design.md)
- **Why deferred:** v1 ships Bonferroni and Benjamini-Hochberg corrections. SPA is more powerful for dependent hypothesis sets (which is what parameter sweeps produce) but the implementation requires bootstrap-of-bootstraps and is meaningfully more involved.
- **What's needed:** implement Hansen's SPA test or White's bootstrap reality check in `coordinator/services/validation/multi_test.py`. Should accept a list of strategy return series and return a corrected p-value for the best performer.

### Pluggable regime taggers
- **Deferred from:** [2026-05-27-crypto-tsmom-research-program-design.md](specs/2026-05-27-crypto-tsmom-research-program-design.md)
- **Why deferred:** v1 ships only a BTC-trailing-return tagger, which is circular for crypto strategies (the regime measures what the strategy trades). Acknowledged limitation noted in spec.
- **What's needed:** make `regime.py` accept a `RegimeTagger` protocol; ship a VIX-based equity tagger and a user-supplied function tagger. Each strategy's manifest declares which tagger it consumes.

### Bayesian / TPE search in sweep
- **Deferred from:** [2026-05-27-crypto-tsmom-research-program-design.md](specs/2026-05-27-crypto-tsmom-research-program-design.md)
- **Why deferred:** grid + random + latin-hypercube cover the v1 needs. TPE / Bayesian search would be more sample-efficient for large parameter spaces.
- **What's needed:** add Optuna integration to `sweep.py` as a fourth search type. The DB schema for `OptimizationSession` already stores the search type as a string, so this is additive.

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
- **What's needed:** change `DownloadManager.__init__` to accept `on_download_complete: list[Callable]` (or expose `add_completion_listener`); rewire `coordinator/main.py` to register each consumer separately instead of through a fan-out function.

### Paid-tier polygon concurrency setting
- **Surfaced by:** [2026-05-27-options-goal-incremental-download-design.md](specs/2026-05-27-options-goal-incremental-download-design.md)
- **Why deferred:** the user is on polygon's free tier (1 concurrent / ~13s latency). A paid-tier upgrade will allow higher concurrency. The new download design uses `concurrency + 1` as the goal's in-flight cap, so raising the setting automatically scales the queue.
- **What's needed:** a settings UI / env var that overrides `DownloadManager._DEFAULT_PROVIDER_CONCURRENCY["polygon"]`. Plumb through `coordinator/main.py` startup. No goal-side changes required.

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
