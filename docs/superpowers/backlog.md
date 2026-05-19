# Deferred-Work Backlog

Items intentionally cut from a shipped spec. Consult this file before starting any new spec — if a deferred item is now in scope, lift the entry here rather than re-deferring it. When a new spec defers something, add it here with a link back.

---

## Positions

### Multi-leg / spread-aware position close
- **Deferred from:** [2026-05-18-close-positions-design.md](specs/2026-05-18-close-positions-design.md)
- **Why deferred:** v1 closes single-leg market orders only. Multi-leg positions (options spreads) need coordinated closing across legs and use a different broker call (`submit_multileg_order` with inverted sides).
- **What's needed:** a position model that knows when broker rows belong to a single user-intent (e.g. an iron condor) and closes them atomically; UI that shows the *strategy*, not just the legs.

### Partial position close
- **Deferred from:** [2026-05-18-close-positions-design.md](specs/2026-05-18-close-positions-design.md)
- **Why deferred:** v1 closes the full quantity shown on the row. Partial close needs a quantity input + validation against current broker quantity.

### Limit / stop close orders
- **Deferred from:** [2026-05-18-close-positions-design.md](specs/2026-05-18-close-positions-design.md)
- **Why deferred:** v1 submits market orders only. Limit needs price input, unfilled-state handling, and an order-management view to cancel/replace.

### Bulk "close all" action
- **Deferred from:** [2026-05-18-close-positions-design.md](specs/2026-05-18-close-positions-design.md)
- **Why deferred:** confirmation UX and error aggregation (one leg fails out of N) need design.

### Coordinate manual close with running algorithm
- **Deferred from:** [2026-05-18-close-positions-design.md](specs/2026-05-18-close-positions-design.md)
- **Why deferred:** v1 close endpoint doesn't notify the algo. The algo sees the position disappear on its next broker sync but may attempt to re-open it.
- **What's needed:** a coord→worker signal "this position was force-closed by user, treat as final"; algo SDK API to receive it.

### `open_position` doesn't forward `asset_type` to the broker adapter
- **Surfaced by:** crypto-close fix on 2026-05-18 (commits `784ca9c` / `416252c` / `1a52a9b`).
- **Why deferred:** the close-position fix threaded `asset_type` through `submit_order` so AlpacaAdapter picks `TimeInForce.GTC` for crypto. The `open_position` route's sequential-fallback path at `coordinator/api/routes/accounts.py` still calls `adapter.submit_order(...)` without `asset_type`, so opening a crypto position via the dashboard will hit the same Alpaca `invalid crypto time_in_force` error.
- **What's needed:** in the open-position handler's sequential fallback, pass `asset_type=leg.asset_type` (or the appropriate leg field) to each `submit_order` call. Add a regression test mirroring `test_close_passes_asset_type_to_adapter`.

### Holistic position-tracking model
- **Deferred from:** [2026-05-18-close-positions-design.md](specs/2026-05-18-close-positions-design.md) (implicit — surfaces as common dependency above)
- **Why deferred:** today positions live in two places — broker's `get_positions` and Quilt's internal `Position` table — with no canonical join. Each new feature (multi-leg, partial, limit, manual-vs-algo attribution, lot tracking) hits this seam.
- **What's needed:** a roadmap spec covering: position identity across legs, ownership (manual vs which algo run), lot-level cost basis, reconciliation against broker truth, and what the data model should look like. **Promote this to a `docs/superpowers/roadmaps/position-tracking.md` once 2-3 more position features have accumulated deferred work here** — the shape will be clearer then than it is today.

---

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
