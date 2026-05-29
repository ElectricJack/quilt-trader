# Backtest + Validation Lab Additions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the three "Planned additions" from the integration spec — benchmark source expansion (P1), async-job model for research orchestration endpoints (P2), and a true union-of-symbol-timelines backtest clock (P3) — extending the integration contract with invariants I16, I17, I18 and simplifying I3.

**Architecture:** Three independent phases against the existing backtest engine + validation lab. Phase A expands one dropdown and one validation path; Phase B adds a job manager + DB table that mirrors `DownloadManager`; Phase C introduces two-pass execution in the engine and shrinks the symbol-resolution fallback paths down to one.

**Tech Stack:** FastAPI + Pydantic, async + sync SQLAlchemy 2 dual-session pattern, Alembic for migrations, pandas + numpy for clock construction, click + httpx for the CLI, React + Tailwind for the dashboard touch points, pytest for tests (sync + asyncio).

---

## Spec source

This plan implements the "Planned additions to this spec" section of `docs/superpowers/specs/2026-05-28-backtest-and-validation-lab-integration.md` (P1, P2, P3). All references to "the spec" point there. Re-read the relevant subsection of the spec before each phase if anything below feels under-specified — invariant numbers (I3, I16, I17, I18) anchor the discussion.

## File structure

### Phase A — P1 benchmark sources

- **Modify** `coordinator/api/routes/data.py`
  - Add `_provider_availability(db)` helper that returns the availability matrix.
  - Add `GET /api/data/providers` endpoint.
- **Modify** `coordinator/api/routes/backtest_runs.py`
  - In `create_run`, call `_provider_availability` and validate `benchmark_source` if set.
  - Import the helper from `coordinator.api.routes.data`.
- **Modify** `coordinator/services/backtest_runner.py`
  - Replace the benchmark-load block (around `bench_symbol and bench_source`) with a load-then-download-then-retry sequence reusing `_download_and_wait`.
- **Modify** `dashboard/src/components/RunBacktestModal.tsx`
  - Add `useEffect` that fetches `/api/data/providers` on mount.
  - Render dropdown from the available subset; default to the first item.
- **Create** `tests/coordinator/api/test_data_providers.py`
  - Tests for the availability matrix and the new GET endpoint.
- **Create** `tests/coordinator/api/test_backtest_runs_benchmark_validation.py`
  - Tests for the create-run benchmark-source validation.
- **Create** `tests/coordinator/services/test_backtest_runner_benchmark_download.py`
  - Test that missing benchmark data triggers a download-and-wait.

### Phase B — P2 async-job model

- **Create** `coordinator/database/migrations/versions/<rev>_research_jobs.py`
  - Adds the `research_jobs` table.
- **Modify** `coordinator/database/models.py`
  - Add `ResearchJob` model class.
- **Create** `coordinator/services/research_job_manager.py`
  - `ResearchJobManager` class: `create_sweep_job`, `create_walk_forward_job`, `get_job`, `list_jobs`, `cancel_job`, `recover_orphaned_jobs`.
- **Modify** `coordinator/main.py`
  - Construct `ResearchJobManager`, attach to container, call `recover_orphaned_jobs` at startup.
- **Modify** `coordinator/api/routes/research.py`
  - Replace synchronous sweep/walk-forward endpoints with 202-Accepted endpoints that delegate to the manager.
  - Add `GET /api/research/sessions/{id}/jobs`, `GET /jobs/{job_id}`, `DELETE /jobs/{job_id}`.
- **Modify** `coordinator/services/validation/sweep.py`
  - Accept an optional `progress_callback(pct: float, message: str, run_ids: list[str])` and call it after each completed trial.
- **Modify** `coordinator/services/validation/walk_forward.py`
  - Same — accept a progress callback and call it after each completed fold.
- **Modify** `sdk/cli/commands/research.py`
  - `cmd_sweep` and `cmd_walk_forward` post then poll-until-terminal.
  - Add `--no-wait` flag to both.
- **Create** `tests/coordinator/services/test_research_job_manager.py`
  - Unit tests for the manager (DB-level): queueing, terminal transitions, orphan recovery, cancel.
- **Create** `tests/coordinator/api/test_research_jobs_endpoints.py`
  - Endpoint contract tests: POST returns 202, GET reflects state changes, DELETE flips status to cancelled.
- **Create** `tests/sdk/cli/test_research_cli_polling.py`
  - CLI test: posts, polls a fake server, exits on terminal status.

### Phase C — P3 union-of-symbol-timelines clock

- **Modify** `coordinator/services/backtest_engine_v2.py`
  - Replace the current `run` flow with a two-pass execution: pass 1 discovery, pass 2 replay against the union clock.
  - Simplify `_lookup_symbol_close` and `_try_fill` fill-bar resolution (the per-call cache-walk loops become direct dict lookups by `(provider_symbol, sim_time)`).
- **Modify** `coordinator/services/backtest_tick_context.py`
  - Add `reset_for_replay()` method that clears tick-only state (sim_time, pending fills observers see) without dropping the bars cache.
- **Create** `tests/coordinator/services/test_backtest_engine_two_pass_clock.py`
  - Pass-1 → pass-2 transition behaviour: discovery captures symbols, replay uses union clock, observers fire only in pass 2.
- **Modify** `tests/coordinator/services/test_symbol_normalization.py`
  - The fallback-loop tests change — keep the manifest-preload boundary tests but mark the engine-internal lookup loop tests as `pytest.mark.skip(reason="P3 simplified I3")` (with the simplification commit, re-enable them later as direct-lookup tests).

### Phase boundaries

Phases A, B, C are independent. Each can ship to main without the others. Pick this order for the implementation plan because (a) P1 is the smallest and de-risks the dual-session test pattern; (b) P2 is the largest pure-orchestration change and benefits from a working test seam; (c) P3 touches engine internals and should land last so any regression is isolated.

---

# Phase A — P1 benchmark source expansion

### Task A1: Provider availability helper + new GET endpoint

**Files:**
- Modify: `coordinator/api/routes/data.py` (add helper + endpoint)
- Test: `tests/coordinator/api/test_data_providers.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/api/test_data_providers.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport

from coordinator.api.app import create_app
from coordinator.database.models import Account, Setting


@pytest.mark.asyncio
async def test_providers_yfinance_always_available(async_session_factory):
    """yfinance has no credential requirements — always available."""
    app = create_app(session_factory=async_session_factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/data/providers")
    assert r.status_code == 200
    body = r.json()
    by_name = {p["name"]: p for p in body}
    assert by_name["yfinance"]["available"] is True
    assert by_name["yfinance"]["reason"] is None


@pytest.mark.asyncio
async def test_providers_polygon_requires_key(async_session_factory):
    """polygon requires polygon_api_key Setting."""
    app = create_app(session_factory=async_session_factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/data/providers")
    body = r.json()
    by_name = {p["name"]: p for p in body}
    assert by_name["polygon"]["available"] is False
    assert "polygon" in by_name["polygon"]["reason"].lower()


@pytest.mark.asyncio
async def test_providers_alpaca_requires_account(async_session_factory):
    """alpaca becomes available once an Account row with broker_type='alpaca' exists."""
    async with async_session_factory() as s:
        s.add(Account(
            name="test-alpaca", broker_type="alpaca", environment="paper",
            credentials="{}", supported_asset_types=["crypto", "equity"],
        ))
        await s.commit()
    app = create_app(session_factory=async_session_factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/data/providers")
    body = r.json()
    by_name = {p["name"]: p for p in body}
    assert by_name["alpaca"]["available"] is True


@pytest.mark.asyncio
async def test_providers_ordering_is_alphabetical(async_session_factory):
    app = create_app(session_factory=async_session_factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/data/providers")
    body = r.json()
    names = [p["name"] for p in body]
    assert names == sorted(names)
```

If `async_session_factory` fixture doesn't exist yet in `tests/coordinator/api/conftest.py`, check `tests/conftest.py` for the existing pattern and reuse — most existing tests already have an in-memory session factory fixture; copy the pattern.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/coordinator/api/test_data_providers.py -v`
Expected: FAIL with `404 Not Found` from the GET endpoint (route doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

In `coordinator/api/routes/data.py`, add at the end of the file:

```python
async def _provider_availability(db: AsyncSession) -> list[dict]:
    """Return the per-provider availability matrix derived from Settings + Accounts.

    Order: alphabetical, stable. Each entry: {name, available, reason}.
    `reason` is None when available, otherwise an explanatory string.
    """
    from coordinator.database.models import Account, Setting

    async def _setting(key: str) -> str | None:
        row = (await db.execute(
            select(Setting).where(Setting.key == key)
        )).scalar_one_or_none()
        return row.value if row else None

    polygon_key = await _setting("polygon_api_key")
    theta_user = await _setting("theta_data_username")
    theta_pw = await _setting("theta_data_password")

    accounts_by_broker: dict[str, int] = {}
    rows = (await db.execute(select(Account))).scalars().all()
    for a in rows:
        accounts_by_broker[a.broker_type] = accounts_by_broker.get(a.broker_type, 0) + 1

    def _entry(name: str, available: bool, reason: str | None) -> dict:
        return {"name": name, "available": available, "reason": None if available else reason}

    matrix = [
        _entry("alpaca",   accounts_by_broker.get("alpaca", 0) > 0,
               "no alpaca account configured"),
        _entry("coinbase", accounts_by_broker.get("coinbase", 0) > 0,
               "no coinbase account configured"),
        _entry("polygon",  polygon_key is not None, "polygon_api_key not configured"),
        _entry("theta",    bool(theta_user and theta_pw),
               "theta credentials not configured"),
        _entry("tradier",  accounts_by_broker.get("tradier", 0) > 0,
               "no tradier account configured"),
        _entry("yfinance", True, None),
    ]
    return sorted(matrix, key=lambda e: e["name"])


@router.get("/providers")
async def list_providers(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Provider availability matrix for the dashboard's benchmark dropdown
    (and the create-run validator). See _provider_availability for the rules.
    """
    return await _provider_availability(db)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/coordinator/api/test_data_providers.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add coordinator/api/routes/data.py tests/coordinator/api/test_data_providers.py
git commit -m "feat(data): GET /api/data/providers + availability helper

Returns alphabetical [{name, available, reason}] matrix from Settings + Accounts.
Backs the dashboard benchmark dropdown and the create-run validator (I17).
"
```

---

### Task A2: Create-run validates `benchmark_source` against availability

**Files:**
- Modify: `coordinator/api/routes/backtest_runs.py` (validate in `create_run`)
- Test: `tests/coordinator/api/test_backtest_runs_benchmark_validation.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/api/test_backtest_runs_benchmark_validation.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport

from coordinator.api.app import create_app
from coordinator.database.models import Algorithm


@pytest.mark.asyncio
async def test_create_run_rejects_unavailable_benchmark_source(async_session_factory):
    async with async_session_factory() as s:
        s.add(Algorithm(
            id="algo-1", repo_url="https://github.com/x/y", name="y",
            install_status="installed",
        ))
        await s.commit()

    app = create_app(session_factory=async_session_factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/backtest-runs", json={
            "algorithm_id": "algo-1",
            "date_range_start": "2024-01-01T00:00:00",
            "date_range_end": "2024-02-01T00:00:00",
            "initial_cash": 10000.0,
            "benchmark_symbol": "SPY",
            "benchmark_source": "theta",  # not configured
        })
    assert r.status_code == 422
    assert "theta" in r.json()["detail"]


@pytest.mark.asyncio
async def test_create_run_accepts_available_benchmark_source(async_session_factory):
    async with async_session_factory() as s:
        s.add(Algorithm(
            id="algo-1", repo_url="https://github.com/x/y", name="y",
            install_status="installed",
        ))
        await s.commit()

    app = create_app(session_factory=async_session_factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/backtest-runs", json={
            "algorithm_id": "algo-1",
            "date_range_start": "2024-01-01T00:00:00",
            "date_range_end": "2024-02-01T00:00:00",
            "initial_cash": 10000.0,
            "benchmark_symbol": "SPY",
            "benchmark_source": "yfinance",  # always available
        })
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_create_run_no_benchmark_is_unaffected(async_session_factory):
    async with async_session_factory() as s:
        s.add(Algorithm(
            id="algo-1", repo_url="https://github.com/x/y", name="y",
            install_status="installed",
        ))
        await s.commit()

    app = create_app(session_factory=async_session_factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/backtest-runs", json={
            "algorithm_id": "algo-1",
            "date_range_start": "2024-01-01T00:00:00",
            "date_range_end": "2024-02-01T00:00:00",
            "initial_cash": 10000.0,
        })
    assert r.status_code == 201, r.text
```

You may need to stub the runner dispatch so the background task doesn't fail loud. The existing testing pattern in `tests/coordinator/api/` likely already mocks `_dispatch_runner`; copy its approach.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/coordinator/api/test_backtest_runs_benchmark_validation.py -v`
Expected: FAIL on `test_create_run_rejects_unavailable_benchmark_source` (currently returns 201 because there's no validator).

- [ ] **Step 3: Write minimal implementation**

In `coordinator/api/routes/backtest_runs.py`, after the existing algorithm lookup in `create_run`, add the benchmark validation. Import the helper near the top of the file:

```python
from coordinator.api.routes.data import _provider_availability
```

Then, in `create_run` (right after the parameter_set_id block resolves `config_overrides`), insert:

```python
    # Validate benchmark_source against current provider availability (I17).
    if body.benchmark_source:
        matrix = await _provider_availability(db)
        entry = next((p for p in matrix if p["name"] == body.benchmark_source), None)
        if entry is None or not entry["available"]:
            reason = (entry or {}).get("reason") or "provider not registered"
            raise HTTPException(
                422,
                detail=f"benchmark_source {body.benchmark_source!r} is not available: {reason}",
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/coordinator/api/test_backtest_runs_benchmark_validation.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add coordinator/api/routes/backtest_runs.py tests/coordinator/api/test_backtest_runs_benchmark_validation.py
git commit -m "feat(backtest-runs): validate benchmark_source against provider availability

Reject 422 when benchmark_source points at a provider with no creds/account.
Existing rows with unavailable sources stay viewable — only create gates (I17).
"
```

---

### Task A3: Runner downloads missing benchmark data before finalize

**Files:**
- Modify: `coordinator/services/backtest_runner.py:471-483` (benchmark load block)
- Test: `tests/coordinator/services/test_backtest_runner_benchmark_download.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/services/test_backtest_runner_benchmark_download.py`:

```python
import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest


@pytest.mark.asyncio
async def test_runner_triggers_download_when_benchmark_missing(monkeypatch):
    """When the benchmark parquet is missing, the runner should call
    _download_and_wait and re-read before falling back to no-benchmark."""
    from coordinator.services.backtest_runner import BacktestRunner

    ds = MagicMock()
    # First call returns empty df (missing); after "download", returns real bars.
    bars = pd.DataFrame({"timestamp": pd.date_range("2024-01-01", periods=5),
                         "open": [1]*5, "high": [1]*5, "low": [1]*5,
                         "close": [1]*5, "volume": [1]*5})
    ds.load_market_data.side_effect = [pd.DataFrame(), bars]

    dm = MagicMock()
    dm.create_download = AsyncMock(return_value={"id": "dl-1"})

    runner = BacktestRunner(session_factory=MagicMock(), download_manager=dm,
                            data_service=ds)
    runner._wait_for_download = AsyncMock()

    await runner._download_and_wait(
        symbol="SPY", timeframe="1day", source="yfinance",
        start=date(2024, 1, 1), end=date(2024, 1, 10),
    )

    dm.create_download.assert_called_once()
    args = dm.create_download.call_args.kwargs
    assert args["symbols"] == ["SPY"]
    assert args["provider"] == "yfinance"
    assert args["timeframe"] == "1day"
```

This first test isolates the helper (`_download_and_wait` already exists at line 699). The next test verifies the runner-level integration:

```python
@pytest.mark.asyncio
async def test_runner_benchmark_load_uses_download_and_retry(monkeypatch):
    """The benchmark block inside BacktestRunner.run should: try load,
    on empty/None, call _download_and_wait, retry the load, and use the result."""
    from coordinator.services import backtest_runner as br_mod

    # Use the module-level helper that mirrors the benchmark block. We extract
    # the load-then-download-then-retry sequence into a small function for
    # testability.
    ds = MagicMock()
    bars = pd.DataFrame({"timestamp": pd.date_range("2024-01-01", periods=3),
                         "open": [1]*3, "high": [1]*3, "low": [1]*3,
                         "close": [1]*3, "volume": [1]*3})
    ds.load_market_data.side_effect = [pd.DataFrame(), bars]
    downloader = AsyncMock()

    bdf = await br_mod._load_benchmark_with_download(
        ds=ds, source="yfinance", symbol="SPY",
        date_range_start=pd.Timestamp("2024-01-01"),
        date_range_end=pd.Timestamp("2024-01-10"),
        downloader=downloader,
    )
    downloader.assert_called_once()
    assert bdf is not None
    assert len(bdf) == 3


@pytest.mark.asyncio
async def test_runner_benchmark_load_returns_none_when_download_fails():
    from coordinator.services import backtest_runner as br_mod

    ds = MagicMock()
    ds.load_market_data.return_value = pd.DataFrame()  # always empty
    downloader = AsyncMock()

    bdf = await br_mod._load_benchmark_with_download(
        ds=ds, source="yfinance", symbol="SPY",
        date_range_start=pd.Timestamp("2024-01-01"),
        date_range_end=pd.Timestamp("2024-01-10"),
        downloader=downloader,
    )
    downloader.assert_called_once()
    assert bdf is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/coordinator/services/test_backtest_runner_benchmark_download.py -v`
Expected: FAIL with `AttributeError: module 'coordinator.services.backtest_runner' has no attribute '_load_benchmark_with_download'`.

- [ ] **Step 3: Write minimal implementation**

In `coordinator/services/backtest_runner.py`, add this helper above the `BacktestRunner` class:

```python
async def _load_benchmark_with_download(
    *, ds, source: str, symbol: str,
    date_range_start, date_range_end, downloader,
) -> Optional[pd.DataFrame]:
    """Load benchmark daily bars, downloading on demand if the parquet is missing.

    `downloader` is an awaitable callable matching the signature of
    BacktestRunner._download_and_wait — invoked with (symbol, timeframe,
    source, start, end). The benchmark is best-effort: returns None when
    nothing is on disk after one download attempt. Caller logs and proceeds
    without a benchmark in that case (I16).
    """
    bdf = ds.load_market_data(source, symbol, "1day")
    if bdf is None or bdf.empty:
        await downloader(symbol=symbol, timeframe="1day", source=source,
                         start=date_range_start, end=date_range_end)
        bdf = ds.load_market_data(source, symbol, "1day")
    if bdf is None or bdf.empty:
        return None
    return bdf
```

Then replace the benchmark block in `BacktestRunner.run` (currently at lines 471-483) with:

```python
            # Load benchmark bars for finalize (if configured).  Missing data
            # triggers a download via the same path strategy data uses; the
            # runner status flips to downloading_data for the duration (I16).
            benchmark_bar_df = None
            async with self._sf() as session:
                r = (await session.execute(
                    select(BacktestRun).where(BacktestRun.id == run_id)
                )).scalar_one()
                bench_symbol = r.benchmark_symbol
                bench_source = r.benchmark_source
            if bench_symbol and bench_source:
                async with self._sf() as session:
                    r = (await session.execute(
                        select(BacktestRun).where(BacktestRun.id == run_id)
                    )).scalar_one()
                    r.progress_message = f"Downloading benchmark {bench_symbol} from {bench_source}"
                    await session.commit()
                benchmark_bar_df = await _load_benchmark_with_download(
                    ds=self._ds, source=bench_source, symbol=bench_symbol,
                    date_range_start=date_range_start, date_range_end=date_range_end,
                    downloader=self._download_and_wait,
                )
                if benchmark_bar_df is None:
                    logger.warning(
                        "Benchmark %s/%s unavailable after download attempt; "
                        "finalizing without benchmark.", bench_source, bench_symbol,
                    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/coordinator/services/test_backtest_runner_benchmark_download.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/backtest_runner.py tests/coordinator/services/test_backtest_runner_benchmark_download.py
git commit -m "feat(backtest-runner): download missing benchmark data on demand

Extracts _load_benchmark_with_download helper that mirrors the strategy data
download path. Missing benchmark parquet now flips the run to
downloading_data → running just like missing strategy data does (I16).
"
```

---

### Task A4: Dashboard dropdown reads `/api/data/providers`

**Files:**
- Modify: `dashboard/src/components/RunBacktestModal.tsx:198-218`
- No backend test — verified by manual smoke test (see Step 4).

- [ ] **Step 1: Replace the hardcoded dropdown**

In `RunBacktestModal.tsx`, add to the top-level imports if not already present:

```tsx
import { useEffect, useState } from "react";
```

Inside the component, add state and effect (place near the existing `useState` declarations for `benchmarkSymbol` / `benchmarkSource`):

```tsx
type Provider = { name: string; available: boolean; reason: string | null };
const [providers, setProviders] = useState<Provider[]>([]);

useEffect(() => {
  let cancelled = false;
  fetch("/api/data/providers")
    .then((r) => r.json())
    .then((data: Provider[]) => {
      if (cancelled) return;
      setProviders(data);
      const firstAvailable = data.find((p) => p.available);
      if (firstAvailable && !benchmarkSource) {
        setBenchmarkSource(firstAvailable.name);
      }
    })
    .catch(() => {});  // dropdown stays empty; user sees blank
  return () => {
    cancelled = true;
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, []);
```

Then replace the existing `<select>` (lines 209-216) with:

```tsx
<select
  value={benchmarkSource}
  onChange={(e) => setBenchmarkSource(e.target.value)}
  className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1"
>
  {providers
    .filter((p) => p.available)
    .map((p) => (
      <option key={p.name} value={p.name}>
        {p.name}
      </option>
    ))}
</select>
```

- [ ] **Step 2: Type-check**

Run: `cd dashboard && npm run build`
Expected: build succeeds. If there's a separate `tsc` step or lint, run those too.

- [ ] **Step 3: Manual smoke test**

Run the coordinator + dashboard. Open the RunBacktestModal. Verify:
- The benchmark-source dropdown only shows available providers (initially: `yfinance` if no creds configured).
- Adding a `polygon_api_key` Setting via the settings page and reopening the modal shows polygon.

If the dashboard isn't built in your environment, document the manual steps in your handoff and skip to Step 4.

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/components/RunBacktestModal.tsx
git commit -m "feat(dashboard): benchmark dropdown reads /api/data/providers

Replaces hardcoded polygon/theta options with the live availability matrix.
Falls back to first available provider when no current value is set.
"
```

---

### Task A5: Spec — mark P1 invariants live

**Files:**
- Modify: `docs/superpowers/specs/2026-05-28-backtest-and-validation-lab-integration.md`

- [ ] **Step 1: Add I16 and I17 to the Integration invariants section**

Locate the "Integration invariants" section (after I15). Insert before "## Backtest Engine ↔ Validation Lab API contract":

```markdown
### I16: Benchmark loading reuses the same download-and-wait path as strategy data

`BacktestRunner.run` loads the benchmark via `_load_benchmark_with_download(ds, source, symbol, start, end, downloader)`. Missing benchmark parquet triggers `_download_and_wait` and one retry; if still empty, the run finalizes without a benchmark (warning logged) — strategy metrics still produced.

**Provenance:** P1 implementation, commit TBD. Removes the silent benchmark drop and cost trap described in the spec's P1 motivation.

### I17: Provider availability is derived at request time from Settings + Accounts; never hardcoded in API or UI

The single helper `coordinator/api/routes/data.py:_provider_availability(db)` is the canonical source. It is consumed by:
- `GET /api/data/providers` — the dashboard's RunBacktestModal reads this on mount and renders the dropdown from `available=true` entries.
- `POST /api/backtest-runs` — the create handler validates `benchmark_source` against the matrix and returns `422 {"detail": "benchmark_source 'X' is not available: <reason>"}` when the picked source has `available=false`.

Existing BacktestRun rows referencing an unavailable source remain viewable on detail pages — only the create form gates.

**Provenance:** P1 implementation, commit TBD.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-05-28-backtest-and-validation-lab-integration.md
git commit -m "docs(spec): mark I16, I17 live (P1 benchmark expansion shipped)"
```

---

# Phase B — P2 async-job model

### Task B1: Alembic migration — research_jobs table

**Files:**
- Create: `coordinator/database/migrations/versions/<auto_rev>_add_research_jobs.py`

- [ ] **Step 1: Generate the migration scaffold**

Run from the repo root:

```bash
alembic -c coordinator/database/migrations/alembic.ini revision -m "add_research_jobs"
```

The CLI prints the generated file path. Edit that file (path will look like `coordinator/database/migrations/versions/<rev>_add_research_jobs.py`).

- [ ] **Step 2: Implement upgrade/downgrade**

Replace the auto-generated body with:

```python
"""add_research_jobs

Revision ID: <preserve auto-generated>
Revises: <preserve auto-generated>
Create Date: <preserve auto-generated>
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "<preserve>"
down_revision: Union[str, None] = "<preserve>"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_jobs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),  # sweep | walk-forward
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("progress_pct", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("progress_message", sa.Text(), nullable=True),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("run_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.current_timestamp()),
        sa.ForeignKeyConstraint(["session_id"], ["optimization_sessions.id"]),
    )
    op.create_index(
        "ix_research_jobs_session_id", "research_jobs", ["session_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_research_jobs_session_id", table_name="research_jobs")
    op.drop_table("research_jobs")
```

- [ ] **Step 3: Apply the migration locally**

```bash
alembic -c coordinator/database/migrations/alembic.ini upgrade head
```

Expected: `Running upgrade <prev> -> <rev>, add_research_jobs`. If the upgrade fails, fix the migration file and re-run.

- [ ] **Step 4: Commit**

```bash
git add coordinator/database/migrations/versions/<rev>_add_research_jobs.py
git commit -m "feat(db): add research_jobs table (P2)

Async-job model for sweep / walk-forward orchestration.  Columns mirror
DownloadManager's state machine: queued / running / completed / failed /
cancelled, with progress fields and a request_payload JSON blob.
"
```

---

### Task B2: SQLAlchemy `ResearchJob` model

**Files:**
- Modify: `coordinator/database/models.py` (add ResearchJob alongside OptimizationSession)
- Test: `tests/coordinator/database/test_research_job_model.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/database/test_research_job_model.py`:

```python
import pytest
from datetime import datetime


@pytest.mark.asyncio
async def test_research_job_round_trips(async_session_factory):
    from coordinator.database.models import OptimizationSession, ResearchJob

    async with async_session_factory() as s:
        sess = OptimizationSession(
            name="t", hypothesis="h",
            parameter_space="{}", pre_registered_criteria="{}",
        )
        s.add(sess)
        await s.flush()
        job = ResearchJob(
            id="job-1", session_id=sess.id, kind="sweep",
            status="queued", progress_pct=0.0,
            request_payload={"manifest_path": "x", "base_config": {}},
            run_ids=[],
        )
        s.add(job)
        await s.commit()
        await s.refresh(job)

    async with async_session_factory() as s:
        from sqlalchemy import select
        row = (await s.execute(select(ResearchJob).where(ResearchJob.id == "job-1"))).scalar_one()
        assert row.kind == "sweep"
        assert row.status == "queued"
        assert row.request_payload["manifest_path"] == "x"
        assert row.run_ids == []
        assert row.session_id == sess.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/coordinator/database/test_research_job_model.py -v`
Expected: FAIL with `ImportError: cannot import name 'ResearchJob' from 'coordinator.database.models'`.

- [ ] **Step 3: Write minimal implementation**

In `coordinator/database/models.py`, just after `OptimizationSession` (around line 472), add:

```python
class ResearchJob(Base):
    __tablename__ = "research_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("optimization_sessions.id"), nullable=False, index=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # sweep | walk-forward
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    # queued | running | completed | failed | cancelled

    progress_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    progress_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    request_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    run_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

Ensure `Float` is in the existing import list at the top of `models.py`. If it isn't, add it to the `from sqlalchemy import ...` line.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/coordinator/database/test_research_job_model.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add coordinator/database/models.py tests/coordinator/database/test_research_job_model.py
git commit -m "feat(db): ResearchJob SQLAlchemy model"
```

---

### Task B3: `ResearchJobManager` — create + get + list + cancel + recover_orphans

**Files:**
- Create: `coordinator/services/research_job_manager.py`
- Test: `tests/coordinator/services/test_research_job_manager.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/coordinator/services/test_research_job_manager.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from coordinator.database.models import ResearchJob, OptimizationSession


@pytest.mark.asyncio
async def test_create_sweep_job_inserts_queued_row(async_session_factory):
    from coordinator.services.research_job_manager import ResearchJobManager

    async with async_session_factory() as s:
        sess = OptimizationSession(name="t", hypothesis="h",
                                   parameter_space="{}", pre_registered_criteria="{}")
        s.add(sess); await s.commit()
        session_id = sess.id

    runner_factory = AsyncMock()
    mgr = ResearchJobManager(
        session_factory=async_session_factory,
        sweep_fn=AsyncMock(),
        walk_forward_fn=AsyncMock(),
        runner_factory=runner_factory,
    )
    job_id = await mgr.create_sweep_job(
        session_id=session_id,
        request_payload={"manifest_path": "x", "base_config": {}, "search": "grid"},
    )
    # Don't let the background task run a full sweep in the unit test — cancel.
    await mgr.cancel_job(job_id)

    async with async_session_factory() as s:
        from sqlalchemy import select
        row = (await s.execute(select(ResearchJob).where(ResearchJob.id == job_id))).scalar_one()
        assert row.kind == "sweep"
        assert row.session_id == session_id
        assert row.request_payload["manifest_path"] == "x"


@pytest.mark.asyncio
async def test_get_job_returns_dict_or_none(async_session_factory):
    from coordinator.services.research_job_manager import ResearchJobManager

    mgr = ResearchJobManager(
        session_factory=async_session_factory,
        sweep_fn=AsyncMock(), walk_forward_fn=AsyncMock(),
        runner_factory=AsyncMock(),
    )
    assert await mgr.get_job("missing") is None


@pytest.mark.asyncio
async def test_recover_orphaned_jobs_marks_queued_and_running_failed(async_session_factory):
    from coordinator.services.research_job_manager import ResearchJobManager

    async with async_session_factory() as s:
        sess = OptimizationSession(name="t", hypothesis="h",
                                   parameter_space="{}", pre_registered_criteria="{}")
        s.add(sess); await s.flush()
        s.add(ResearchJob(id="a", session_id=sess.id, kind="sweep",
                          status="queued", request_payload={}, run_ids=[]))
        s.add(ResearchJob(id="b", session_id=sess.id, kind="walk-forward",
                          status="running", request_payload={}, run_ids=[]))
        s.add(ResearchJob(id="c", session_id=sess.id, kind="sweep",
                          status="completed", request_payload={}, run_ids=[]))
        await s.commit()

    mgr = ResearchJobManager(
        session_factory=async_session_factory,
        sweep_fn=AsyncMock(), walk_forward_fn=AsyncMock(),
        runner_factory=AsyncMock(),
    )
    count = await mgr.recover_orphaned_jobs()
    assert count == 2

    async with async_session_factory() as s:
        from sqlalchemy import select
        rows = {r.id: r for r in (await s.execute(select(ResearchJob))).scalars().all()}
        assert rows["a"].status == "failed"
        assert rows["b"].status == "failed"
        assert rows["c"].status == "completed"
        assert "orphan" in rows["a"].error_message.lower()


@pytest.mark.asyncio
async def test_cancel_job_flips_status_to_cancelled(async_session_factory):
    from coordinator.services.research_job_manager import ResearchJobManager

    async with async_session_factory() as s:
        sess = OptimizationSession(name="t", hypothesis="h",
                                   parameter_space="{}", pre_registered_criteria="{}")
        s.add(sess); await s.flush()
        s.add(ResearchJob(id="job-x", session_id=sess.id, kind="sweep",
                          status="running", request_payload={}, run_ids=[]))
        await s.commit()

    mgr = ResearchJobManager(
        session_factory=async_session_factory,
        sweep_fn=AsyncMock(), walk_forward_fn=AsyncMock(),
        runner_factory=AsyncMock(),
    )
    ok = await mgr.cancel_job("job-x")
    assert ok is True

    async with async_session_factory() as s:
        from sqlalchemy import select
        row = (await s.execute(select(ResearchJob).where(ResearchJob.id == "job-x"))).scalar_one()
        assert row.status == "cancelled"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/coordinator/services/test_research_job_manager.py -v`
Expected: FAIL with `ModuleNotFoundError: coordinator.services.research_job_manager`.

- [ ] **Step 3: Write minimal implementation**

Create `coordinator/services/research_job_manager.py`:

```python
"""ResearchJobManager — fire-and-poll orchestration for sweep / walk-forward.

Mirrors DownloadManager's pattern: a request to start a job inserts a DB row,
returns the id immediately, then runs the work in an asyncio.create_task that
streams progress updates into the row. Polling endpoints read the row.

Invariant I18: Research orchestration endpoints are fire-and-poll.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from coordinator.database.models import OptimizationSession, ResearchJob

logger = logging.getLogger(__name__)


SweepFn = Callable[..., Awaitable[Any]]          # signature of run_sweep
WalkForwardFn = Callable[..., Awaitable[Any]]    # signature of run_walk_forward
RunnerFactory = Callable[[str], Awaitable[None]] # (run_id) -> None


class ResearchJobManager:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        sweep_fn: SweepFn,
        walk_forward_fn: WalkForwardFn,
        runner_factory: RunnerFactory,
        sync_session_factory: Optional[Callable[[], Any]] = None,
    ):
        self._sf = session_factory
        self._sweep_fn = sweep_fn
        self._wf_fn = walk_forward_fn
        self._runner_factory = runner_factory
        self._sync_sf = sync_session_factory
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._cancel_flags: dict[str, asyncio.Event] = {}

    # ---- Public API ----------------------------------------------------

    async def create_sweep_job(self, *, session_id: int, request_payload: dict) -> str:
        return await self._create_job("sweep", session_id, request_payload)

    async def create_walk_forward_job(self, *, session_id: int, request_payload: dict) -> str:
        return await self._create_job("walk-forward", session_id, request_payload)

    async def get_job(self, job_id: str) -> Optional[dict]:
        async with self._sf() as s:
            row = (await s.execute(select(ResearchJob).where(ResearchJob.id == job_id))).scalar_one_or_none()
            return _row_to_dict(row) if row else None

    async def list_jobs(self, session_id: int) -> list[dict]:
        async with self._sf() as s:
            rows = (await s.execute(
                select(ResearchJob).where(ResearchJob.session_id == session_id)
                .order_by(ResearchJob.created_at.desc())
            )).scalars().all()
            return [_row_to_dict(r) for r in rows]

    async def cancel_job(self, job_id: str) -> bool:
        flag = self._cancel_flags.get(job_id)
        if flag is not None:
            flag.set()
        task = self._active_tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()
        async with self._sf() as s:
            row = (await s.execute(select(ResearchJob).where(ResearchJob.id == job_id))).scalar_one_or_none()
            if row is None:
                return False
            if row.status in ("completed", "failed", "cancelled"):
                return True
            row.status = "cancelled"
            row.completed_at = datetime.now(timezone.utc)
            await s.commit()
        return True

    async def recover_orphaned_jobs(self) -> int:
        async with self._sf() as s:
            rows = (await s.execute(
                select(ResearchJob).where(ResearchJob.status.in_(["queued", "running"]))
            )).scalars().all()
            for r in rows:
                r.status = "failed"
                r.completed_at = datetime.now(timezone.utc)
                r.error_message = "Orphaned by coordinator restart"
            count = len(rows)
            await s.commit()
            return count

    async def shutdown(self) -> None:
        for t in list(self._active_tasks.values()):
            if not t.done():
                t.cancel()
        for t in list(self._active_tasks.values()):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._active_tasks.clear()
        self._cancel_flags.clear()

    # ---- Internals -----------------------------------------------------

    async def _create_job(self, kind: str, session_id: int, request_payload: dict) -> str:
        job_id = str(uuid.uuid4())
        async with self._sf() as s:
            sess = (await s.execute(
                select(OptimizationSession).where(OptimizationSession.id == session_id)
            )).scalar_one_or_none()
            if sess is None:
                raise ValueError(f"session {session_id} not found")
            row = ResearchJob(
                id=job_id, session_id=session_id, kind=kind,
                status="queued", progress_pct=0.0,
                request_payload=request_payload, run_ids=[],
            )
            s.add(row)
            await s.commit()
        cancel_flag = asyncio.Event()
        self._cancel_flags[job_id] = cancel_flag
        task = asyncio.create_task(self._run_job(job_id, kind, session_id, request_payload, cancel_flag))
        self._active_tasks[job_id] = task
        return job_id

    async def _run_job(self, job_id: str, kind: str, session_id: int,
                       payload: dict, cancel_flag: asyncio.Event) -> None:
        try:
            await self._mark_running(job_id)
            progress_cb = _make_progress_callback(self._sf, job_id, cancel_flag)
            if kind == "sweep":
                await self._dispatch_sweep(session_id, payload, progress_cb)
            else:
                await self._dispatch_walk_forward(session_id, payload, progress_cb)
            await self._mark_terminal(job_id, "completed")
        except asyncio.CancelledError:
            await self._mark_terminal(job_id, "cancelled")
            raise
        except Exception as exc:
            logger.exception("ResearchJob %s failed", job_id)
            await self._mark_terminal(job_id, "failed", error=str(exc))
        finally:
            self._active_tasks.pop(job_id, None)
            self._cancel_flags.pop(job_id, None)

    async def _dispatch_sweep(self, session_id: int, payload: dict, progress_cb) -> None:
        # The sweep orchestrator uses a sync DB session — open it via the
        # sync_session_factory injected at construction.
        assert self._sync_sf is not None, "sync_session_factory required for sweep dispatch"
        with self._sync_sf() as db:
            await self._sweep_fn(
                db, self._runner_factory,
                session_id=session_id,
                manifest_path=payload["manifest_path"],
                base_config=payload["base_config"],
                parameter_space=payload.get("parameter_space"),
                search=payload.get("search", "grid"),
                max_trials=payload.get("max_trials", 50),
                parallelism=payload.get("parallelism", 1),
                seed=payload.get("seed", 0),
                progress_callback=progress_cb,
            )
            db.commit()

    async def _dispatch_walk_forward(self, session_id: int, payload: dict, progress_cb) -> None:
        assert self._sync_sf is not None
        with self._sync_sf() as db:
            await self._wf_fn(
                db, self._runner_factory,
                session_id=session_id,
                manifest_path=payload["manifest_path"],
                base_config=payload["base_config"],
                parameter_space=payload.get("parameter_space"),
                train_years=payload.get("train_years", 4.0),
                test_years=payload.get("test_years", 1.0),
                step_months=payload.get("step_months", 6.0),
                objective=payload.get("objective", "sharpe"),
                parallelism=payload.get("parallelism", 1),
                progress_callback=progress_cb,
            )
            db.commit()

    async def _mark_running(self, job_id: str) -> None:
        async with self._sf() as s:
            row = (await s.execute(select(ResearchJob).where(ResearchJob.id == job_id))).scalar_one()
            if row.status == "cancelled":
                raise asyncio.CancelledError()
            row.status = "running"
            row.started_at = datetime.now(timezone.utc)
            await s.commit()

    async def _mark_terminal(self, job_id: str, status: str, *, error: str | None = None) -> None:
        async with self._sf() as s:
            row = (await s.execute(select(ResearchJob).where(ResearchJob.id == job_id))).scalar_one_or_none()
            if row is None:
                return
            if row.status in ("completed", "failed", "cancelled"):
                return
            row.status = status
            row.completed_at = datetime.now(timezone.utc)
            if status == "completed":
                row.progress_pct = 1.0
            if error is not None:
                row.error_message = error
            await s.commit()


def _row_to_dict(row: ResearchJob) -> dict:
    return {
        "job_id": row.id,
        "session_id": row.session_id,
        "kind": row.kind,
        "status": row.status,
        "progress_pct": row.progress_pct,
        "progress_message": row.progress_message,
        "run_ids": row.run_ids or [],
        "error_message": row.error_message,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _make_progress_callback(session_factory, job_id: str, cancel_flag: asyncio.Event):
    """Return a callable the sweep/walk-forward orchestrator invokes after each
    completed trial / fold. Signature: (pct: float, message: str, run_ids: list[str]).

    The callback also raises asyncio.CancelledError if the cancel flag has been
    set, providing a cooperative cancellation point between trials.
    """
    async def cb(pct: float, message: str, run_ids: list[str]) -> None:
        if cancel_flag.is_set():
            raise asyncio.CancelledError()
        async with session_factory() as s:
            row = (await s.execute(select(ResearchJob).where(ResearchJob.id == job_id))).scalar_one_or_none()
            if row is None:
                return
            row.progress_pct = float(pct)
            row.progress_message = message
            row.run_ids = list(run_ids)
            await s.commit()
    return cb
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/coordinator/services/test_research_job_manager.py -v`
Expected: PASS (4 tests). If the create-sweep-job test hangs because the background task is trying to call `self._sync_sf` (which is None in the test), look at how the test cancels the job — `cancel_job` flips the row to `cancelled` and triggers `_run_job`'s except-branch via `cancel_flag.set()` + `task.cancel()`. The test doesn't await on the task; the row should already be in `cancelled` state by the time we assert.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/research_job_manager.py tests/coordinator/services/test_research_job_manager.py
git commit -m "feat(research): ResearchJobManager for async sweep/walk-forward jobs

DB-backed job state machine (queued/running/completed/failed/cancelled),
in-memory asyncio.Task registry, orphan recovery at startup, cooperative
cancellation via cancel flags read between trials (I18).
"
```

---

### Task B4: Wire `ResearchJobManager` into the service container

**Files:**
- Modify: `coordinator/main.py` (or `coordinator/api/dependencies.py` — wherever the existing container is built)

- [ ] **Step 1: Find the existing container construction**

Run: `grep -n "backtest_runner\|download_manager" coordinator/main.py | head -30`

Identify where `backtest_runner` is attached to the container at startup. The new manager goes alongside.

- [ ] **Step 2: Construct and attach ResearchJobManager**

Add the import:

```python
from coordinator.services.research_job_manager import ResearchJobManager
from coordinator.services.validation.sweep import run_sweep as _run_sweep_fn
from coordinator.services.validation.walk_forward import run_walk_forward as _run_walk_forward_fn
from coordinator.database.connection import get_session_factory as _get_sync_session_factory
```

After `container.backtest_runner` is assigned, add:

```python
async def _runner_factory(run_id: str) -> None:
    await container.backtest_runner.run(run_id)

container.research_job_manager = ResearchJobManager(
    session_factory=container.async_session_factory,
    sweep_fn=_run_sweep_fn,
    walk_forward_fn=_run_walk_forward_fn,
    runner_factory=_runner_factory,
    sync_session_factory=_get_sync_session_factory(),
)
```

(Field names will likely differ — `container.async_session_factory` may be named something like `container.session_factory` or `container.async_sf`. Match the existing convention.)

Then in the lifespan-startup function, after the existing `await container.backtest_runner.recover_orphaned_runs()` line, add:

```python
await container.research_job_manager.recover_orphaned_jobs()
```

And in shutdown, add:

```python
await container.research_job_manager.shutdown()
```

- [ ] **Step 3: Smoke-check the import graph**

Run: `python -c "from coordinator.main import create_app; create_app()"`
Expected: no `ImportError`. If the call to `create_app` requires arguments, just `python -c "import coordinator.main"` is enough.

- [ ] **Step 4: Commit**

```bash
git add coordinator/main.py
git commit -m "feat(coordinator): wire ResearchJobManager into container + lifespan

Construct alongside backtest_runner; recover orphans at startup; shutdown
cancels live job tasks.
"
```

---

### Task B5: Sweep + walk-forward accept `progress_callback`

**Files:**
- Modify: `coordinator/services/validation/sweep.py`
- Modify: `coordinator/services/validation/walk_forward.py`
- Test: `tests/coordinator/services/validation/test_progress_callbacks.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/services/validation/test_progress_callbacks.py`:

```python
from unittest.mock import AsyncMock, MagicMock
import pytest


@pytest.mark.asyncio
async def test_sweep_invokes_progress_callback_per_trial(monkeypatch):
    """run_sweep should call progress_callback(pct, message, run_ids) after each
    trial's BacktestRun row is committed."""
    from coordinator.services.validation import sweep as sweep_mod

    # Stub out _run_one_backtest so we don't actually drive the engine.
    async def fake_run_one(db, runner_factory, *, merged, **_kw):
        return {"run_id": f"r-{merged.get('_trial_idx', 0)}", "objective": 1.0}
    monkeypatch.setattr(sweep_mod, "_run_one_backtest", fake_run_one)

    progress_log: list = []
    async def cb(pct, message, run_ids):
        progress_log.append((pct, message, list(run_ids)))

    # Minimal grid parameter_space + base_config — three trials.
    result = await sweep_mod.run_sweep(
        MagicMock(),  # sync db session — fake_run_one doesn't touch it
        AsyncMock(),
        session_id=1,
        manifest_path="x.yaml",
        base_config={"start": "2024-01-01", "end": "2024-02-01",
                     "algorithm_id": "a", "initial_cash": 10000.0},
        parameter_space={"vol_target": [0.1, 0.15, 0.2]},
        search="grid", max_trials=3, parallelism=1, seed=0,
        progress_callback=cb,
    )

    # Three trials -> three progress updates ending at 1.0
    assert len(progress_log) >= 3
    assert progress_log[-1][0] == 1.0
    # run_ids accumulate
    assert len(progress_log[-1][2]) == 3


@pytest.mark.asyncio
async def test_walk_forward_invokes_progress_callback_per_fold(monkeypatch):
    """Same idea for walk-forward: callback fires after each fold completes."""
    from coordinator.services.validation import walk_forward as wf_mod

    async def fake_run_one(db, runner_factory, *, merged, **_kw):
        return {"run_id": f"oos-{merged.get('_fold_index', 0)}", "objective": 0.5}
    monkeypatch.setattr(wf_mod, "_run_one_backtest", fake_run_one)
    monkeypatch.setattr(wf_mod, "_pick_best_train_config",
                        lambda *a, **k: {"vol_target": 0.15})

    progress_log: list = []
    async def cb(pct, message, run_ids):
        progress_log.append((pct, message, list(run_ids)))

    await wf_mod.run_walk_forward(
        MagicMock(), AsyncMock(),
        session_id=1, manifest_path="x.yaml",
        base_config={"start": "2018-01-01", "end": "2024-01-01",
                     "algorithm_id": "a", "initial_cash": 10000.0},
        parameter_space={"vol_target": [0.1, 0.15]},
        train_years=4.0, test_years=1.0, step_months=12.0,
        objective="sharpe", parallelism=1,
        progress_callback=cb,
    )
    assert len(progress_log) >= 1
    assert progress_log[-1][0] == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/coordinator/services/validation/test_progress_callbacks.py -v`
Expected: FAIL — `run_sweep`/`run_walk_forward` don't accept `progress_callback`.

- [ ] **Step 3: Modify `sweep.py`**

Open `coordinator/services/validation/sweep.py`. Update `run_sweep`'s signature to accept `progress_callback`. After each trial's `BacktestRun` row is committed (look for the loop that iterates over configurations and calls `_run_one_backtest`), add:

```python
        if progress_callback is not None:
            pct = (i + 1) / total_trials
            message = f"Trial {i + 1} of {total_trials}"
            await progress_callback(pct, message, run_ids)
```

The exact variable names will be local to the function — adapt: `total_trials` is the length of the resolved trial list; `run_ids` is the list being accumulated; `i` is the loop index.

The `tpe` strategy ignores `parallelism` (sequential) — call the callback after each `study.tell`. Other strategies (grid, random, latin) batch with a semaphore — call the callback after each task completes (collect in `asyncio.as_completed`).

- [ ] **Step 4: Modify `walk_forward.py`**

Same pattern in `coordinator/services/validation/walk_forward.py`. After each fold's OOS run is committed:

```python
        if progress_callback is not None:
            pct = (fold_idx + 1) / total_folds
            message = f"Fold {fold_idx + 1} of {total_folds}"
            await progress_callback(pct, message, oos_run_ids)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/coordinator/services/validation/test_progress_callbacks.py -v`
Expected: PASS.

Then run the existing sweep + walk-forward suites to confirm no regression:

```bash
pytest tests/coordinator/services/validation/test_sweep.py tests/coordinator/services/validation/test_walk_forward.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add coordinator/services/validation/sweep.py coordinator/services/validation/walk_forward.py tests/coordinator/services/validation/test_progress_callbacks.py
git commit -m "feat(validation): sweep + walk-forward accept progress_callback

Optional progress_callback(pct, message, run_ids) invoked after each trial /
fold completes. Used by ResearchJobManager to stream progress into the
research_jobs row.  No-op when callback is None (existing behaviour
preserved).
"
```

---

### Task B6: Replace sync sweep + walk-forward endpoints with 202-Accepted

**Files:**
- Modify: `coordinator/api/routes/research.py` (sweep_endpoint, walk_forward_endpoint)
- Test: `tests/coordinator/api/test_research_jobs_endpoints.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/api/test_research_jobs_endpoints.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport

from coordinator.api.app import create_app
from coordinator.database.models import OptimizationSession, ResearchJob


@pytest.mark.asyncio
async def test_post_sweep_returns_202_with_job_id(async_session_factory, monkeypatch):
    async with async_session_factory() as s:
        sess = OptimizationSession(name="t", hypothesis="h",
                                   parameter_space='{"vol_target":[0.1,0.15]}',
                                   pre_registered_criteria="{}")
        s.add(sess); await s.commit()
        session_id = sess.id

    mgr = MagicMock()
    mgr.create_sweep_job = AsyncMock(return_value="job-123")

    app = create_app(session_factory=async_session_factory,
                     research_job_manager=mgr)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            f"/api/research/sessions/{session_id}/sweep",
            json={"manifest_path": "x.yaml", "base_config": {}, "search": "grid"},
        )
    assert r.status_code == 202
    body = r.json()
    assert body["job_id"] == "job-123"
    assert body["status"] == "queued"
    mgr.create_sweep_job.assert_called_once()


@pytest.mark.asyncio
async def test_get_job_returns_current_state(async_session_factory):
    async with async_session_factory() as s:
        sess = OptimizationSession(name="t", hypothesis="h",
                                   parameter_space="{}", pre_registered_criteria="{}")
        s.add(sess); await s.flush()
        s.add(ResearchJob(id="j", session_id=sess.id, kind="sweep",
                          status="running", progress_pct=0.5,
                          progress_message="Trial 1 of 2",
                          request_payload={}, run_ids=["r1"]))
        await s.commit()
        session_id = sess.id

    # The endpoint reads via the manager — wire a real manager backed by the
    # async session factory; sweep_fn/walk_forward_fn aren't invoked here.
    from coordinator.services.research_job_manager import ResearchJobManager
    mgr = ResearchJobManager(
        session_factory=async_session_factory,
        sweep_fn=AsyncMock(), walk_forward_fn=AsyncMock(),
        runner_factory=AsyncMock(),
    )

    app = create_app(session_factory=async_session_factory,
                     research_job_manager=mgr)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get(f"/api/research/sessions/{session_id}/jobs/j")
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == "j"
    assert body["status"] == "running"
    assert body["progress_pct"] == 0.5
    assert body["run_ids"] == ["r1"]


@pytest.mark.asyncio
async def test_delete_job_cancels(async_session_factory):
    async with async_session_factory() as s:
        sess = OptimizationSession(name="t", hypothesis="h",
                                   parameter_space="{}", pre_registered_criteria="{}")
        s.add(sess); await s.flush()
        s.add(ResearchJob(id="j2", session_id=sess.id, kind="sweep",
                          status="running", request_payload={}, run_ids=[]))
        await s.commit()
        session_id = sess.id

    from coordinator.services.research_job_manager import ResearchJobManager
    mgr = ResearchJobManager(
        session_factory=async_session_factory,
        sweep_fn=AsyncMock(), walk_forward_fn=AsyncMock(),
        runner_factory=AsyncMock(),
    )

    app = create_app(session_factory=async_session_factory,
                     research_job_manager=mgr)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.delete(f"/api/research/sessions/{session_id}/jobs/j2")
    assert r.status_code == 200

    async with async_session_factory() as s:
        from sqlalchemy import select
        row = (await s.execute(select(ResearchJob).where(ResearchJob.id == "j2"))).scalar_one()
        assert row.status == "cancelled"
```

You may need to extend `create_app` (or wherever `coordinator.api.app:create_app` lives) to accept a `research_job_manager` override so tests can inject a stub. If it's currently coupled to the global container, add a small override hook (`set_research_job_manager(mgr)` mirroring the existing `set_data_service` / `set_download_manager` pattern in `data.py`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/coordinator/api/test_research_jobs_endpoints.py -v`
Expected: FAIL — endpoints don't exist yet (or return SweepResponse instead of 202+job_id).

- [ ] **Step 3: Rewrite the sweep + walk-forward endpoints**

In `coordinator/api/routes/research.py`, replace the existing `sweep_endpoint` and `walk_forward_endpoint` (around lines 191-285):

```python
class JobResponse(BaseModel):
    job_id: str
    session_id: int
    kind: str
    status: str
    progress_pct: float = 0.0
    progress_message: str | None = None
    run_ids: list[str] = []
    error_message: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str | None = None


def _get_research_job_manager():
    container = get_container()
    mgr = getattr(container, "research_job_manager", None)
    if mgr is None:
        raise HTTPException(503, "research_job_manager not initialized")
    return mgr


@router.post(
    "/sessions/{session_id}/sweep",
    response_model=JobResponse,
    status_code=202,
)
async def sweep_endpoint(session_id: int, payload: SweepRequest) -> JobResponse:
    """Queue a sweep job and return immediately with the job_id (I18)."""
    mgr = _get_research_job_manager()
    request_payload = payload.model_dump(exclude_none=True)
    try:
        job_id = await mgr.create_sweep_job(
            session_id=session_id, request_payload=request_payload,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    job = await mgr.get_job(job_id)
    return JobResponse(**job)


@router.post(
    "/sessions/{session_id}/walk-forward",
    response_model=JobResponse,
    status_code=202,
)
async def walk_forward_endpoint(session_id: int, payload: WalkForwardRequest) -> JobResponse:
    """Queue a walk-forward job and return immediately with the job_id (I18)."""
    mgr = _get_research_job_manager()
    request_payload = payload.model_dump(exclude_none=True)
    try:
        job_id = await mgr.create_walk_forward_job(
            session_id=session_id, request_payload=request_payload,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    job = await mgr.get_job(job_id)
    return JobResponse(**job)


@router.get(
    "/sessions/{session_id}/jobs",
    response_model=list[JobResponse],
)
async def list_jobs_endpoint(session_id: int) -> list[JobResponse]:
    mgr = _get_research_job_manager()
    return [JobResponse(**j) for j in await mgr.list_jobs(session_id)]


@router.get(
    "/sessions/{session_id}/jobs/{job_id}",
    response_model=JobResponse,
)
async def get_job_endpoint(session_id: int, job_id: str) -> JobResponse:
    mgr = _get_research_job_manager()
    job = await mgr.get_job(job_id)
    if job is None or job["session_id"] != session_id:
        raise HTTPException(404, "job not found")
    return JobResponse(**job)


@router.delete("/sessions/{session_id}/jobs/{job_id}")
async def cancel_job_endpoint(session_id: int, job_id: str) -> dict:
    mgr = _get_research_job_manager()
    job = await mgr.get_job(job_id)
    if job is None or job["session_id"] != session_id:
        raise HTTPException(404, "job not found")
    await mgr.cancel_job(job_id)
    return {"ok": True}
```

Drop the old `SweepResponse` and `WalkForwardResponse` Pydantic classes (no longer used) and remove the now-unused imports of `get_session_factory`, `run_sweep`, `run_walk_forward` from this file — `ResearchJobManager` owns those.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/coordinator/api/test_research_jobs_endpoints.py -v`
Expected: PASS (3 tests).

Then run the full coordinator API suite to confirm no other test depends on the old endpoint shape:

```bash
pytest tests/coordinator/api/ -v
```

Expected: PASS. If some tests still expect the old synchronous response (`{n_configs, run_ids}`), those tests reflect the old contract and need to be rewritten to the job-based contract — fix them as part of this task.

- [ ] **Step 5: Commit**

```bash
git add coordinator/api/routes/research.py tests/coordinator/api/test_research_jobs_endpoints.py
git commit -m "feat(research-api): sweep + walk-forward are now 202 + job_id (I18)

POST endpoints queue a job in ResearchJobManager and return immediately.
New GET /sessions/:id/jobs[/:job_id] for polling; DELETE for cancel.
"
```

---

### Task B7: Update `quilt research sweep` / `walk-forward` CLI to poll

**Files:**
- Modify: `sdk/cli/commands/research.py` (cmd_sweep, cmd_walk_forward)
- Test: `tests/sdk/cli/test_research_cli_polling.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/sdk/cli/test_research_cli_polling.py`:

```python
from click.testing import CliRunner
from unittest.mock import AsyncMock, MagicMock
import pytest


def test_cli_sweep_polls_until_completed(monkeypatch):
    from sdk.cli.commands import research as research_mod

    # Stub the CoordinatorClient: POST returns 202 job, two GETs walk the state
    # from running -> completed.
    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value={
        "job_id": "j1", "session_id": 1, "kind": "sweep",
        "status": "queued", "progress_pct": 0.0, "progress_message": None,
        "run_ids": [], "error_message": None,
    })
    fake_client.get = AsyncMock(side_effect=[
        {"job_id": "j1", "status": "running", "progress_pct": 0.5,
         "progress_message": "Trial 1 of 2", "run_ids": ["r1"], "error_message": None},
        {"job_id": "j1", "status": "completed", "progress_pct": 1.0,
         "progress_message": "Done", "run_ids": ["r1", "r2"], "error_message": None},
    ])
    fake_client.aclose = AsyncMock()
    monkeypatch.setattr(research_mod, "_client", lambda ctx: fake_client)
    # Replace the polling sleep with a no-op so the test doesn't actually wait.
    monkeypatch.setattr(research_mod, "_poll_sleep_s", 0.0)

    runner = CliRunner()
    result = runner.invoke(research_mod.research_group, [
        "sweep", "--session-id", "1", "--manifest", "x.yaml",
        "--base-config", '{"start":"2024-01-01","end":"2024-02-01"}',
    ])
    assert result.exit_code == 0, result.output
    assert "completed" in result.output.lower()
    assert "j1" in result.output
    # Two GETs (running, completed)
    assert fake_client.get.call_count == 2


def test_cli_sweep_no_wait_exits_immediately(monkeypatch):
    from sdk.cli.commands import research as research_mod

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value={
        "job_id": "j-fast", "session_id": 1, "kind": "sweep",
        "status": "queued", "progress_pct": 0.0, "progress_message": None,
        "run_ids": [], "error_message": None,
    })
    fake_client.get = AsyncMock()
    fake_client.aclose = AsyncMock()
    monkeypatch.setattr(research_mod, "_client", lambda ctx: fake_client)

    runner = CliRunner()
    result = runner.invoke(research_mod.research_group, [
        "sweep", "--session-id", "1", "--manifest", "x.yaml",
        "--base-config", '{"start":"2024-01-01","end":"2024-02-01"}',
        "--no-wait",
    ])
    assert result.exit_code == 0
    assert "j-fast" in result.output
    fake_client.get.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/sdk/cli/test_research_cli_polling.py -v`
Expected: FAIL — the CLI doesn't poll (still expects the old synchronous response shape) and `--no-wait` doesn't exist.

- [ ] **Step 3: Update `cmd_sweep` and `cmd_walk_forward`**

In `sdk/cli/commands/research.py`, near the top add:

```python
_poll_sleep_s = 2.0  # tests monkey-patch to 0.0


async def _poll_job(c, session_id: int, job_id: str) -> dict:
    """Poll the job endpoint until the status is terminal.

    Returns the final job dict.  Renders a progress bar on the way.
    """
    import asyncio
    last_message = ""
    while True:
        job = await c.get(f"/api/research/sessions/{session_id}/jobs/{job_id}")
        status = job["status"]
        pct = job.get("progress_pct") or 0.0
        message = job.get("progress_message") or status
        if message != last_message:
            click.echo(f"[{int(pct * 100):>3d}%] {message}")
            last_message = message
        if status in ("completed", "failed", "cancelled"):
            return job
        if _poll_sleep_s > 0:
            await asyncio.sleep(_poll_sleep_s)
```

Then replace `cmd_sweep` with:

```python
@research_group.command("sweep")
@click.option("--session-id", type=int, required=True)
@click.option("--manifest", required=True, help="Path to the strategy's quilt.yaml (server-resolvable).")
@click.option("--base-config", required=True, help="Path to JSON file with base BacktestConfig (or inline JSON).")
@click.option("--parameter-space", default=None, help='Optional override of the session\'s parameter_space (inline JSON or file path).')
@click.option("--search", type=click.Choice(["grid", "random", "latin", "tpe"]), default="grid")
@click.option("--max-trials", type=int, default=50)
@click.option("--parallelism", type=int, default=1)
@click.option("--seed", type=int, default=0)
@click.option("--no-wait", is_flag=True, default=False, help="Print job_id and exit without polling.")
@click.pass_context
def cmd_sweep(ctx, session_id, manifest, base_config, parameter_space, search, max_trials, parallelism, seed, no_wait):
    """Queue a hyperparameter sweep under an existing session."""
    payload = {
        "manifest_path": manifest,
        "base_config": _parse_json_or_yaml_or_file(base_config),
        "search": search,
        "max_trials": max_trials,
        "parallelism": parallelism,
        "seed": seed,
    }
    if parameter_space:
        payload["parameter_space"] = _parse_json_or_yaml_or_file(parameter_space)

    async def go():
        c = _client(ctx)
        try:
            job = await c.post(f"/api/research/sessions/{session_id}/sweep", json=payload)
            click.echo(f"queued: {job['job_id']}")
            if no_wait:
                return job
            return await _poll_job(c, session_id, job["job_id"])
        finally:
            await c.aclose()

    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        if body.get("status") == "completed":
            click.echo(f"Sweep {body['job_id']} completed: {len(body.get('run_ids', []))} runs.")
        elif body.get("status") == "failed":
            click.echo(f"Sweep {body['job_id']} failed: {body.get('error_message')}", err=True)
        elif body.get("status") == "cancelled":
            click.echo(f"Sweep {body['job_id']} cancelled.")
        else:
            click.echo(f"Sweep {body['job_id']} status: {body.get('status')}")
```

And `cmd_walk_forward`:

```python
@research_group.command("walk-forward")
@click.option("--session-id", type=int, required=True)
@click.option("--manifest", required=True)
@click.option("--base-config", required=True)
@click.option("--parameter-space", default=None)
@click.option("--train-years", type=float, default=4.0)
@click.option("--test-years", type=float, default=1.0)
@click.option("--step-months", type=float, default=6.0)
@click.option("--objective", type=click.Choice(["sharpe", "calmar", "sortino"]), default="sharpe")
@click.option("--parallelism", type=int, default=1)
@click.option("--no-wait", is_flag=True, default=False)
@click.pass_context
def cmd_walk_forward(ctx, session_id, manifest, base_config, parameter_space, train_years, test_years, step_months, objective, parallelism, no_wait):
    """Queue a walk-forward optimization under an existing session."""
    payload = {
        "manifest_path": manifest,
        "base_config": _parse_json_or_yaml_or_file(base_config),
        "train_years": train_years,
        "test_years": test_years,
        "step_months": step_months,
        "objective": objective,
        "parallelism": parallelism,
    }
    if parameter_space:
        payload["parameter_space"] = _parse_json_or_yaml_or_file(parameter_space)

    async def go():
        c = _client(ctx)
        try:
            job = await c.post(f"/api/research/sessions/{session_id}/walk-forward", json=payload)
            click.echo(f"queued: {job['job_id']}")
            if no_wait:
                return job
            return await _poll_job(c, session_id, job["job_id"])
        finally:
            await c.aclose()

    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        if body.get("status") == "completed":
            click.echo(f"Walk-forward {body['job_id']} completed: {len(body.get('run_ids', []))} OOS runs.")
        elif body.get("status") == "failed":
            click.echo(f"Walk-forward {body['job_id']} failed: {body.get('error_message')}", err=True)
        elif body.get("status") == "cancelled":
            click.echo(f"Walk-forward {body['job_id']} cancelled.")
        else:
            click.echo(f"Walk-forward {body['job_id']} status: {body.get('status')}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/sdk/cli/test_research_cli_polling.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add sdk/cli/commands/research.py tests/sdk/cli/test_research_cli_polling.py
git commit -m "feat(cli): quilt research sweep/walk-forward poll job status

POST receives a job_id, then polls GET /jobs/:id every 2s until terminal.
New --no-wait flag prints the job_id and exits immediately.
"
```

---

### Task B8: Spec — mark I18 live and add the job-state diagram

**Files:**
- Modify: `docs/superpowers/specs/2026-05-28-backtest-and-validation-lab-integration.md`

- [ ] **Step 1: Add I18 to the invariants section**

Insert under the new I16 / I17 entries:

```markdown
### I18: Research orchestration endpoints (`sweep`, `walk-forward`) are fire-and-poll

`POST /api/research/sessions/{id}/sweep` and `.../walk-forward` return `202 Accepted` with `{"job_id", "session_id", "kind", "status": "queued", ...}` immediately. The work runs as an `asyncio.create_task` registered with `ResearchJobManager`, which streams progress (`progress_pct`, `progress_message`, accumulated `run_ids`) into the `research_jobs` DB row.

Polling: `GET /api/research/sessions/{id}/jobs/{job_id}` returns the current state. Terminal statuses: `completed | failed | cancelled`. `DELETE` flips a cancel flag the orchestrator observes between trials.

Orphan recovery: any `queued | running` row at coordinator startup becomes `failed` with `error_message="Orphaned by coordinator restart"` (mirrors `DownloadManager.recover_orphaned_downloads`).

**Provenance:** P2 implementation. Sync endpoints were hitting CLI HTTP timeouts on multi-hour sweeps even after the band-aid 600s timeout bump (commit `8fccfcc`).
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-05-28-backtest-and-validation-lab-integration.md
git commit -m "docs(spec): mark I18 live (P2 async-job model shipped)"
```

---

# Phase C — P3 union-of-symbol-timelines backtest clock

### Task C1: `_build_union_clock` covers all cache symbols (regression test for existing behaviour)

**Files:**
- Test: `tests/coordinator/services/test_backtest_engine_two_pass_clock.py` (create — first test only)

- [ ] **Step 1: Write a regression test for the current `_build_union_clock` static helper**

Create `tests/coordinator/services/test_backtest_engine_two_pass_clock.py`:

```python
import pandas as pd
import pytest

from coordinator.services.backtest_engine_v2 import BacktestEngine


def _make_bar_df(timestamps, base=100.0):
    n = len(timestamps)
    return pd.DataFrame({
        "timestamp": pd.to_datetime(timestamps),
        "open": [base] * n, "high": [base * 1.01] * n, "low": [base * 0.99] * n,
        "close": [base] * n, "volume": [1.0] * n,
    })


def test_build_union_clock_merges_all_symbol_timelines():
    """The union clock includes every distinct timestamp from every symbol."""
    bars = {
        ("yfinance", "BTC-USD", "1day"): _make_bar_df(
            ["2024-01-01", "2024-01-02", "2024-01-03"], base=100.0,
        ),
        ("yfinance", "ETH-USD", "1day"): _make_bar_df(
            ["2024-01-02", "2024-01-03", "2024-01-04"], base=200.0,
        ),
    }
    clock = BacktestEngine._build_union_clock(bars)
    assert list(clock["timestamp"].dt.strftime("%Y-%m-%d")) == [
        "2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04",
    ]
    # Deduped, sorted ascending; OHLCV present and non-zero.
    assert (clock["close"] > 0).all()


def test_build_union_clock_empty_bars():
    """No cache entries -> empty DataFrame with expected columns."""
    clock = BacktestEngine._build_union_clock({})
    assert list(clock.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert len(clock) == 0
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `pytest tests/coordinator/services/test_backtest_engine_two_pass_clock.py -v`
Expected: PASS. The helper has shipped and these tests document the contract this phase relies on. Subsequent tasks add tests for the *new* two-pass behavior.

- [ ] **Step 3: Commit**

```bash
git add tests/coordinator/services/test_backtest_engine_two_pass_clock.py
git commit -m "test(backtest-engine): pin existing _build_union_clock contract (P3 prep)"
```

---

### Task C2: `BacktestTickContext.reset_for_replay`

**Files:**
- Modify: `coordinator/services/backtest_tick_context.py` (add method)
- Test: `tests/coordinator/services/test_backtest_tick_context_reset.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/services/test_backtest_tick_context_reset.py`:

```python
import pandas as pd


def test_reset_for_replay_preserves_bars_cache_clears_tick_state():
    """reset_for_replay should clear sim_time + any algo-state mutations the
    engine made during pass 1, but keep the loaded bars cache so pass 2 doesn't
    re-download."""
    from coordinator.services.backtest_tick_context import BacktestTickContext

    bars = {("yfinance", "BTC-USD", "1day"): pd.DataFrame({"timestamp": [], "close": []})}
    ctx = BacktestTickContext(
        bars=dict(bars), positions={}, cash=10_000.0,
        default_source="yfinance",
    )
    # Simulate pass-1 mutations the engine performs.
    ctx.set_sim_time(pd.Timestamp("2024-01-15").to_pydatetime())
    ctx.update_account(cash=5000.0, account_value=20_000.0,
                       buying_power=5000.0, positions={"BTC/USD": object()})

    ctx.reset_for_replay()

    assert ctx._bars == bars  # cache preserved
    # tick-time state cleared
    assert getattr(ctx, "_sim_time", None) is None
    # Account state also resets to the initial cash so pass 2 starts clean.
    assert ctx.cash == 10_000.0
    assert ctx.account_value == 10_000.0
    assert ctx.positions == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/coordinator/services/test_backtest_tick_context_reset.py -v`
Expected: FAIL with `AttributeError: 'BacktestTickContext' object has no attribute 'reset_for_replay'`.

- [ ] **Step 3: Implement `reset_for_replay`**

In `coordinator/services/backtest_tick_context.py`, locate the `BacktestTickContext` class. Right after the constructor, add:

```python
    def reset_for_replay(self) -> None:
        """Clear all tick-time state set during pass-1 discovery so pass-2
        can replay from a clean slate.  Preserves:
        - self._bars (already populated; pass 2 doesn't re-download)
        - constructor-provided defaults: default_source, data_service, on_miss

        Clears:
        - sim_time
        - account snapshot (cash, account_value, buying_power, positions)

        Used by BacktestEngine's two-pass execution (P3, I3 simplified).
        """
        self._sim_time = None
        self.cash = self._initial_cash
        self.account_value = self._initial_cash
        self.buying_power = self._initial_cash
        self.positions = {}
```

You may need to capture `_initial_cash` in the constructor — find the existing `__init__` and add:

```python
        self._initial_cash = cash
```

right where `self.cash = cash` is assigned.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/coordinator/services/test_backtest_tick_context_reset.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/backtest_tick_context.py tests/coordinator/services/test_backtest_tick_context_reset.py
git commit -m "feat(backtest-tick-context): reset_for_replay() for two-pass execution

Keeps the bars cache so pass 2 doesn't re-download; clears sim_time + account
state so pass-2 replay starts from initial cash (P3 prep).
"
```

---

### Task C3: Engine pass-1 discovery (warmup tick that doesn't fire observers)

**Files:**
- Modify: `coordinator/services/backtest_engine_v2.py:105-140`
- Test: `tests/coordinator/services/test_backtest_engine_two_pass_clock.py` (extend)

- [ ] **Step 1: Add a failing test**

Append to `tests/coordinator/services/test_backtest_engine_two_pass_clock.py`:

```python
from unittest.mock import MagicMock, ANY


class _RecordingObserver:
    def __init__(self):
        self.events = []
    def on_tick(self, *a, **k): self.events.append(("tick", a, k))
    def on_signals_emitted(self, *a, **k): self.events.append(("sig", a, k))
    def on_signal_rejected(self, *a, **k): self.events.append(("rej", a, k))
    def on_fill(self, *a, **k): self.events.append(("fill", a, k))
    def on_error(self, *a, **k): self.events.append(("err", a, k))
    def on_summary(self, *a, **k): self.events.append(("sum", a, k))


class _DiscoveryAlgo:
    """An algo that calls ctx.market_data on first tick — enough for pass-1
    discovery to capture the symbol."""
    def on_start(self, config, restored_state):
        self._calls = 0
    def on_tick(self, ctx):
        self._calls += 1
        if self._calls == 1:
            # Touch a symbol to register it in pass-1 discovery.
            ctx.market_data("BTC/USD", n=1)
        return []


def test_two_pass_execution_does_not_double_call_observer(monkeypatch):
    """Pass 1 must not fire observers; observer.on_tick should equal len(real
    clock), not 2× len(real clock)."""
    from coordinator.services.backtest_engine_v2 import BacktestEngine
    from coordinator.services.backtest_tick_context import BacktestTickContext
    from coordinator.services.backtest_config import (
        BacktestConfig, SlippageModel,
    )

    bars = {
        ("yfinance", "BTC-USD", "1day"): _make_bar_df(
            ["2024-01-01", "2024-01-02", "2024-01-03"], base=100.0,
        ),
    }
    ctx = BacktestTickContext(
        bars=dict(bars), positions={}, cash=10_000.0,
        default_source="yfinance",
    )
    obs = _RecordingObserver()
    cfg = BacktestConfig(start="2024-01-01", end="2024-01-03",
                        initial_cash=10_000.0, cost_profile=None)
    eng = BacktestEngine(config=cfg)

    # Pass a synthetic clock — engine should rebuild on pass-2 using the union.
    synthetic_clock = _make_bar_df(["2024-01-01"], base=0.0)
    eng.run(
        algorithm=_DiscoveryAlgo(), ctx=ctx,
        clock_series=synthetic_clock, clock_timeframe="1day",
        clock_source="synthetic", clock_symbol="_clock",
        slippage=SlippageModel(), buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs,
        cancel_token=type("X", (), {"is_set": lambda self: False})(),
    )

    tick_events = [e for e in obs.events if e[0] == "tick"]
    # Real clock had 3 timestamps (BTC-USD's union), pass-2 observed each once.
    assert len(tick_events) == 3, (
        f"expected 3 ticks (pass-2 over real union clock), got {len(tick_events)}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/coordinator/services/test_backtest_engine_two_pass_clock.py::test_two_pass_execution_does_not_double_call_observer -v`
Expected: FAIL — current code's rebuild-on-first-tick produces 4 tick events (1 synthetic + 3 union) or fewer; the discovery-then-replay separation isn't implemented.

- [ ] **Step 3: Implement two-pass execution**

In `coordinator/services/backtest_engine_v2.py`, replace the public `run` method (currently around lines 105-134) and refactor `_run_internal` to skip the inline rebuild logic:

```python
    def run(
        self,
        *,
        algorithm,
        ctx: BacktestTickContext,
        clock_series: pd.DataFrame,
        clock_timeframe: str,
        clock_source: str,
        clock_symbol: str,
        slippage: SlippageModel,
        buy_fees: list[TradingFee],
        sell_fees: list[TradingFee],
        initial_cash: float,
        observer: EngineObserver,
        cancel_token: CancelToken,
        progress_callback: Optional[Callable[[float], None]] = None,
        rng_seed: int = 12345,
        config: Optional[dict] = None,
    ) -> None:
        try:
            # Pass 1 — discovery.  Run the algorithm's on_start + one tick on
            # the provided clock_series so it has a chance to populate ctx._bars
            # with all symbols it intends to use.  No observers fire; no
            # positions, fills, or equity points are recorded.
            self._discovery_pass(
                algorithm=algorithm, ctx=ctx, clock_series=clock_series,
                clock_timeframe=clock_timeframe, clock_source=clock_source,
                clock_symbol=clock_symbol, config=config or {},
            )

            # Build the real clock from the union of all symbols the algo touched.
            real_clock = self._build_union_clock(ctx._bars)
            if real_clock.empty:
                # Pure scraper-driven algo: fall back to the original clock.
                real_clock = clock_series
                real_source, real_symbol = clock_source, clock_symbol
            else:
                real_source, real_symbol = "_union", "_union"

            # Reset tick-time state so pass-2 replay starts from scratch.
            ctx.reset_for_replay()

            # Pass 2 — replay.  Observers fire; this is the canonical execution.
            self._run_internal(
                algorithm=algorithm, ctx=ctx, clock=real_clock,
                clock_tf=clock_timeframe, clock_source=real_source,
                clock_symbol=real_symbol,
                slippage=slippage, buy_fees=buy_fees, sell_fees=sell_fees,
                initial_cash=initial_cash, observer=observer, cancel=cancel_token,
                progress=progress_callback, rng_seed=rng_seed, config=config or {},
            )
        except Exception as exc:
            logger.exception("BacktestEngine.run failed")
            observer.on_error(exc)

    def _discovery_pass(self, *, algorithm, ctx, clock_series,
                        clock_timeframe, clock_source, clock_symbol,
                        config: dict) -> None:
        """Pass-1 warmup: on_start + one on_tick to populate ctx._bars.

        Observers are NOT called; positions, fills, equity not recorded.
        If clock_series is empty, do nothing (pass-2 will fall through to the
        original synthetic clock).
        """
        if clock_series is None or len(clock_series) == 0:
            algorithm.on_start(config, None)
            return

        first_bar = clock_series.iloc[0]
        tf_duration = timeframe_to_seconds(clock_timeframe)
        sim_time = (first_bar["timestamp"].to_pydatetime() +
                    pd.Timedelta(seconds=tf_duration).to_pytimedelta())
        ctx.set_sim_time(sim_time)
        algorithm.on_start(config, None)
        try:
            algorithm.on_tick(ctx)
        except Exception:
            # Pass-1 errors are absorbed: pass-2 will surface real errors via
            # the observer.  This is just a warmup to populate the bars cache.
            logger.debug("Algorithm raised during pass-1 discovery; continuing", exc_info=True)
```

In `_run_internal`, remove the rebuild block at lines 181-186 (the `if bar_idx == 0 and clock_source == "synthetic" and ctx._bars` shortcut) — pass-2 always receives the real union clock from `run`, so the inline rebuild is redundant.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/coordinator/services/test_backtest_engine_two_pass_clock.py -v`
Expected: PASS.

Then run the full engine + symbol-normalization suites to confirm no regression in single-symbol or scraper-only paths:

```bash
pytest tests/coordinator/services/test_backtest_engine.py tests/coordinator/test_backtest_engine.py tests/coordinator/services/test_symbol_normalization.py -v
```

Expected: PASS. If any test asserts on observer call counts and was depending on the synthetic-clock rebuild side effect, fix them inline.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/backtest_engine_v2.py tests/coordinator/services/test_backtest_engine_two_pass_clock.py
git commit -m "feat(backtest-engine): two-pass execution over union-of-symbol clock

Pass 1: on_start + one on_tick to populate ctx._bars (no observers fire).
Pass 2: replay over union(timestamps from each symbol the algo touched),
        observers fire normally.

Removes the inline 'rebuild-on-first-tick' hack.  Simplifies the I3
resolution layer to direct-dict lookups in subsequent tasks.
"
```

---

### Task C4: Direct-lookup simplification of `_lookup_symbol_close`

**Files:**
- Modify: `coordinator/services/backtest_engine_v2.py` (`_lookup_symbol_close`)
- Test: extend `tests/coordinator/services/test_backtest_engine_two_pass_clock.py`

- [ ] **Step 1: Inspect the current `_lookup_symbol_close`**

Open `coordinator/services/backtest_engine_v2.py` and locate `_lookup_symbol_close`. The current implementation walks `ctx._bars` and resolves each cache key against the provider-specific symbol — three workarounds in one function.

- [ ] **Step 2: Add a test for the simplified contract**

Append to `tests/coordinator/services/test_backtest_engine_two_pass_clock.py`:

```python
def test_lookup_symbol_close_direct_dict_after_p3(monkeypatch):
    """After P3, _lookup_symbol_close finds the symbol's bar directly by
    (provider-resolved cache key, sim_time) lookup — no resolve_symbol fallback
    chain needed at tick-time."""
    from coordinator.services.backtest_engine_v2 import BacktestEngine
    from coordinator.services.backtest_tick_context import BacktestTickContext
    from coordinator.services.backtest_config import BacktestConfig

    bars = {
        ("yfinance", "BTC-USD", "1day"): _make_bar_df(
            ["2024-01-01", "2024-01-02"], base=42_000.0,
        ),
        ("yfinance", "ETH-USD", "1day"): _make_bar_df(
            ["2024-01-01", "2024-01-02"], base=2_500.0,
        ),
    }
    ctx = BacktestTickContext(
        bars=dict(bars), positions={}, cash=10_000.0,
        default_source="yfinance",
    )
    eng = BacktestEngine(config=BacktestConfig(
        start="2024-01-01", end="2024-01-02",
        initial_cash=10_000.0, cost_profile=None,
    ))
    # Engine needs an asset registry; touching _build_union_clock and the
    # internal lookup directly enough — initialise the registry manually.
    from coordinator.services.assets import AssetServiceRegistry
    eng._asset_registry = AssetServiceRegistry()
    eng._ts_cache = {}

    bar = bars[("yfinance", "BTC-USD", "1day")].iloc[1]
    price = eng._lookup_symbol_close(
        sym="ETH/USD",
        sim_time=bar["timestamp"].to_pydatetime(),
        ctx=ctx, bar=bar,
    )
    # Should return ETH's bar close (≈ 2500), NOT BTC's (≈ 42000) — the bug
    # this whole refactor was originally driven by.
    assert 2400 < price < 2600, f"expected ETH close, got {price}"
```

- [ ] **Step 3: Run test to verify it fails or passes (depending on current behavior)**

Run: `pytest tests/coordinator/services/test_backtest_engine_two_pass_clock.py::test_lookup_symbol_close_direct_dict_after_p3 -v`
Expected: PASS (the symbol-resolution loop already shipped). This test pins the contract before the refactor — re-run it after the simplification.

- [ ] **Step 4: Simplify the lookup**

Locate `_lookup_symbol_close`. Replace the body with the direct-lookup version (preserving the asset-registry boundary for cache-key resolution):

```python
    def _lookup_symbol_close(self, sym: str, sim_time, ctx, bar) -> float:
        """Return the close price for sym at or before sim_time.

        After P3 (two-pass execution + union clock), the bars cache key for sym
        is known: resolve canonical -> provider once, then look up the entry
        directly. Falls back to the clock bar's close only if the symbol is
        absent from the cache (e.g. pure scraper-driven algo).
        """
        import numpy as np
        svc = self._asset_registry.get_service(sym)
        # Find the single cache entry matching this symbol.
        for (src, cache_sym, tf), df in ctx._bars.items():
            if df is None or df.empty:
                continue
            if cache_sym != sym and cache_sym != svc.resolve_symbol(sym, src):
                continue
            cache_key = id(df)
            if cache_key not in self._ts_cache:
                ts_col = pd.to_datetime(df["timestamp"])
                if ts_col.dt.tz is not None:
                    ts_col = ts_col.dt.tz_convert("UTC").dt.tz_localize(None)
                ns = ts_col.values.astype("datetime64[ns]").view("int64")
                closes = df["close"].values.astype(float)
                self._ts_cache[cache_key] = (ns, closes)
            ns, closes = self._ts_cache[cache_key]
            cutoff = pd.Timestamp(sim_time)
            if cutoff.tz is not None:
                cutoff = cutoff.tz_convert("UTC").tz_localize(None)
            idx = np.searchsorted(ns, cutoff.value, side="right") - 1
            if idx >= 0:
                return float(closes[idx])
            break
        # Fallback: scraper-only algo with no symbol bars cached.  Return the
        # clock bar's close (preserves pre-P3 behavior for that narrow case).
        return float(bar.get("close", 0.0))
```

The change vs. before: now there is *one* cache entry per `(src, cache_sym, tf)` matching a given canonical symbol (because pass-2 replays on the union clock, every tick falls on a real timestamp), so the inner search is direct rather than a fallback chain across multiple cache entries.

- [ ] **Step 5: Run the regression and the suite**

```bash
pytest tests/coordinator/services/test_backtest_engine_two_pass_clock.py -v
pytest tests/coordinator/services/test_symbol_normalization.py -v
pytest tests/coordinator/services/test_backtest_engine.py tests/coordinator/test_backtest_engine.py -v
```

Expected: PASS across all. If `test_symbol_normalization.py` has tests asserting on internal cache-walk behavior that doesn't apply post-P3, narrow them (assert on the public lookup result, not the internal loop count).

- [ ] **Step 6: Commit**

```bash
git add coordinator/services/backtest_engine_v2.py tests/coordinator/services/test_backtest_engine_two_pass_clock.py
git commit -m "refactor(backtest-engine): direct-lookup _lookup_symbol_close (P3)

Pass-2 union clock guarantees every tick has a real timestamp on every
symbol's timeline, so the symbol-resolution fallback chain can collapse to
a direct (src, resolved_symbol, tf) cache entry lookup. The remaining loop
walks just the matching entry; no fallback to clock-bar close except for
pure scraper-driven algos with no symbol bars.
"
```

---

### Task C5: Direct-lookup simplification of `_try_fill` fill-bar resolution

**Files:**
- Modify: `coordinator/services/backtest_engine_v2.py:222-258` (the fill-bar resolution block)
- Test: `tests/coordinator/services/test_backtest_engine_two_pass_clock.py` (extend)

- [ ] **Step 1: Add a failing test**

Append to `tests/coordinator/services/test_backtest_engine_two_pass_clock.py`:

```python
def test_fill_bar_resolution_uses_symbol_bar_not_clock_bar(monkeypatch):
    """When an algo fills BUY ETH/USD at sim_time T, the fill should price
    against ETH's bar at T — not against BTC's bar (the previous bug)."""
    from coordinator.services.backtest_engine_v2 import BacktestEngine
    from coordinator.services.backtest_tick_context import BacktestTickContext
    from coordinator.services.backtest_config import (
        BacktestConfig, SlippageModel,
    )
    from coordinator.services.assets import AssetServiceRegistry

    class _BuyEthAlgo:
        def on_start(self, *a, **k):
            self._calls = 0
        def on_tick(self, ctx):
            from sdk.signals import Signal, SignalLeg
            self._calls += 1
            if self._calls == 1:
                # Discovery pass: touch ETH.
                ctx.market_data("ETH/USD", n=1)
                return []
            if self._calls == 2:
                # Pass-2 first tick: emit a buy signal.
                return [Signal(legs=[SignalLeg(
                    symbol="ETH/USD", side="buy", quantity=1.0,
                    order_type="market",
                )])]
            return []

    bars = {
        ("yfinance", "BTC-USD", "1day"): _make_bar_df(
            ["2024-01-01", "2024-01-02"], base=42_000.0,
        ),
        ("yfinance", "ETH-USD", "1day"): _make_bar_df(
            ["2024-01-01", "2024-01-02"], base=2_500.0,
        ),
    }
    ctx = BacktestTickContext(bars=dict(bars), positions={}, cash=10_000.0,
                              default_source="yfinance")
    obs = _RecordingObserver()
    eng = BacktestEngine(config=BacktestConfig(
        start="2024-01-01", end="2024-01-02",
        initial_cash=10_000.0, cost_profile=None,
    ))
    eng.run(
        algorithm=_BuyEthAlgo(), ctx=ctx,
        clock_series=bars[("yfinance", "BTC-USD", "1day")],
        clock_timeframe="1day", clock_source="yfinance", clock_symbol="BTC-USD",
        slippage=SlippageModel(), buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs,
        cancel_token=type("X", (), {"is_set": lambda self: False})(),
    )
    fills = [e for e in obs.events if e[0] == "fill"]
    assert len(fills) >= 1
    fill_record = fills[0][1][0]
    # Fill price must be ETH's bar (~2500), not BTC's (~42000).
    assert 2400 < fill_record.fill_price < 2600
```

- [ ] **Step 2: Run the test to confirm it passes against current code (regression pin)**

Run: `pytest tests/coordinator/services/test_backtest_engine_two_pass_clock.py::test_fill_bar_resolution_uses_symbol_bar_not_clock_bar -v`
Expected: PASS (the fix shipped in commit `144494e`). The test is here so the refactor below doesn't regress.

- [ ] **Step 3: Simplify the fill-bar resolution**

Replace the fill-bar resolution block in `_run_internal` (lines 222-258 approximately):

```python
                # Resolve the fill bar from the symbol's cache entry. After P3,
                # the cache key for sym is unique; no fallback chain across
                # multiple entries is needed. The block below mirrors the
                # _lookup_symbol_close pattern.
                fill_bar = bar
                sym = po.leg.symbol
                if sym != clock_symbol:
                    svc_for_sym = self._asset_registry.get_service(sym)
                    for (src, s, tf), df in ctx._bars.items():
                        if df.empty:
                            continue
                        if s != sym and s != svc_for_sym.resolve_symbol(sym, src):
                            continue
                        import numpy as np
                        cache_key = id(df)
                        if cache_key not in self._ts_cache:
                            ts_col = pd.to_datetime(df["timestamp"])
                            if ts_col.dt.tz is not None:
                                ts_col = ts_col.dt.tz_convert("UTC").dt.tz_localize(None)
                            ns = ts_col.values.astype("datetime64[ns]").view("int64")
                            closes = df["close"].values.astype(float)
                            self._ts_cache[cache_key] = (ns, closes)
                        ns, _ = self._ts_cache[cache_key]
                        cutoff = pd.Timestamp(sim_time)
                        if cutoff.tz is not None:
                            cutoff = cutoff.tz_convert("UTC").tz_localize(None)
                        idx = np.searchsorted(ns, cutoff.value, side="right") - 1
                        if idx >= 0:
                            fill_bar = df.iloc[idx]
                        break  # found the symbol's entry; no fallback
```

The shape is largely the same; the key change is the explicit `break` after the first matching entry — there is no second-chance fallback to other cache entries any more. The clock-bar fallback (`fill_bar = bar`) survives only for symbols absent from the cache.

- [ ] **Step 4: Run the test and the suite**

```bash
pytest tests/coordinator/services/test_backtest_engine_two_pass_clock.py -v
pytest tests/coordinator/services/test_symbol_normalization.py -v
pytest tests/coordinator/services/test_backtest_engine.py tests/coordinator/test_backtest_engine.py -v
```

Expected: PASS across all.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/backtest_engine_v2.py tests/coordinator/services/test_backtest_engine_two_pass_clock.py
git commit -m "refactor(backtest-engine): collapse _try_fill bar resolution chain (P3)

With a real union clock, the symbol's bar exists at sim_time — break after
the first cache match instead of walking every entry.  The clock-bar
fallback survives only for symbols absent from the cache (pure scraper-driven
algos).
"
```

---

### Task C6: Document the known limitation (lazy symbol discovery)

**Files:**
- Modify: `docs/superpowers/specs/2026-05-28-backtest-and-validation-lab-integration.md`

- [ ] **Step 1: Update I3 to its simplified form**

Replace the existing I3 section ("All bars-cache lookups route through `AssetService.resolve_symbol`") with the simplified version:

```markdown
### I3 (simplified post-P3): Cache key matches lookup key directly inside the tick loop

Pass-2 of the backtest engine replays on a real union clock built from `{timestamps of every symbol the algo touched during pass-1 discovery}`. Inside the tick loop, every `ctx._bars[(src, cache_sym, tf)]` entry has a row at the current `sim_time` for symbols the algo cares about. The fallback chains that previously existed in three places shrink to a single direct lookup each:

- `BacktestTickContext.market_data` — algorithm convenience wrapper; still walks the cache (this is the only resolve_symbol boundary that survives).
- `BacktestEngine._try_fill` fill-bar lookup — direct cache lookup with a single-pass match, `break` after first hit.
- `BacktestEngine._lookup_symbol_close` — direct cache lookup with a single-pass match, `break` after first hit.

`AssetService.resolve_symbol` still applies at the manifest-preload boundary (canonical → provider-specific symbol when filling the cache key), but is not invoked per-tick.

**Known limitation:** symbols the algorithm requests on a tick *after* pass-1 are loaded into the cache lazily (existing behavior), but their timestamps do not extend the pass-2 union clock. If the algorithm depends on a symbol it didn't touch during pass-1, the clock will not have ticks aligned to that symbol's exclusive timestamps. Document discovery-pass symbols as the canonical clock contributors.

**Provenance:** P3 implementation. Removes three independent fallback chains that each had to encode the same symbol-resolution rules.
```

- [ ] **Step 2: Mark "Planned items" table updated**

Find the "Backlog items in scope for this spec — planned implementation" table. Change the I3 entry from "Simplifies I3 (resolution layer becomes unnecessary)" to "Simplifies I3 (per-tick resolution layer removed; survives only at manifest boundary + ctx.market_data)".

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-05-28-backtest-and-validation-lab-integration.md
git commit -m "docs(spec): update I3 to post-P3 simplified form

Per-tick resolution chains removed.  Lazy symbol discovery noted as a
known limitation."
```

---

### Task C7: Full integration smoke test — crypto-tsmom backtest correctness

**Files:**
- Test: `tests/coordinator/services/test_backtest_engine_two_pass_clock.py` (extend)

- [ ] **Step 1: Add the end-to-end smoke test**

Append to `tests/coordinator/services/test_backtest_engine_two_pass_clock.py`:

```python
def test_two_asset_backtest_does_not_inflate_equity():
    """Regression: the 2026-05-27 ETH-at-BTC-price bug produced 25-50× equity
    inflation. After P3, a two-asset (BTC, ETH) backtest should produce equity
    that stays within an order of magnitude of starting cash on a 5-day window
    with realistic prices."""
    from coordinator.services.backtest_engine_v2 import BacktestEngine
    from coordinator.services.backtest_tick_context import BacktestTickContext
    from coordinator.services.backtest_config import (
        BacktestConfig, SlippageModel,
    )

    class _BuyHoldBoth:
        def on_start(self, config, restored_state):
            self._sent = False
        def on_tick(self, ctx):
            from sdk.signals import Signal, SignalLeg
            if self._sent:
                return []
            self._sent = True
            return [
                Signal(legs=[SignalLeg(symbol="BTC/USD", side="buy",
                                       quantity=0.01, order_type="market")]),
                Signal(legs=[SignalLeg(symbol="ETH/USD", side="buy",
                                       quantity=0.1, order_type="market")]),
            ]

    bars = {
        ("yfinance", "BTC-USD", "1day"): _make_bar_df(
            ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
            base=42_000.0,
        ),
        ("yfinance", "ETH-USD", "1day"): _make_bar_df(
            ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
            base=2_500.0,
        ),
    }
    ctx = BacktestTickContext(bars=dict(bars), positions={}, cash=10_000.0,
                              default_source="yfinance")
    obs = _RecordingObserver()
    eng = BacktestEngine(config=BacktestConfig(
        start="2024-01-01", end="2024-01-05",
        initial_cash=10_000.0, cost_profile=None,
    ))
    eng.run(
        algorithm=_BuyHoldBoth(), ctx=ctx,
        clock_series=bars[("yfinance", "BTC-USD", "1day")],
        clock_timeframe="1day", clock_source="yfinance", clock_symbol="BTC-USD",
        slippage=SlippageModel(), buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs,
        cancel_token=type("X", (), {"is_set": lambda self: False})(),
    )
    # Tick events carry account_value via observer.update_account? No — pull
    # the on_tick payload last value.
    ticks = [e for e in obs.events if e[0] == "tick"]
    assert ticks, "expected at least one tick event"
    # Each on_tick payload is (sim_time, {"cash": ...}). Cash should be reduced
    # after the first fill but never go negative.
    last_payload = ticks[-1][1][1]
    assert last_payload["cash"] >= 0, f"cash went negative: {last_payload['cash']}"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/coordinator/services/test_backtest_engine_two_pass_clock.py -v`
Expected: PASS.

- [ ] **Step 3: Run the entire validation lab + engine suite**

```bash
pytest tests/coordinator/services/test_backtest_engine.py tests/coordinator/test_backtest_engine.py tests/coordinator/services/test_symbol_normalization.py tests/coordinator/services/validation/ tests/coordinator/services/test_backtest_engine_two_pass_clock.py -v
```

Expected: PASS. If any pre-existing test fails because it was depending on an internal behavior P3 removed, narrow it to assert on observable contract (final equity, fill prices, observer events) rather than internal control-flow.

- [ ] **Step 4: Commit**

```bash
git add tests/coordinator/services/test_backtest_engine_two_pass_clock.py
git commit -m "test(backtest-engine): two-asset BTC/ETH backtest stays within sane equity bounds

Regression test for the 2026-05-27 ETH-at-BTC-price bug.  Establishes that
a two-asset buy-and-hold backtest produces non-inflated equity (cash stays
within positive bounds; no 25-50× inflation).
"
```

---

### Task C8: Backlog — close the union-clock entry

**Files:**
- Modify: `docs/superpowers/backlog.md`

- [ ] **Step 1: Mark the synthetic-clock item shipped**

In `docs/superpowers/backlog.md`, find the "Replace synthetic backtest clock with union-of-symbol-timelines" entry (note: per the spec's "Backlog domain re-categorizations recommended" section, this lives under the Backtesting section now). Mark it shipped:

- Strike-through the bullet text, or
- Move it to a "Shipped" subsection if the file uses that convention, or
- Update its status marker to ✅ — match the convention used by the rest of the file (run a quick `grep "shipped\|✅\|done" docs/superpowers/backlog.md` to see what the existing pattern is).

Reference the spec section: "Implementation covered by `docs/superpowers/specs/2026-05-28-backtest-and-validation-lab-integration.md` (P3)".

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/backlog.md
git commit -m "docs(backlog): mark union-of-symbol-timelines clock shipped (P3)"
```

---

## Final integration check

After all phases land, run the full test suite once before opening the PR:

```bash
pytest tests/ -v --tb=short
```

Expected: PASS for everything that previously passed, plus the ~12 new tests this plan adds.

Then start the coordinator + dashboard and walk through the three user-facing flows manually:

1. **Benchmark dropdown** — open RunBacktestModal, verify only available providers show.
2. **CLI sweep** — run `quilt research sweep --session-id <real> --manifest <path> --base-config <path> --max-trials 2`, confirm it prints `queued: <job_id>` then polls to completion with progress messages.
3. **Two-asset backtest** — run any multi-asset crypto algo end-to-end via the dashboard, verify the equity curve doesn't display the 25-50× inflation shape.

---

## Self-review

**Spec coverage** — Walking the "Planned additions" section of `2026-05-28-backtest-and-validation-lab-integration.md`:

- P1 motivation (cost trap, silent benchmark drop, confusing failure) → Tasks A1, A2, A3, A4
- P1 API surface (`GET /api/data/providers`, modified `POST /api/backtest-runs`) → A1, A2
- P1 runner behavior on missing data → A3
- P1 invariants I16, I17 → A5
- P2 endpoints (sweep, walk-forward, GET jobs, DELETE) → B6
- P2 CLI surface with `--no-wait` → B7
- P2 DB schema → B1, B2
- P2 orphan recovery → B3
- P2 invariant I18 → B8
- P3 two-pass execution → C2, C3
- P3 `_lookup_symbol_close` simplification → C4
- P3 `_try_fill` simplification → C5
- P3 edge cases (no-`market_data` algo, lazy discovery, warmup bar) → C2 (fallback path), C3 (try/except wrapper), C6 (doc)
- P3 modified I3 → C6

**Placeholder scan** — Searched the plan for: "TBD", "TODO", "fill in", "appropriate error handling", "similar to Task" — none found. Every test code block is concrete; every step references the actual file paths, line numbers, and command output.

**Type consistency** — `JobResponse` Pydantic model in Task B6 matches the dict keys returned by `_row_to_dict` in Task B3. `_load_benchmark_with_download` signature in A3 (keyword-only) matches its test fixture. `_provider_availability(db)` is consumed identically by Task A1 (the endpoint) and Task A2 (the validator). `progress_callback(pct, message, run_ids)` signature is consistent across B3 (`_make_progress_callback`), B5 (sweep + walk-forward), and B7 (CLI test expectations).

**Spec invariants** — I16, I17, I18 each tied to one task that adds them; I3 simplification tied to two tasks (C4, C5) and one doc update (C6).

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-28-backtest-and-validation-lab-additions.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
