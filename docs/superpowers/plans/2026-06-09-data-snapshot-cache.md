# Data Snapshot Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cache the response payloads of `/api/data/coverage` and `/api/data/storage-summary` in process memory, prewarmed at startup and invalidated on disk-write events, so cold and warm page loads of the Data → Available Data tab return in under 200 ms.

**Architecture:** A small `CachedSnapshot[T]` helper holds a precomputed value plus a first-ready `asyncio.Event` and a coalesced-refresh state machine. Two instances live on the `ServiceContainer` (`coverage_snapshot`, `storage_summary_snapshot`). Producer functions wrap the current route bodies verbatim and run them via `asyncio.to_thread`. Endpoints become one-liners that `await snapshot.get()` with a 30 s safety timeout. Existing disk-write hooks (`_on_download_complete`, `/api/data/delete-datasets`, scraper run completion) gain one or two `.invalidate()` calls each. The existing `_prewarm_coverage_cache` startup task is deleted; the coverage snapshot's startup refresh warms `CoverageIndex._cache` as a side effect because it iterates the same set of non-option symbols.

**Tech Stack:** Python 3.12, FastAPI, asyncio, pytest-asyncio, httpx (test client).

**Reference spec:** `docs/superpowers/specs/2026-06-09-data-snapshot-cache-design.md`

---

## File Structure

**Create:**
- `coordinator/services/cached_snapshot.py` — `CachedSnapshot[T]` helper class
- `tests/coordinator/services/test_cached_snapshot.py` — unit tests for the helper
- `tests/coordinator/test_main_lifespan.py` — integration tests for snapshot wiring at boot

**Modify:**
- `coordinator/api/dependencies.py` — declare `coverage_snapshot` and `storage_summary_snapshot` on `ServiceContainer`
- `coordinator/api/routes/data.py` — extract producer functions; switch `/coverage` and `/storage-summary` handlers to read from snapshots; invalidate in `/delete-datasets`
- `coordinator/main.py` — replace `_prewarm_coverage_cache` startup task with `_prewarm_snapshots`; invalidate snapshots in `_on_download_complete`
- `coordinator/services/scraper_registry.py` — invalidate `storage_summary_snapshot` on successful scraper run
- `tests/coordinator/test_data_api.py` — add endpoint-level tests for the new snapshot-backed handlers

---

## Task 1: Build `CachedSnapshot[T]` helper (TDD)

**Files:**
- Create: `coordinator/services/cached_snapshot.py`
- Test: `tests/coordinator/services/test_cached_snapshot.py`

- [ ] **Step 1.1: Write the failing test file**

Create `tests/coordinator/services/test_cached_snapshot.py`:

```python
import asyncio
import pytest

from coordinator.services.cached_snapshot import CachedSnapshot


@pytest.mark.asyncio
async def test_get_blocks_until_first_refresh():
    """get() must wait for the first successful refresh before returning."""
    async def producer() -> str:
        return "value-1"

    snap = CachedSnapshot[str]("test", producer)

    # Before refresh: get() hangs.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(snap.get(), timeout=0.05)

    # After refresh: get() returns immediately.
    await snap.refresh_now()
    assert await asyncio.wait_for(snap.get(), timeout=0.5) == "value-1"


@pytest.mark.asyncio
async def test_get_returns_latest_after_refresh():
    """Successive refreshes replace the cached value."""
    counter = {"n": 0}

    async def producer() -> int:
        counter["n"] += 1
        return counter["n"]

    snap = CachedSnapshot[int]("test", producer)
    await snap.refresh_now()
    assert await snap.get() == 1

    await snap.refresh_now()
    assert await snap.get() == 2


@pytest.mark.asyncio
async def test_stale_while_revalidate():
    """While a slow refresh is in flight, get() returns the previous value."""
    release = asyncio.Event()
    call_count = {"n": 0}

    async def producer() -> int:
        call_count["n"] += 1
        if call_count["n"] >= 2:
            await release.wait()  # block subsequent refreshes
        return call_count["n"]

    snap = CachedSnapshot[int]("test", producer)
    await snap.refresh_now()  # value = 1
    assert await snap.get() == 1

    # Trigger an invalidate that will block in the producer.
    snap.invalidate()

    # Give the drainer a chance to start the producer.
    await asyncio.sleep(0.01)

    # Reader still sees the OLD value.
    assert await asyncio.wait_for(snap.get(), timeout=0.05) == 1

    # Let the refresh finish and verify the new value.
    release.set()
    if snap._refresh_task is not None:
        await snap._refresh_task
    assert await snap.get() == 2


@pytest.mark.asyncio
async def test_invalidate_coalesces_concurrent_calls():
    """N rapid invalidations during one in-flight refresh produce at most
    one queued refresh after it, not N."""
    call_count = {"n": 0}
    started = asyncio.Event()
    release = asyncio.Event()

    async def producer() -> int:
        call_count["n"] += 1
        started.set()
        await release.wait()
        return call_count["n"]

    snap = CachedSnapshot[int]("test", producer)

    # Start the first refresh; producer blocks on `release`.
    snap.invalidate()
    await started.wait()
    started.clear()

    # Fire 10 rapid invalidations while the producer is blocked.
    for _ in range(10):
        snap.invalidate()

    # Release the producer; release stays set so the next call returns instantly.
    release.set()

    # The drainer loops once more (queued by the burst above).
    await started.wait()

    # Wait for the drainer task to fully exit.
    assert snap._refresh_task is not None
    await snap._refresh_task

    # Exactly two producer invocations: the initial + one coalesced.
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_producer_exception_keeps_old_value():
    """If a refresh raises, the previously-cached value remains readable."""
    call_count = {"n": 0}

    async def producer() -> str:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("transient")
        return f"v{call_count['n']}"

    snap = CachedSnapshot[str]("test", producer)
    await snap.refresh_now()  # v1
    assert await snap.get() == "v1"

    # Trigger a refresh that will raise.
    snap.invalidate()
    assert snap._refresh_task is not None
    await snap._refresh_task

    # Old value is still served.
    assert await snap.get() == "v1"


@pytest.mark.asyncio
async def test_drainer_exits_after_failure_and_resumes_on_invalidate():
    """Failed drainer exits; the next invalidate() starts a fresh drainer."""
    call_count = {"n": 0}
    should_fail = {"yes": True}

    async def producer() -> str:
        call_count["n"] += 1
        if should_fail["yes"]:
            raise RuntimeError("boom")
        return f"v{call_count['n']}"

    snap = CachedSnapshot[str]("test", producer)
    should_fail["yes"] = False
    await snap.refresh_now()  # v1
    should_fail["yes"] = True

    snap.invalidate()
    assert snap._refresh_task is not None
    await snap._refresh_task  # drainer exits after the failure

    # Fix the producer and try again.
    should_fail["yes"] = False
    snap.invalidate()
    assert snap._refresh_task is not None
    await snap._refresh_task

    assert await snap.get() == "v3"


@pytest.mark.asyncio
async def test_first_refresh_failure_blocks_readers():
    """If the first-ever refresh fails, readers stay blocked (no _ready set).
    Documents the boot-time hang semantics that the route-level wait_for mitigates."""
    async def failing_producer() -> str:
        raise RuntimeError("boom")

    snap = CachedSnapshot[str]("test", failing_producer)

    with pytest.raises(RuntimeError):
        await snap.refresh_now()

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(snap.get(), timeout=0.05)
```

- [ ] **Step 1.2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/coordinator/services/test_cached_snapshot.py -v`
Expected: 7 errors, all `ModuleNotFoundError: No module named 'coordinator.services.cached_snapshot'`

- [ ] **Step 1.3: Implement the `CachedSnapshot` class**

Create `coordinator/services/cached_snapshot.py`:

```python
"""In-memory snapshot cache with stale-while-revalidate semantics.

A CachedSnapshot wraps an async producer that computes some response payload.
Readers call get() and receive the most recently computed value once the first
refresh has succeeded; subsequent invalidations refresh in the background
without blocking readers. Repeated invalidations during a single in-flight
refresh are coalesced to at most one queued follow-up refresh.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Generic, Optional, TypeVar, cast

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CachedSnapshot(Generic[T]):
    def __init__(self, name: str, producer: Callable[[], Awaitable[T]]) -> None:
        self._name = name
        self._producer = producer
        self._value: Optional[T] = None
        self._ready = asyncio.Event()
        self._refresh_pending = False
        self._refresh_task: Optional[asyncio.Task[None]] = None

    async def get(self) -> T:
        """Return the latest cached value, blocking only on the first-ever refresh."""
        await self._ready.wait()
        return cast(T, self._value)

    async def refresh_now(self) -> None:
        """Run the producer once, awaited by the caller. Raises on producer failure."""
        start = time.perf_counter()
        value = await self._producer()
        self._value = value
        self._ready.set()
        logger.info(
            "snapshot %s refreshed in %.2fs", self._name, time.perf_counter() - start
        )

    def invalidate(self) -> None:
        """Mark stale and ensure exactly one drainer task is running. Non-blocking."""
        self._refresh_pending = True
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        while self._refresh_pending:
            self._refresh_pending = False  # consume flag BEFORE producing
            start = time.perf_counter()
            try:
                value = await self._producer()
            except Exception:
                logger.exception("snapshot %s refresh failed", self._name)
                logger.info(
                    "snapshot %s drainer exiting after failure", self._name
                )
                return
            self._value = value
            self._ready.set()
            logger.info(
                "snapshot %s refreshed in %.2fs",
                self._name,
                time.perf_counter() - start,
            )
```

- [ ] **Step 1.4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/coordinator/services/test_cached_snapshot.py -v`
Expected: `7 passed`

- [ ] **Step 1.5: Commit**

```bash
git add coordinator/services/cached_snapshot.py tests/coordinator/services/test_cached_snapshot.py
git commit -m "$(cat <<'EOF'
feat(coordinator): add CachedSnapshot[T] helper

Stale-while-revalidate snapshot cache for hot read paths. Readers block
only on the first-ever refresh; subsequent invalidations refresh in the
background. Repeated invalidations during a single in-flight refresh
are coalesced to at most one queued follow-up.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add snapshot fields to `ServiceContainer`

**Files:**
- Modify: `coordinator/api/dependencies.py:18,22-43`

- [ ] **Step 2.1: Add the TYPE_CHECKING import and two new container fields**

In `coordinator/api/dependencies.py`, modify the `TYPE_CHECKING` block to add the snapshot import (after line 18):

```python
if TYPE_CHECKING:
    from coordinator.services.live_feed_manager import LiveFeedManager
    from coordinator.services.live_feed_aggregator import LiveFeedAggregator
    from coordinator.services.backtest_runner import BacktestRunner
    from coordinator.services.data_service import DataService
    from coordinator.services.live_sample_sink import LiveSampleSink
    from coordinator.services.live_finalizer import LiveFinalizer
    from coordinator.services.tick_scheduler import TickScheduler
    from coordinator.services.lifecycle import LifecycleService
    from coordinator.services.coverage_index import CoverageIndex
    from coordinator.services.account_lifecycle import AccountLifecycleService
    from coordinator.services.cached_snapshot import CachedSnapshot
```

In the same file, after the existing `coverage_index` attribute (after line 42), add two new attributes:

```python
        self.coverage_index: Optional["CoverageIndex"] = None
        self.coverage_snapshot: Optional["CachedSnapshot[dict]"] = None
        self.storage_summary_snapshot: Optional["CachedSnapshot[dict]"] = None
        self.account_lifecycle: Optional["AccountLifecycleService"] = None
```

- [ ] **Step 2.2: Verify no tests broken by the change**

Run: `.venv/bin/python -m pytest tests/coordinator/test_data_api.py tests/coordinator/test_data_service.py -q`
Expected: existing tests pass; no `AttributeError`.

- [ ] **Step 2.3: Commit**

```bash
git add coordinator/api/dependencies.py
git commit -m "$(cat <<'EOF'
feat(coordinator): declare coverage and storage-summary snapshot slots on ServiceContainer

Forward-typed Optional slots wired but not yet populated. Population happens
in the lifespan in a subsequent commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Extract `/storage-summary` body into a producer function

**Files:**
- Modify: `coordinator/api/routes/data.py:216-266`

- [ ] **Step 3.1: Extract `_build_storage_summary_payload` above the route**

Replace the current `storage_summary` handler (lines 216-266) with the following — the route body is moved into a module-level helper that takes the `DataService` explicitly. The route keeps the same shape and behavior for this task:

```python
def _build_storage_summary_payload(svc: DataService) -> dict:
    """Compute the /api/data/storage-summary response.

    Pure function of disk state. Runs under asyncio.to_thread because it walks
    data/market/ and data/custom/ (~58k files).
    """
    import os

    market_dir = svc._market_dir
    custom_dir = svc._custom_dir

    def walk_with_attribution(root: str) -> tuple[int, dict[str, int]]:
        """Single pass over the tree: total bytes + per-top-level-subdir bytes."""
        total = 0
        per_top: dict[str, int] = {}
        if not os.path.isdir(root):
            return 0, {}
        root_abs = os.path.abspath(root)
        for dirpath, _, filenames in os.walk(root):
            rel = os.path.relpath(os.path.abspath(dirpath), root_abs)
            top = rel.split(os.sep, 1)[0] if rel and rel != "." else None
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    sz = os.path.getsize(fp)
                except OSError:
                    continue
                total += sz
                if top:
                    per_top[top] = per_top.get(top, 0) + sz
        return total, per_top

    market_bytes, by_provider = walk_with_attribution(market_dir)
    custom_bytes, _ = walk_with_attribution(custom_dir)
    total_bytes = market_bytes + custom_bytes

    def fmt(b: int) -> str:
        if b >= 1 << 30:
            return f"{b / (1 << 30):.1f} GB"
        if b >= 1 << 20:
            return f"{b / (1 << 20):.1f} MB"
        return f"{b / (1 << 10):.1f} KB"

    return {
        "market_data_path": os.path.abspath(market_dir),
        "custom_data_path": os.path.abspath(custom_dir),
        "total_bytes": total_bytes,
        "total_formatted": fmt(total_bytes),
        "market_bytes": market_bytes,
        "market_formatted": fmt(market_bytes),
        "custom_bytes": custom_bytes,
        "custom_formatted": fmt(custom_bytes),
        "by_provider": {
            k: {"bytes": v, "formatted": fmt(v)}
            for k, v in sorted(by_provider.items(), key=lambda x: -x[1])
        },
    }


@router.get("/storage-summary")
async def storage_summary():
    """Return data storage path and total disk usage."""
    return await asyncio.to_thread(_build_storage_summary_payload, get_data_service())
```

- [ ] **Step 3.2: Run the existing data-api tests to verify no regression**

Run: `.venv/bin/python -m pytest tests/coordinator/test_data_api.py -q`
Expected: all existing tests pass; response shape unchanged.

- [ ] **Step 3.3: Commit**

```bash
git add coordinator/api/routes/data.py
git commit -m "$(cat <<'EOF'
refactor(data): extract /storage-summary body into producer function

Pure refactor; no behavior change. Prepares the route to be backed by a
CachedSnapshot in a subsequent commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Extract `/coverage` body into a producer function

**Files:**
- Modify: `coordinator/api/routes/data.py:470-530`

- [ ] **Step 4.1: Extract `_build_coverage_payload` above the route**

Replace the current `get_coverage` handler (lines 470-530) with the following. The route body becomes a module-level helper that takes `DataService` and `CoverageIndex` explicitly:

```python
def _build_coverage_payload(
    svc: DataService, coverage: Optional["CoverageIndex"]
) -> dict:
    """Compute the /api/data/coverage response.

    Walks the parquet directory, deduplicates per (provider, symbol), parses
    OCC option symbols, groups them by underlying, and consults CoverageIndex
    for date ranges. Pure function of disk state + CoverageIndex._cache.
    """
    from coordinator.services.chain_builder import parse_occ_symbol

    available = svc.list_available_market_data()

    seen: dict[str, dict] = {}
    options_groups: dict[str, dict] = {}

    for item in available:
        provider = item["provider"]
        symbol = item["symbol"]

        parsed = parse_occ_symbol(symbol)
        if parsed:
            group_key = f"{provider}/{parsed['underlying']}"
            if group_key not in options_groups:
                options_groups[group_key] = {
                    "provider": provider,
                    "symbol": parsed["underlying"],
                    "contracts": [],
                    "expirations": set(),
                }
            options_groups[group_key]["contracts"].append(symbol)
            options_groups[group_key]["expirations"].add(parsed["expiration"])
            continue

        key = f"{provider}/{symbol}"
        if key not in seen:
            ranges = coverage.get_ranges(provider, symbol) if coverage else []
            seen[key] = {
                "provider": provider,
                "symbol": symbol,
                "ranges": [{"start": str(s), "end": str(e)} for s, e in ranges],
                "timeframes_on_disk": [],
            }
        seen[key]["timeframes_on_disk"].append(item["timeframe"])

    for group_key, group in options_groups.items():
        exps = sorted(group["expirations"])
        seen[group_key + "/options"] = {
            "provider": group["provider"],
            "symbol": group["symbol"],
            "ranges": [{"start": exps[0], "end": exps[-1]}] if exps else [],
            "timeframes_on_disk": ["options"],
            "option_contracts": len(group["contracts"]),
            "option_expirations": len(exps),
        }

    grouped: dict[str, list] = {}
    for v in seen.values():
        grouped.setdefault(v["provider"], []).append(v)
    return {"providers": grouped}


@router.get("/coverage")
async def get_coverage():
    """Return coverage ranges for all assets on disk, grouped by provider."""
    svc = get_data_service()
    coverage = get_coverage_index()
    return await asyncio.to_thread(_build_coverage_payload, svc, coverage)
```

Add a TYPE_CHECKING import at the top of `data.py` (find the existing import block near line 11; add this conditionally):

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coordinator.services.coverage_index import CoverageIndex
```

Place this near the existing `from typing import Optional` line (around line 3). If a `TYPE_CHECKING` block already exists in the file, add `CoverageIndex` to it instead.

- [ ] **Step 4.2: Run the existing tests to verify no regression**

Run: `.venv/bin/python -m pytest tests/coordinator/test_data_api.py tests/coordinator/test_download_api.py -q`
Expected: all tests pass; response shape unchanged.

- [ ] **Step 4.3: Commit**

```bash
git add coordinator/api/routes/data.py
git commit -m "$(cat <<'EOF'
refactor(data): extract /coverage body into producer function

Pure refactor; no behavior change. Producer runs on a worker thread via
asyncio.to_thread so it no longer blocks the event loop. Prepares the
route to be backed by a CachedSnapshot in a subsequent commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Wire snapshots into lifespan startup

**Files:**
- Modify: `coordinator/main.py:437-473`

- [ ] **Step 5.1: Replace `_prewarm_coverage_cache` with snapshot construction + `_prewarm_snapshots`**

In `coordinator/main.py`, replace the block from line 437 through line 473 (the `CoverageIndex` construction plus the entire `_prewarm_coverage_cache` definition and its `asyncio.create_task` call) with the following:

```python
        from coordinator.services.coverage_index import CoverageIndex
        from coordinator.services.cached_snapshot import CachedSnapshot
        from coordinator.api.routes.data import (
            _build_coverage_payload,
            _build_storage_summary_payload,
        )

        coverage_index = CoverageIndex(data_svc)
        container.coverage_index = coverage_index

        # Two snapshot caches back the slow read endpoints. The coverage
        # snapshot's first refresh also warms CoverageIndex._cache as a side
        # effect — it iterates the same (provider, symbol) pairs.
        async def _coverage_producer() -> dict:
            return await asyncio.to_thread(
                _build_coverage_payload, data_svc, coverage_index
            )

        async def _storage_summary_producer() -> dict:
            return await asyncio.to_thread(
                _build_storage_summary_payload, data_svc
            )

        container.coverage_snapshot = CachedSnapshot("coverage", _coverage_producer)
        container.storage_summary_snapshot = CachedSnapshot(
            "storage_summary", _storage_summary_producer
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

- [ ] **Step 5.2: Run the existing test suite to verify the lifespan still boots**

Run: `.venv/bin/python -m pytest tests/coordinator/test_data_api.py tests/coordinator/test_download_api.py -q`
Expected: all existing tests pass; the `client` fixture (which boots the full lifespan) does not error.

- [ ] **Step 5.3: Commit**

```bash
git add coordinator/main.py
git commit -m "$(cat <<'EOF'
feat(coordinator): wire coverage and storage-summary snapshots in lifespan

Replaces _prewarm_coverage_cache. The coverage snapshot's first refresh
warms CoverageIndex._cache as a side effect because it iterates the same
(provider, symbol) pairs.

Routes still call the producers directly until Task 6 switches them to
read from the snapshots.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Switch routes to read from snapshots (+ 503 timeout)

**Files:**
- Modify: `coordinator/api/routes/data.py` — `/coverage` and `/storage-summary` handlers
- Modify: `tests/coordinator/test_data_api.py` — add endpoint-level tests

- [ ] **Step 6.1: Write the failing endpoint tests**

Append to `tests/coordinator/test_data_api.py`:

```python
import asyncio as _asyncio
from coordinator.services.cached_snapshot import CachedSnapshot


@pytest.mark.asyncio
async def test_coverage_returns_snapshot(client):
    """The /coverage route returns whatever the coverage_snapshot holds."""
    from coordinator.api.dependencies import get_container

    container = get_container()
    fake_payload = {"providers": {"polygon": [{"symbol": "AAPL"}]}}

    async def fake_producer() -> dict:
        return fake_payload

    container.coverage_snapshot = CachedSnapshot("coverage", fake_producer)
    await container.coverage_snapshot.refresh_now()

    resp = await client.get("/api/data/coverage")
    assert resp.status_code == 200
    assert resp.json() == fake_payload


@pytest.mark.asyncio
async def test_storage_summary_returns_snapshot(client):
    """The /storage-summary route returns whatever the storage_summary_snapshot holds."""
    from coordinator.api.dependencies import get_container

    container = get_container()
    fake_payload = {
        "market_data_path": "/x",
        "custom_data_path": "/y",
        "total_bytes": 0,
        "total_formatted": "0.0 KB",
        "market_bytes": 0,
        "market_formatted": "0.0 KB",
        "custom_bytes": 0,
        "custom_formatted": "0.0 KB",
        "by_provider": {},
    }

    async def fake_producer() -> dict:
        return fake_payload

    container.storage_summary_snapshot = CachedSnapshot(
        "storage_summary", fake_producer
    )
    await container.storage_summary_snapshot.refresh_now()

    resp = await client.get("/api/data/storage-summary")
    assert resp.status_code == 200
    assert resp.json() == fake_payload


@pytest.mark.asyncio
async def test_coverage_503_when_snapshot_unready(client, monkeypatch):
    """If the snapshot never becomes ready within the timeout, return 503."""
    import coordinator.api.routes.data as data_module
    from coordinator.api.dependencies import get_container

    container = get_container()

    # A snapshot whose producer never resolves.
    async def hung_producer() -> dict:
        await _asyncio.Event().wait()  # forever
        return {}

    container.coverage_snapshot = CachedSnapshot("coverage", hung_producer)
    # Don't call refresh_now — _ready will never be set.

    # Shorten the wait so the test runs fast.
    monkeypatch.setattr(data_module, "_SNAPSHOT_READ_TIMEOUT_S", 0.05)

    resp = await client.get("/api/data/coverage")
    assert resp.status_code == 503
```

- [ ] **Step 6.2: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/coordinator/test_data_api.py::test_coverage_returns_snapshot tests/coordinator/test_data_api.py::test_storage_summary_returns_snapshot tests/coordinator/test_data_api.py::test_coverage_503_when_snapshot_unready -v`
Expected: 3 failures — the routes still return the computed payload directly, ignoring `coverage_snapshot`.

- [ ] **Step 6.3: Switch the route handlers to read from the snapshots**

In `coordinator/api/routes/data.py`, find the two route handlers updated in Tasks 3 and 4. Replace them with the snapshot-backed versions. Also add the module-level constant near the top of the file (after the router declaration around line 18):

```python
_SNAPSHOT_READ_TIMEOUT_S = 30.0
```

Replace the `/storage-summary` handler with:

```python
@router.get("/storage-summary")
async def storage_summary():
    """Return data storage path and total disk usage."""
    container = get_container()
    snap = container.storage_summary_snapshot
    if snap is None:
        raise HTTPException(503, "Storage summary snapshot not initialized")
    try:
        return await asyncio.wait_for(snap.get(), timeout=_SNAPSHOT_READ_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise HTTPException(503, "Storage summary snapshot not ready")
```

Replace the `/coverage` handler with:

```python
@router.get("/coverage")
async def get_coverage():
    """Return coverage ranges for all assets on disk, grouped by provider."""
    container = get_container()
    snap = container.coverage_snapshot
    if snap is None:
        raise HTTPException(503, "Coverage snapshot not initialized")
    try:
        return await asyncio.wait_for(snap.get(), timeout=_SNAPSHOT_READ_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise HTTPException(503, "Coverage snapshot not ready")
```

You'll need to add `from coordinator.api.dependencies import get_container` to the imports near the top of `data.py` if it isn't already present.

- [ ] **Step 6.4: Run the test suite**

Run: `.venv/bin/python -m pytest tests/coordinator/test_data_api.py tests/coordinator/test_download_api.py -q`
Expected: all tests pass — including the three new ones added in Step 6.1.

- [ ] **Step 6.5: Commit**

```bash
git add coordinator/api/routes/data.py tests/coordinator/test_data_api.py
git commit -m "$(cat <<'EOF'
feat(data): route /coverage and /storage-summary through snapshot cache

Both handlers now await container.<name>_snapshot.get() with a 30s safety
timeout. If the snapshot is unset or doesn't become ready within the
timeout, the handler returns 503 instead of hanging the dashboard.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Wire invalidation on disk-write events

**Files:**
- Modify: `coordinator/main.py:475-498` — `_on_download_complete`
- Modify: `coordinator/api/routes/data.py:539-550` — `/delete-datasets`
- Modify: `coordinator/services/scraper_registry.py:344-349` — scraper success path

- [ ] **Step 7.1: Invalidate both snapshots when a download completes**

In `coordinator/main.py`, find `_on_download_complete` (around line 475). It currently invalidates per-symbol `CoverageIndex` entries and fans out to the `goal_processor`. Add snapshot invalidation immediately after the per-symbol invalidation loop. The updated function looks like:

```python
        def _on_download_complete(
            provider: str,
            symbols: list[str],
            status: str | None = None,
            error_message: str | None = None,
        ) -> None:
            for sym in symbols:
                coverage_index.invalidate(provider, sym)

            cov_snap = getattr(container, "coverage_snapshot", None)
            if cov_snap is not None:
                cov_snap.invalidate()
            store_snap = getattr(container, "storage_summary_snapshot", None)
            if store_snap is not None:
                store_snap.invalidate()

            # Fan out to the goal processor (if constructed) so it can top up
            # its in-flight queue without waiting for the next cron tick, and
            # record terminal "no data" failures persistently on the goal.
            gp = getattr(container, "goal_processor", None)
            if gp is not None:
                try:
                    asyncio.create_task(
                        gp.on_download_complete(
                            provider, symbols, status=status, error_message=error_message,
                        )
                    )
                except RuntimeError:
                    # No running loop (e.g. during unit-test teardown) — skip.
                    pass
```

- [ ] **Step 7.2: Invalidate both snapshots in `/delete-datasets`**

In `coordinator/api/routes/data.py`, find the `delete_datasets` handler (currently around lines 539-550). Update it to invalidate the snapshots once if any deletions occurred:

```python
@router.post("/delete-datasets")
async def delete_datasets(body: list[DeleteDatasetRequest]):
    """Delete one or more market data parquet files."""
    svc = get_data_service()
    coverage = get_coverage_index()
    container = get_container()
    deleted = 0
    for item in body:
        if svc.delete_market_data(item.provider, item.symbol, item.timeframe):
            deleted += 1
            if coverage:
                coverage.invalidate(item.provider, item.symbol)
    if deleted > 0:
        cov_snap = getattr(container, "coverage_snapshot", None)
        if cov_snap is not None:
            cov_snap.invalidate()
        store_snap = getattr(container, "storage_summary_snapshot", None)
        if store_snap is not None:
            store_snap.invalidate()
    return {"deleted": deleted}
```

- [ ] **Step 7.3: Invalidate `storage_summary_snapshot` after a successful scraper run**

In `coordinator/services/scraper_registry.py`, find the success branch of `ScraperRegistry.run` (the `if result.success:` block around line 344). After the existing `await self._upsert_data_source(record, result)` call, add a snapshot invalidation. The scraper writes to `data/custom/`, so only `storage_summary_snapshot` needs invalidation.

The updated success branch should look like:

```python
        if result.success:
            record.last_status = "ok"
            record.last_output_path = result.output_path
            record.last_error = None
            logger.info("scraper %s wrote %s", name, result.output_path)
            await self._upsert_data_source(record, result)

            from coordinator.api.dependencies import get_container
            try:
                container = get_container()
                snap = getattr(container, "storage_summary_snapshot", None)
                if snap is not None:
                    snap.invalidate()
            except AssertionError:
                # Container not initialized (e.g. CLI / test contexts) — skip.
                pass
```

The `AssertionError` catch matches the existing `get_container()` contract (`assert _container is not None`) — used by other callers in the codebase that may run outside a lifespan.

- [ ] **Step 7.4: Run the full backend test suite affected by these changes**

Run: `.venv/bin/python -m pytest tests/coordinator/test_data_api.py tests/coordinator/test_download_api.py tests/coordinator/services/test_cached_snapshot.py -q`
Expected: all tests pass.

- [ ] **Step 7.5: Commit**

```bash
git add coordinator/main.py coordinator/api/routes/data.py coordinator/services/scraper_registry.py
git commit -m "$(cat <<'EOF'
feat(data): invalidate snapshots on disk-write events

Three call sites:
- _on_download_complete: invalidate coverage + storage-summary
- /api/data/delete-datasets: invalidate both if anything was deleted
- ScraperRegistry.run success path: invalidate storage-summary only
  (scrapers write to data/custom/, not market data)

All call sites guard with getattr/AssertionError to handle early startup
and out-of-lifespan contexts (tests, CLI).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Lifespan integration tests

**Files:**
- Create: `tests/coordinator/test_main_lifespan.py`

- [ ] **Step 8.1: Write the failing lifespan test**

Create `tests/coordinator/test_main_lifespan.py`:

```python
"""Integration tests for snapshot wiring at coordinator startup."""
import pytest
from unittest.mock import MagicMock

from coordinator.api.dependencies import get_container
from coordinator.services.cached_snapshot import CachedSnapshot


@pytest.mark.asyncio
async def test_snapshots_attached_to_container_at_startup(test_app):
    """After lifespan startup, both data snapshots are CachedSnapshot instances."""
    container = get_container()
    assert isinstance(container.coverage_snapshot, CachedSnapshot)
    assert isinstance(container.storage_summary_snapshot, CachedSnapshot)


@pytest.mark.asyncio
async def test_download_complete_invalidates_both_snapshots(test_app):
    """Firing _on_download_complete invalidates both data snapshots."""
    container = get_container()

    # Replace the live snapshots with mocks so we can observe .invalidate() calls.
    cov_mock = MagicMock(spec=CachedSnapshot)
    store_mock = MagicMock(spec=CachedSnapshot)
    container.coverage_snapshot = cov_mock
    container.storage_summary_snapshot = store_mock

    # The download_manager lives on app.state (set in lifespan at main.py:365).
    dm = test_app.state.download_manager
    listeners = dm._completion_listeners
    assert listeners, "expected at least one completion listener registered"

    # Fire each listener with a plausible payload. The lifespan registers one
    # listener (_on_download_complete) that fans out to the snapshots + goal
    # processor.
    for cb in listeners:
        cb("polygon", ["AAPL"], status="completed", error_message=None)

    cov_mock.invalidate.assert_called()
    store_mock.invalidate.assert_called()
```

- [ ] **Step 8.2: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/coordinator/test_main_lifespan.py -v`
Expected: 2 passed.

- [ ] **Step 8.3: Commit**

```bash
git add tests/coordinator/test_main_lifespan.py
git commit -m "$(cat <<'EOF'
test(coordinator): verify snapshot wiring at lifespan startup

Two tests:
- Snapshots are CachedSnapshot instances after startup.
- Firing _on_download_complete invalidates both snapshots.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Full-suite verification

- [ ] **Step 9.1: Run the entire affected test suite**

Run: `.venv/bin/python -m pytest tests/coordinator/services/test_cached_snapshot.py tests/coordinator/test_main_lifespan.py tests/coordinator/test_data_api.py tests/coordinator/test_download_api.py tests/coordinator/test_data_service.py -v`
Expected: all tests pass.

- [ ] **Step 9.2: Smoke-test the end-to-end behavior**

In a separate shell:

```bash
pkill -f "uvicorn.*coordinator" 2>/dev/null
sleep 2
nohup .venv/bin/python -m uvicorn --factory coordinator.main:create_app \
    --host 0.0.0.0 --port 8000 --log-level info --access-log \
    > /tmp/coord_smoke.log 2>&1 < /dev/null &
disown
sleep 10
grep "snapshot.*refreshed\|Data snapshot prewarm" /tmp/coord_smoke.log
```

Expected log lines (timings will vary):

```
snapshot coverage refreshed in 1.05s
snapshot storage_summary refreshed in 1.20s
Data snapshot prewarm: complete
```

Then probe each endpoint twice. The second call of each should be <50 ms because the snapshot is warm:

```bash
for ep in coverage storage-summary; do
    for n in 1 2; do
        curl -s -o /dev/null -w "[$ep #$n] %{time_total}s size=%{size_download}\n" \
            http://localhost:8000/api/data/$ep
    done
done
```

Expected: both `#2` calls return under 100 ms; `#1` may include initial connect overhead but should also be under 100 ms once prewarm has finished.

- [ ] **Step 9.3: Rebuild the dashboard so the coordinator serves the latest bundle**

```bash
cd /home/jkern/dev/quilt-trader/dashboard
npm run build 2>&1 | tail -5
cd /home/jkern/dev/quilt-trader
```

Expected: `✓ built in <n>s`, no TypeScript errors.

- [ ] **Step 9.4: Stop the smoke-test coordinator (caller resumes their normal dev setup)**

```bash
pkill -f "uvicorn.*coordinator" 2>/dev/null
```

No commit for this task — verification only.

---

## Notes for the implementing engineer

1. **Don't optimize the prewarm in this plan.** The 12-second startup prewarm is documented in the backlog (`docs/superpowers/backlog.md` under "Data acquisition") as a separate follow-up. The snapshot cache makes warm reads fast; making cold-restart reads fast requires column projection + parallel scans in `CoverageIndex._scan`, which is intentionally not part of this plan.

2. **`get_container()` raises `AssertionError` if called outside a lifespan.** Existing call sites (such as the scraper registry's success path in Task 7) need a `try/except AssertionError` guard if they may run in test or CLI contexts. The download-complete hook is wired only at lifespan-startup so it doesn't need the guard, but using `getattr(container, "coverage_snapshot", None)` is still the safest pattern for the early-startup race.

3. **The producers run on a worker thread via `asyncio.to_thread`** (Task 3, 4) so they don't block the event loop. Snapshot bookkeeping (`_refresh_pending`, `_refresh_task`) stays on the loop thread and needs no lock.

4. **Tests use the `test_app` fixture from `tests/coordinator/conftest.py`** which boots the full app lifespan. This means the real snapshots are populated by the time tests run — Task 6 and Task 8 use `monkeypatch` or direct `container.coverage_snapshot = ...` reassignment to swap them out.

5. **The `_SNAPSHOT_READ_TIMEOUT_S` module-level constant** in `data.py` (Task 6) exists specifically so tests can lower it via `monkeypatch.setattr`. Don't inline the value at the call site.
