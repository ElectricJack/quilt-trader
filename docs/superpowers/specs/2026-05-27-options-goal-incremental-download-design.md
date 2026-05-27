# Options data-goal incremental download

## Background

`GoalProcessor._download_options` (`coordinator/services/goal_processor.py`)
drives data acquisition for options data goals after discovery completes. The
SPY options goal currently has 130,344 discovered contracts. The download
phase has three structural problems:

1. **Unbounded enqueue.** Every tick (cron `* * * * *`) the processor scans
   all 130K contracts and enqueues up to `DOWNLOAD_BATCH=50` new
   `market_data_downloads` rows. Because polygon's per-provider semaphore
   in `DownloadManager` is fixed at concurrency 1 (rate-limited ~13s/request
   on the free tier), one row drains every ~13s while ~50 new rows are added
   every minute. The queue grows monotonically, manual downloads sit behind
   it, and other goals starve.

2. **Per-tick filesystem storm.** For each of 130K contracts the loop calls
   `self._ds.load_market_data(provider, sym, "1day")` which runs
   `pd.read_parquet` (or stats the file path). At minute cadence this is
   several seconds of disk I/O per tick to compute information that changes
   by ~5 entries per minute.

3. **Restart-fragile.** Whatever is in-flight when the coordinator restarts
   gets marked `failed` with `Orphaned by coordinator restart`. Three
   restarts during today's session orphaned ~130 contracts. With the user
   developing actively this compounds.

The user wants the system to run unattended over many days (days, plausibly
weeks on the free tier) and converge to "all downloadable contracts saved"
without manual intervention.

## Constraints

- **Polygon free tier: 1 concurrent request, ~13s latency.** Total wall
  time for 130,344 contracts is approximately 470 hours regardless of
  scheduling shape. This redesign does not change throughput; it changes
  queue shape, observability, retry behavior, and resilience.
- **A paid-tier upgrade is anticipated.** A future setting will raise
  polygon's semaphore. The design must continue to work — and remain
  responsive — at higher concurrency.
- **The system must self-heal.** Coordinator restarts, transient polygon
  errors, and one-off bad contracts must not require operator attention.

## Goals

- Cap this goal's visible queue at **2 in-flight rows** in
  `market_data_downloads` (1 running + 1 queued). When polygon's concurrency
  is later raised, the cap scales with it (default = `concurrency + 1`).
- Drive new enqueues from **download-completion events**, not the cron tick.
  The cron tick remains as a reconcile / safety net at minute cadence.
- Replace per-tick 130K-contract filesystem reads with a **cached on-disk
  symbol set** per provider, refreshed incrementally on completion events
  and re-listed at most once per minute.
- **Per-symbol exponential backoff** on failures so permanent failures stop
  burning the polygon lane but stay eligible for eventual retry. No manual
  blacklist.
- No new database tables, no new columns on `market_data_downloads`.

## Non-goals

- Speeding up downloads. (Constrained by polygon's free tier — out of
  scope.)
- Generalizing this to all goal types. The bars goal already works; this
  is options-specific.
- Per-contract observability rows (status table, retry counters as columns).
  Derivation from existing tables is sufficient.

## Design

### Per-tick algorithm

Each tick of `_download_options(goal)`:

1. **Refresh on-disk cache if expired** (TTL 60 s, see "Cached disk view"
   below). Otherwise reuse cached set.
2. Compute three sets restricted to the goal's discovered symbols:
   - `on_disk` — symbols whose `data/market/<provider>/<sym>/1day.parquet`
     exists, intersected with `discovered_contracts` symbols.
   - `in_flight` — symbols with a `market_data_downloads` row where
     `provider = <goal provider>` and `status IN ('queued', 'running')`.
   - `recently_failed` — symbols with a row in status `failed` whose
     exponential-backoff window has not yet expired (see "Failure handling").
3. `eligible_pending = discovered − on_disk − in_flight − recently_failed`.
4. **Update goal row**: `completed_items = len(on_disk)`,
   `last_processed_at = utcnow()`, `error_message = NULL`.
5. **Enqueue**: if `len(in_flight) < CAP` (default 2), select up to
   `CAP - len(in_flight)` symbols from `eligible_pending` and create one
   `market_data_downloads` row per symbol via `DownloadManager.create_download`
   (one symbol per row — single-symbol downloads make per-symbol progress and
   cancellation cleaner).
6. **Terminal transition**: if `on_disk ⊇ discovered AND in_flight is empty`,
   set `goal.phase = "completed"` and `goal.status = "completed"`.

`CAP` is `polygon_concurrency + 1` (today: 2). Read from
`DownloadManager._provider_semaphores[provider]._value` initial value or
exposed via a new helper `download_manager.concurrency_for(provider)`.

### Cached disk view

Goal processor maintains:

```python
self._disk_cache: dict[str, set[str]] = {}      # provider -> set of symbol dirs
self._disk_cache_ts: dict[str, datetime] = {}   # provider -> last full scan time
```

- **Refresh path 1 (lazy, on tick):** at start of `_download_options`, if
  `now - self._disk_cache_ts[provider] >= 60s`, run a single
  `os.scandir(data/market/<provider>)`. For each `DirEntry`, check
  `os.path.exists(entry.path + "/1day.parquet")` — one stat per entry. At
  130K entries this is ~1s, executed at most once per minute, never on the
  enqueue path.
- **Refresh path 2 (incremental, event-driven):** the
  `DownloadManager.on_download_complete(provider, symbols)` callback adds
  each symbol to `self._disk_cache[provider]` if the download status was
  `completed` or `completed_with_errors` AND the parquet file now exists.

The full scan happens at most once per minute. The hot enqueue path never
calls `os.listdir`.

### Event-driven enqueue

`DownloadManager` already accepts an `on_download_complete` callback (set in
`coordinator/main.py`). Wire `GoalProcessor` into it. When a download
completes:

1. Update the in-memory disk cache (above).
2. For each active options goal whose `discovered_contracts` includes any of
   the completed symbols, run the enqueue step of `_download_options` (steps
   5 of the per-tick algorithm — without the disk rescan, since the cache is
   already current).

This keeps the polygon lane saturated continuously. The cron tick remains
necessary for: failed-backoff transitions, goal completion detection, the
periodic disk-cache refresh, and as a safety net if a completion callback is
ever missed.

### Failure handling

Failures are classified into two categories at lookup time:

- **Terminal**: the latest failed row's `error_message` matches the
  pattern `"no data returned"`. This means polygon (or the provider) has
  authoritatively answered "this contract has no bars for the requested
  window" — re-asking will never produce different data. The symbol is
  excluded from `pending` permanently, counted into the goal's
  `failed_items`, and counts toward the "done" criterion for phase
  transition. If a later `completed` row ever lands for the same symbol
  (e.g., after a manual retry that succeeds because the provider added
  data), the state resets and the next failure starts the count over.
- **Backed off (transient)**: any other failure — rate limit, network,
  HTTP 5xx, etc. Treated with the existing per-symbol exponential
  backoff.

Per-symbol exponential backoff for the transient bucket:

```
failure_count = number of rows for this (provider, symbol) with
                status='failed' since the most recent 'completed' row
                (or since row 0 if none).
delay_seconds = min(60 * 2**(failure_count - 1), 86_400)
last_failed   = max(completed_at where status='failed') for that symbol
eligible      = now - last_failed >= delay_seconds
```

- 1st failure → 1 min, 2nd → 2 min, 3rd → 4 min, 4th → 8 min, ... 12th → 24 h cap.

The "done" criterion is `on_disk ∪ terminal ⊇ discovered` with `in_flight = 0`.
- After a successful download, the count resets (the next failure starts
  over at 1 min).
- Single grouped query against `market_data_downloads`, restricted to the
  goal's provider. Since goal-driven downloads always use single-symbol rows
  (`symbols=[sym]`), the symbol is extractable via
  `json_extract(symbols, '$[0]')`. For 130K rows this is a few tens of ms in
  SQLite; if profiling shows it dominates, add a generated `symbol` column
  with an index. No schema change is needed up front.

This implements the user requirement: "run unattended without oversight and
arrive at the best possible result". Permanent failures throttle to 1
retry/day without polluting the queue, and recover automatically if polygon
later starts returning the contract.

### Goal completion detection

`phase=completed` transition happens in the tick (not the completion
callback) because completion depends on `in_flight` being empty across the
entire goal, which is naturally observed at tick reconcile time.

## Data flow

```
                   cron tick (1 min)              download_manager
                         │                              │
                         ▼                              │ on_download_complete
                ┌────────────────┐                      │ (provider, symbols)
                │ refresh cache  │◀─────────────────────┘
                │ if TTL expired │
                └───────┬────────┘
                        ▼
                ┌──────────────────────┐
                │ compute on_disk,     │
                │ in_flight,           │
                │ recently_failed sets │
                └───────┬──────────────┘
                        ▼
                ┌──────────────────────┐
                │ enqueue up to        │
                │ (CAP − in_flight)    │
                │ from eligible_pending│
                └───────┬──────────────┘
                        ▼
                ┌──────────────────────┐
                │ update goal row;     │
                │ transition phase if  │
                │ all done             │
                └──────────────────────┘
```

## Error handling

- **Coordinator restart**: `DownloadManager.recover_orphaned_downloads()`
  marks in-flight rows `failed` with `Orphaned by coordinator restart`. With
  the new failure-handling path, the next tick treats those as
  `recently_failed`, applies the 1-minute backoff (this is failure #1 for
  most), and retries automatically.
- **Stale disk cache after manual deletion**: the 60s full rescan
  reconciles. Manual deletes will be re-detected within a minute, marked
  pending, and re-enqueued.
- **Provider not in registry**: same as today — `_download_options` early-
  returns if the provider isn't registered; goal stays active and retries
  next tick when the provider becomes available.

## Testing

Unit tests in `tests/coordinator/services/test_goal_processor.py`:

- `test_download_caps_in_flight_at_two` — seed `discovered_contracts` with 10
  symbols, none on disk. Run tick once: expect 2 rows in
  `market_data_downloads`. Run again with the 2 still queued: expect no new
  rows.
- `test_completion_event_triggers_next_enqueue` — fire
  `on_download_complete` for one symbol; expect a new row enqueued (so the
  in-flight count returns to 2).
- `test_failed_symbol_is_backed_off` — insert a failed row with
  `completed_at` 30s ago; expect that symbol excluded from `eligible_pending`.
  Advance time past the backoff; expect the symbol picked.
- `test_exponential_backoff_grows_with_count` — insert N failed rows; assert
  computed delay matches `min(60 * 2**N, 86400)`.
- `test_phase_transitions_to_completed` — all symbols on disk, no in-flight:
  expect `phase=status='completed'`.
- `test_disk_cache_refreshes_at_ttl` — mock `os.listdir`, advance time;
  assert the function is called at most once per 60s window.
- `test_disk_cache_updated_incrementally_on_completion` — fire
  `on_download_complete`; assert the symbol appears in the in-memory cache
  without calling `os.listdir`.

## Implementation footprint

Touched files (estimated):

- `coordinator/services/goal_processor.py` — rewrite `_download_options`,
  add disk-cache state, wire into `on_download_complete`.
- `coordinator/main.py` — pass `goal_processor.on_download_complete` (or a
  router that fans out to all goal-like consumers) as the
  `DownloadManager`'s `on_download_complete` callback. The PortfolioTracker
  already uses this hook; we add to it rather than replace.
- `coordinator/services/download_manager.py` — expose
  `concurrency_for(provider) -> int` so the goal processor can read the cap
  without poking at `_provider_semaphores`.
- `tests/coordinator/services/test_goal_processor.py` — new test module.

No schema changes. No migration. No new tables.

## Open question

The `on_download_complete` callback currently takes
`Callable[[str, list[str]], None]`. If multiple consumers want to register
(goal processor, portfolio tracker, future), the registration should be a
list — a small refactor either now or when the second consumer appears.
Deferring: if the portfolio tracker is the only existing consumer and
doesn't share the slot, we wrap it with a fan-out function in
`coordinator/main.py` for now.
