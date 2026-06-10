# Data Snapshot Cache — Snappy Reads for `/coverage` and `/storage-summary`

**Status:** Design approved 2026-06-09. Pending implementation plan.

**Motivation:** The Data → Available Data tab took 10–15 seconds to load on a cold first hit. Investigation identified two compounding causes. The primary one — `/api/data/available` returning a 9 MB list of every parquet file (which the dashboard consumed solely for an `isLoading` flag) — was removed in commit `e091be7`. With that gone, total page load drops to ~2.5 s on a cold hit, dominated by:

- `/api/data/storage-summary` — 1.0–1.3 s every call, walks `data/market/` and `data/custom/` (~58k files), no cache at all.
- `/api/data/coverage` — 1.0 s first call, walks the parquet directory tree, parses OCC option symbols, groups by underlying, and consults `CoverageIndex.get_ranges()` per non-option symbol. The underlying `CoverageIndex._cache` is process-lifetime and warm after startup, but the response *shape* is rebuilt per request, and the iteration runs synchronously in the async handler (blocks the event loop).

Both endpoints fire on every fresh page load and on every navigation that exits the React Query `staleTime: 30 s` window. The goal is page loads consistently in the low hundreds of milliseconds, both for the first cold hit after a coordinator restart and for ongoing re-navigation during a long-running session.

A small in-memory snapshot cache on the coordinator achieves this without introducing a new long-running service or polling loop. The existing event-driven hook (`_on_download_complete`) plus two new invalidation call sites is sufficient because every disk-write path in the codebase is enumerable.

---

## 1. Architecture

A single helper class `CachedSnapshot[T]` lives in `coordinator/services/cached_snapshot.py`. Two instances are constructed at lifespan startup and attached to the service container:

```
container.coverage_snapshot         : CachedSnapshot[dict]
container.storage_summary_snapshot  : CachedSnapshot[dict]
```

Each snapshot wraps a *producer* — an async callable that builds the full JSON-ready response payload for the corresponding endpoint. The endpoints become one-liners:

```python
@router.get("/coverage")
async def get_coverage():
    return await get_container().coverage_snapshot.get()
```

Three lifecycle events drive each snapshot:

| Event | Trigger | Effect |
|---|---|---|
| **Startup** | `lifespan` schedules `snapshot.refresh_now()` for each snapshot once | First-ever value populated; readers wait on the snapshot's `ready` event until done. |
| **Invalidation** | Disk-write code paths call `snapshot.invalidate()` | Schedules a background refresh; readers continue to get the *old* value until refresh completes (stale-while-revalidate). |
| **Read** | Route handler calls `await snapshot.get()` | Returns the cached value instantly once first ready; blocks only on the first-ever request. |

The existing `_prewarm_coverage_cache` background task (`coordinator/main.py:447`) is removed. Its job — warming `CoverageIndex._cache` — happens as a side effect of building the coverage snapshot, because the producer iterates the same set of non-option `(provider, symbol)` pairs and calls `coverage_index.get_ranges(...)` on each. The existing `_on_download_complete` hook (`main.py:475`) keeps invalidating per-symbol `CoverageIndex` entries, but additionally invalidates both snapshots.

---

## 2. `CachedSnapshot` internals

The core class is small but has one subtle bit: coalescing rapid-fire invalidations without dropping any.

```python
class CachedSnapshot(Generic[T]):
    def __init__(self, name: str, producer: Callable[[], Awaitable[T]]) -> None:
        self._name = name
        self._producer = producer
        self._value: T | None = None
        self._ready = asyncio.Event()
        self._refresh_pending = False
        self._refresh_task: asyncio.Task[None] | None = None

    async def get(self) -> T:
        await self._ready.wait()
        return cast(T, self._value)

    async def refresh_now(self) -> None:
        """Run the producer once, awaited by caller. Used at startup."""
        value = await self._producer()
        self._value = value
        self._ready.set()

    def invalidate(self) -> None:
        """Mark stale and ensure one drainer task is running. Non-blocking."""
        self._refresh_pending = True
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        while self._refresh_pending:
            self._refresh_pending = False  # consume flag BEFORE producing
            try:
                value = await self._producer()
                self._value = value
                self._ready.set()
            except Exception:
                logger.exception("snapshot %s refresh failed", self._name)
                # Avoid spin-loop on persistent failure; the flag stays consumed
                # so a new invalidate() is required to retry.
                return
```

### Design rationale

1. **`get()` only blocks once.** After the first successful refresh sets `_ready`, every subsequent `get()` returns instantly with the latest committed value. No lock on the read path.

2. **`invalidate()` is fire-and-forget and idempotent.** Five downloads completing in the same second produce at most *one extra refresh* after the in-flight one, not five sequential refreshes. The `_refresh_pending` flag is consumed by the drainer before the producer runs, so any `invalidate()` arriving *during* the producer call schedules one more pass.

3. **Stale-while-revalidate is the semantic.** During a refresh, readers see the *previous* value. This is the right tradeoff: coverage data is eventually-consistent (a new download appears within seconds, not milliseconds), and we never want a read to slow down behind a writer.

4. **Producer failure doesn't blank the cache.** Old value stays. Error is logged. Drainer exits — a future `invalidate()` is needed to retry. This is deliberate: if `list_available_market_data` is currently raising, we don't want a busy-loop retrying it; the next download-complete or restart will trigger a fresh attempt.

5. **`refresh_now()` is separate from `_drain()`** so startup can `await` it directly. We want the first-ever refresh to surface failures loudly during boot, not be swallowed by background-task exception handling.

6. **No thread-safety hardening.** Since the FastAPI worker runs all snapshot bookkeeping on a single event loop, the `_refresh_pending` boolean and `_refresh_task` slot are safe without locks. Producer CPU/IO work is offloaded inside the producer via `asyncio.to_thread`; the snapshot orchestration stays on the loop.

7. **No periodic-refresh safety net.** Invalidation-driven only. If a write path forgets to call `.invalidate()`, the cache stays stale until restart — that's a bug we want to surface and fix, not paper over with polling.

---

## 3. Wiring and endpoint changes

### Producer functions

Each producer returns the full response payload — identical in shape to what the current handlers return.

```python
async def _build_coverage_payload(
    data_svc: DataService, coverage_index: CoverageIndex
) -> dict:
    """Reproduces the current /api/data/coverage response. Runs the
    list_available + OCC parse + groupBy work that the route does today,
    plus CoverageIndex.get_ranges per non-option (provider, symbol)."""
    def _work() -> dict:
        # body of current get_coverage(), refactored to return the dict
        ...
    return await asyncio.to_thread(_work)

async def _build_storage_summary_payload(data_svc: DataService) -> dict:
    """Reproduces the current /api/data/storage-summary response."""
    return await asyncio.to_thread(_compute_storage_summary, data_svc)
```

Both producers are pure functions of disk state — the existing route logic is moved verbatim, just behind the snapshot.

### Startup wiring (replaces `coordinator/main.py:441–473`)

```python
container.coverage_snapshot = CachedSnapshot(
    "coverage",
    lambda: _build_coverage_payload(data_svc, coverage_index),
)
container.storage_summary_snapshot = CachedSnapshot(
    "storage_summary",
    lambda: _build_storage_summary_payload(data_svc),
)

async def _prewarm_snapshots() -> None:
    try:
        await container.coverage_snapshot.refresh_now()
        await container.storage_summary_snapshot.refresh_now()
        logger.info("Data snapshot prewarm: complete")
    except Exception:
        logger.exception("Data snapshot prewarm failed")

asyncio.create_task(_prewarm_snapshots())
```

The old `_prewarm_coverage_cache` is deleted. `CoverageIndex._cache` continues to be populated as a side effect.

### Invalidation call sites

| Location | Current behavior | Change |
|---|---|---|
| `_on_download_complete` (`main.py:475`) | `coverage_index.invalidate(provider, sym)` per symbol | Add: `container.coverage_snapshot.invalidate()` and `container.storage_summary_snapshot.invalidate()` |
| `/api/data/delete-datasets` route (`data.py:553`) | `coverage.invalidate(provider, symbol)` per item | Add same two `.invalidate()` calls after the loop |
| Scraper run completion (`coordinator/services/scraper_registry.py`) | Updates `DataSource` row | Add `container.storage_summary_snapshot.invalidate()` only (scrapers write to `data/custom/`, not market data) |

All three call sites use the existing `getattr(container, "<name>", None)` guard pattern from `_on_download_complete` to handle the early-startup race before the container attribute is wired.

### Endpoint changes

```python
@router.get("/coverage")
async def get_coverage():
    return await asyncio.wait_for(
        get_container().coverage_snapshot.get(), timeout=30.0
    )

@router.get("/storage-summary")
async def storage_summary():
    return await asyncio.wait_for(
        get_container().storage_summary_snapshot.get(), timeout=30.0
    )
```

The old route bodies move into the producer functions. The 30 s `wait_for` is a defense-in-depth measure against a broken first-ever refresh wedging the dashboard (see §4.2).

### Burst-write efficiency

A multi-symbol download (e.g., backfilling 50 tickers) fires `_on_download_complete` 50 times. With the coalescing in §2, that produces at most *two* snapshot refreshes total (the in-flight one plus one queued). Each refresh re-runs `list_available_market_data` (~900 ms warm OS cache) and `CoverageIndex.get_ranges` per symbol (instant warm). If this becomes a hot spot under heavier load, the hook can be batched upstream — but it's out of scope for this design.

---

## 4. Error handling

Three failure modes are addressed explicitly.

### 4.1 Producer raises during a refresh (cache has a prior value)

Covered by `_drain()`: log via `logger.exception`, leave `self._value` untouched, exit the drainer. Readers keep getting the old value indefinitely until the next `invalidate()` arrives. Correct semantics — a transient `pd.read_parquet` IOError shouldn't blank coverage data that's been correct for hours.

### 4.2 Producer raises on the very first refresh (cache has no value yet)

The dangerous case. `_ready` is never set; every reader on `await snapshot.get()` blocks forever.

Mitigation:

- Startup `_prewarm_snapshots()` `await`s `refresh_now()` directly and the exception is caught/logged loudly via `logger.exception`. Boot logs surface the problem.
- Route handlers wrap `get()` in `asyncio.wait_for(..., timeout=30.0)`. If the snapshot isn't ready within 30 s, the handler raises `HTTPException(503)`. The dashboard sees a clean error instead of a hung request.

### 4.3 Invalidation arrives before the container is wired

If a download completes during the lifespan's startup phase before `container.coverage_snapshot` is assigned, naive `.invalidate()` would AttributeError. All call sites use the existing pattern:

```python
snap = getattr(container, "coverage_snapshot", None)
if snap is not None:
    snap.invalidate()
```

### What's deliberately not handled

- **Automatic retries.** If a refresh fails, no timer-based retry. The next disk-write event is the trigger. Rationale: a broken producer doesn't get better by being called more often; a flaky disk will produce a new write event with its own upstream retry logic.
- **Stale-data signaling.** Readers can't tell whether the value they got is freshly refreshed or stale-while-revalidate. Don't need it — the dashboard is fine with eventually-consistent data on the order of a second.
- **Bounded memory.** Snapshots hold one `dict` each (coverage ≈ 35 KB, storage summary ≈ 700 B). No size limits or eviction.

### Logging discipline

- `logger.info("snapshot %s refreshed in %.1fs", name, elapsed)` on every successful refresh.
- `logger.exception("snapshot %s refresh failed", name)` on failure (auto-includes traceback).
- `logger.info("snapshot %s drainer exiting after failure", name)` when the drainer returns post-error, so we can see that the cache went silent.

---

## 5. Testing

Three test files, plus targeted updates to existing ones.

### 5.1 `tests/coordinator/services/test_cached_snapshot.py` (new)

Pure asyncio tests, no FastAPI. The state machine is the interesting surface area:

| Test | Assertion |
|---|---|
| `test_get_blocks_until_first_refresh` | `get()` hangs before any refresh, returns the producer's value once `refresh_now()` completes. |
| `test_get_returns_latest_after_refresh` | Successive refreshes update what `get()` returns; the old value is replaced. |
| `test_stale_while_revalidate` | While a slow refresh is in flight (`asyncio.sleep` in producer), `get()` returns the *previous* value, not blocked. |
| `test_invalidate_coalesces_concurrent_calls` | 10 rapid `invalidate()` calls during one in-flight refresh produce exactly 2 producer invocations total (running + one queued). |
| `test_producer_exception_keeps_old_value` | Set value, then make producer raise on next refresh, then `get()` returns the *prior* value, not None or an exception. |
| `test_drainer_exits_after_failure_and_resumes_on_invalidate` | After producer fails, drainer exits; next `invalidate()` starts a fresh drainer; if producer is fixed, value updates. |
| `test_first_refresh_failure_blocks_readers` | If `refresh_now()` raises, `_ready` is never set; documents the boot-time hang semantics that §4.2 mitigates. |

The coalescing test is load-bearing — it's the property that prevents a multi-symbol download burst from doing N+1 refreshes.

### 5.2 `tests/coordinator/test_data_api.py` (extend)

```python
async def test_coverage_returns_snapshot(client, monkeypatch):
    # Inject a fake snapshot returning a known payload; assert handler returns it.

async def test_coverage_503_when_snapshot_unready(client, monkeypatch):
    # Snapshot that never sets ready; assert wait_for trips to 503 within timeout.
    # Monkeypatch the route's timeout constant to 0.1s so the test runs fast.

async def test_storage_summary_returns_snapshot(client, monkeypatch):
    # Same shape for storage-summary.
```

### 5.3 `tests/coordinator/test_main_lifespan.py` (extend if it exists, otherwise new)

| Test | Assertion |
|---|---|
| `test_snapshots_attached_to_container_at_startup` | After lifespan startup, `container.coverage_snapshot` and `container.storage_summary_snapshot` are `CachedSnapshot` instances. |
| `test_download_complete_invalidates_both_snapshots` | Fire `_on_download_complete`, assert `invalidate()` was called on both (via mock). |

### What's not tested

- **Performance assertions** (e.g., "response under 200 ms"). Easy to write, flaky in CI; the design's whole point is that the endpoint returns from memory — measuring is more useful in dev than as a regression test.
- **Producer correctness.** The producer functions are moved verbatim from the existing route bodies, which are already exercised by existing tests (`test_data_service.py::test_list_available_market_data` and the data-api integration tests).

### Tests kept unchanged

- `tests/coordinator/test_data_service.py::test_list_available_market_data` — still tests the underlying disk walker.
- `tests/coordinator/api/test_ttl_cache.py` — `TTLCache` is still used by `algorithms.py` for git status caching.

---

## 6. Open decisions

These are flagged for confirmation during implementation, not blockers for the design:

1. **Scraper hook scope.** The design assumes scrapers write to `data/custom/` only. Implementation should verify this (`grep` for any scraper output path under `data/market/`). If any scraper writes to market data, the coverage snapshot needs invalidation on scraper completion too.
2. **Container access style.** The design uses `get_container().coverage_snapshot` to match the existing pattern in the route handlers. If a future refactor moves to FastAPI `Depends(...)` injection across all data routes, both endpoints should move with it.

---

## 7. Expected behavior after implementation

Assuming warm OS page cache (typical case after coordinator has been running for any length of time):

| Endpoint | Before | After |
|---|---|---|
| `/api/data/coverage` | ~1.0 s | ~5 ms (return cached dict, JSON-encode 35 KB) |
| `/api/data/storage-summary` | ~1.3 s | ~1 ms (return cached dict, JSON-encode 700 B) |

Cold first-load after coordinator restart: dominated by the prewarm task building the coverage snapshot for the first time (~12 s in the current single-threaded implementation). Requests that arrive during the prewarm wait on the snapshot's `_ready` event — same wall-clock latency as today but without the GIL-contention secondary effects. Reducing the prewarm time itself (e.g., parallelizing `_scan` across multiple threads, or projecting `["timestamp"]` only in `pd.read_parquet`) is out of scope for this design.

Total Data → Available Data tab page load, after this design ships:

- **Warm coordinator, fresh browser navigation:** ~150–250 ms (network + render + small endpoint calls).
- **Coordinator just restarted, first navigation:** dominated by prewarm — ~12 s today, addressable in a follow-up.

---

## 8. Out of scope

- Periodic refresh / safety-net timer.
- Distributed cache (no second coordinator process to coordinate with).
- Persistence across restarts (in-memory is sufficient; restart time is dominated by the producer cost regardless).
- Reducing the prewarm time itself (separate follow-up — column projection, parallel scans).
- Caching `/api/data/sources`, `/api/data/scrapers`, `/api/data/providers/timeframes`. These are already fast and not on the critical path.
