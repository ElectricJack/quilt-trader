# Research Lab Dashboard (Phases 1 + 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the existing Research / Validation Lab a React frontend — sidebar entry, sessions list, session creation, session detail with live job progress, sweep submission, run links, and Generate Report.

**Architecture:** Three small additive backend changes (`manifest_path` field on `/api/algorithms`, `algorithm_id` alt on sweep/walk-forward requests, `on_job_update` callback on `ResearchJobManager` wired to the existing `ConnectionManager.broadcast_to_dashboards` WS channel). On the frontend, two new pages, five new components, and four new query/mutation hooks; the existing `useWebSocketSync` hub gains a `research_job` subscription block.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.x async, pytest + pytest-asyncio, React 18 + Vite + TypeScript, TanStack Query + Table, react-router-dom, lucide-react, TailwindCSS, vitest + @testing-library/react.

**Spec:** [`docs/superpowers/specs/2026-05-30-research-lab-dashboard-design.md`](../specs/2026-05-30-research-lab-dashboard-design.md)

---

## File map

### Created
- `dashboard/src/pages/Research.tsx` — sessions list page
- `dashboard/src/pages/Research.test.tsx`
- `dashboard/src/pages/ResearchSessionDetail.tsx` — session detail page
- `dashboard/src/pages/ResearchSessionDetail.test.tsx`
- `dashboard/src/components/NewSessionModal.tsx`
- `dashboard/src/components/NewSessionModal.test.tsx`
- `dashboard/src/components/NewSweepModal.tsx`
- `dashboard/src/components/NewSweepModal.test.tsx`
- `dashboard/src/components/ResearchJobRow.tsx`
- `dashboard/src/components/ResearchJobRow.test.tsx`
- `dashboard/src/components/ResearchSessionSummary.tsx`
- `dashboard/src/components/JsonTextField.tsx`
- `dashboard/src/components/JsonTextField.test.tsx`
- `dashboard/src/hooks/useResearchSessions.ts`
- `dashboard/src/hooks/useResearchSession.ts`
- `dashboard/src/hooks/useResearchMutations.ts`

### Modified
- `coordinator/api/routes/algorithms.py` — `_algo_to_response` adds `manifest_path`
- `coordinator/api/routes/research.py` — extend `SweepRequest` + `WalkForwardRequest` with `algorithm_id`; resolve in handlers
- `coordinator/services/research_job_manager.py` — `on_job_update` constructor arg; publish on each status / progress transition
- `coordinator/main.py` — pass `on_job_update=_broadcast_research_update` when constructing `ResearchJobManager`
- `dashboard/src/api/client.ts` — research types + endpoints
- `dashboard/src/api/hooks.ts` — `keys.researchSessions()`, `keys.researchSession(id)`, `keys.researchJobs(id)`, `keys.researchJob(sid, jid)`
- `dashboard/src/hooks/useWebSocketSync.ts` — add `research_job` subscription block
- `dashboard/src/components/Layout.tsx` — sidebar nav entry (between Backtests and Settings)
- `dashboard/src/App.tsx` — `/research` and `/research/sessions/:id` routes
- `tests/coordinator/api/test_algorithms_routes.py` — assert `manifest_path` field
- `tests/coordinator/api/test_research_routes.py` — assert `algorithm_id` resolution + 422/404 cases
- `tests/coordinator/services/test_research_job_manager.py` — assert `on_job_update` invocation

---

## Task 1: Add `manifest_path` to `/api/algorithms` response

**Files:**
- Modify: `coordinator/api/routes/algorithms.py:147` — `_algo_to_response`
- Modify: `tests/coordinator/api/test_algorithms_routes.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/coordinator/api/test_algorithms_routes.py`. (If a test file matching that name doesn't exist, find the existing algorithms test file via `ls tests/coordinator/api/ | grep algorithm` and append there. The shape should match the file's existing test fixtures and helpers.)

```python
import pytest


@pytest.mark.asyncio
async def test_list_algorithms_includes_manifest_path(test_client, seeded_algorithm):
    """The /api/algorithms response surface includes manifest_path so the
    dashboard's sweep form can pre-fill it after the user picks an algorithm.
    """
    resp = await test_client.get("/api/algorithms")
    assert resp.status_code == 200
    body = resp.json()
    algo = next(a for a in body if a["id"] == seeded_algorithm.id)
    assert "manifest_path" in algo
    # source_path/quilt.yaml is the convention. Both paths come back as strings.
    if algo["source_path"]:
        assert algo["manifest_path"] == f"{algo['source_path']}/quilt.yaml"
    else:
        assert algo["manifest_path"] is None


@pytest.mark.asyncio
async def test_manifest_path_is_null_when_source_path_is_null(test_client, orphan_algorithm):
    """An algorithm row without a source_path returns manifest_path=null
    rather than throwing or producing 'None/quilt.yaml'."""
    resp = await test_client.get(f"/api/algorithms/{orphan_algorithm.id}")
    assert resp.status_code == 200
    assert resp.json()["manifest_path"] is None
```

Add minimal fixtures if missing. `seeded_algorithm` should be a freshly inserted `Algorithm` row with a non-null `source_path`; `orphan_algorithm` is the same with `source_path=None`. Look at any existing fixture in the file for the construction pattern, then add these two near them.

- [ ] **Step 2: Run tests to verify they fail**

```
python3 -m pytest tests/coordinator/api/test_algorithms_routes.py::test_list_algorithms_includes_manifest_path tests/coordinator/api/test_algorithms_routes.py::test_manifest_path_is_null_when_source_path_is_null -v
```

Expected: 2 failures (KeyError on `"manifest_path"`).

- [ ] **Step 3: Implement**

Edit `coordinator/api/routes/algorithms.py:147` — extend `_algo_to_response`:

```python
def _algo_to_response(algo: Algorithm) -> dict:
    return {
        "id": algo.id,
        "repo_url": algo.repo_url,
        "source_path": algo.source_path,
        "manifest_path": (
            f"{algo.source_path}/quilt.yaml"
            if algo.source_path else None
        ),
        "name": algo.name,
        "description": algo.description,
        "version": algo.version,
        "commit_hash": algo.commit_hash,
        "required_asset_types": algo.required_asset_types,
        "required_options_level": algo.required_options_level,
        "required_account_features": algo.required_account_features,
        "supported_brokers": algo.supported_brokers,
        "data_dependencies": algo.assets,
        "config_schema": algo.config_schema,
        "custom_events": algo.custom_events,
        "install_status": algo.install_status,
        "install_error": algo.install_error,
        "installed_at": to_iso_utc(algo.installed_at),
        "updated_at": to_iso_utc(algo.updated_at),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```
python3 -m pytest tests/coordinator/api/test_algorithms_routes.py -v
```

Expected: all tests pass (the two new ones plus existing ones).

- [ ] **Step 5: Commit**

```
git add coordinator/api/routes/algorithms.py tests/coordinator/api/test_algorithms_routes.py
git commit -m "feat(algorithms-api): expose manifest_path in /api/algorithms response

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Accept `algorithm_id` on sweep + walk-forward requests

**Files:**
- Modify: `coordinator/api/routes/research.py` — `SweepRequest`, `WalkForwardRequest`, the two POST handlers
- Modify: `tests/coordinator/api/test_research_routes.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/coordinator/api/test_research_routes.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_sweep_accepts_algorithm_id_and_resolves_manifest(
    test_client, seeded_session, seeded_algorithm
):
    resp = await test_client.post(
        f"/api/research/sessions/{seeded_session.id}/sweep",
        json={
            "algorithm_id": seeded_algorithm.id,
            "base_config": {},
            "search": "grid",
            "max_trials": 5,
        },
    )
    assert resp.status_code == 202, resp.text
    job = resp.json()
    # Resolved manifest_path should land in request_payload for the manager
    assert job["request_payload"]["manifest_path"] == \
        f"{seeded_algorithm.source_path}/quilt.yaml"
    assert "algorithm_id" not in job["request_payload"]


@pytest.mark.asyncio
async def test_sweep_rejects_both_manifest_and_algorithm_id(
    test_client, seeded_session
):
    resp = await test_client.post(
        f"/api/research/sessions/{seeded_session.id}/sweep",
        json={
            "manifest_path": "/some/path/quilt.yaml",
            "algorithm_id": "abc123",
            "base_config": {},
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_sweep_rejects_neither_manifest_nor_algorithm_id(
    test_client, seeded_session
):
    resp = await test_client.post(
        f"/api/research/sessions/{seeded_session.id}/sweep",
        json={"base_config": {}},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_sweep_rejects_unknown_algorithm_id(test_client, seeded_session):
    resp = await test_client.post(
        f"/api/research/sessions/{seeded_session.id}/sweep",
        json={
            "algorithm_id": "no-such-algorithm",
            "base_config": {},
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_walk_forward_accepts_algorithm_id(
    test_client, seeded_session, seeded_algorithm
):
    resp = await test_client.post(
        f"/api/research/sessions/{seeded_session.id}/walk-forward",
        json={
            "algorithm_id": seeded_algorithm.id,
            "base_config": {},
        },
    )
    assert resp.status_code == 202, resp.text
    job = resp.json()
    assert job["request_payload"]["manifest_path"] == \
        f"{seeded_algorithm.source_path}/quilt.yaml"
```

Add fixtures `seeded_session` (creates an `OptimizationSession` row) and `seeded_algorithm` if not present.

Important: the existing route may not include `request_payload` in `JobResponse`. If the assertion path `job["request_payload"]["manifest_path"]` is unreachable through the response, change the test to fetch the job row directly via SQLAlchemy from the test DB session and assert against `ResearchJob.request_payload`. Read `coordinator/database/models.py` near `ResearchJob` to see if `request_payload` is a stored column (the spec says yes).

- [ ] **Step 2: Run tests to verify they fail**

```
python3 -m pytest tests/coordinator/api/test_research_routes.py -v -k "algorithm_id or rejects"
```

Expected: all 5 new tests fail (current `SweepRequest` requires `manifest_path`, doesn't know `algorithm_id`).

- [ ] **Step 3: Implement — update request models + handlers**

Edit `coordinator/api/routes/research.py`:

```python
# Imports — add near the top
from typing import Literal
from pydantic import model_validator
from coordinator.database.models import Algorithm  # may already be imported


# Replace existing SweepRequest
class SweepRequest(BaseModel):
    # Provide EITHER manifest_path OR algorithm_id (resolved server-side).
    manifest_path: str | None = None
    algorithm_id: str | None = None
    base_config: dict
    parameter_space: dict | None = None
    search: Literal["grid", "random", "latin", "tpe"] = "grid"
    max_trials: int = 50
    parallelism: int = 1
    seed: int = 0

    @model_validator(mode="after")
    def _exactly_one_of_manifest_or_algorithm(self):
        if (self.manifest_path is None) == (self.algorithm_id is None):
            raise ValueError(
                "provide exactly one of manifest_path or algorithm_id"
            )
        return self


# Replace existing WalkForwardRequest
class WalkForwardRequest(BaseModel):
    manifest_path: str | None = None
    algorithm_id: str | None = None
    base_config: dict
    parameter_space: dict | None = None
    train_years: float = 4.0
    test_years: float = 1.0
    step_months: float = 6.0
    objective: Literal["sharpe", "calmar", "sortino"] = "sharpe"
    parallelism: int = 1

    @model_validator(mode="after")
    def _exactly_one_of_manifest_or_algorithm(self):
        if (self.manifest_path is None) == (self.algorithm_id is None):
            raise ValueError(
                "provide exactly one of manifest_path or algorithm_id"
            )
        return self
```

Add a resolver helper near the top of the file:

```python
async def _resolve_manifest_path(
    db: AsyncSession,
    *,
    manifest_path: str | None,
    algorithm_id: str | None,
) -> str:
    """Return manifest_path either as-given or resolved from an algorithm_id.

    Caller is responsible for ensuring exactly one of the two is set (the
    Pydantic validator enforces that).
    """
    if manifest_path is not None:
        return manifest_path
    algo = (
        await db.execute(select(Algorithm).where(Algorithm.id == algorithm_id))
    ).scalar_one_or_none()
    if algo is None:
        raise HTTPException(404, f"unknown algorithm: {algorithm_id}")
    if not algo.source_path:
        raise HTTPException(
            400,
            f"algorithm {algorithm_id} has no source_path; cannot resolve manifest",
        )
    return f"{algo.source_path}/quilt.yaml"
```

Find the existing `POST .../sweep` handler and update it to call this resolver. Pseudo-locator: search for `SweepRequest` usage in handler signatures. The change is:

```python
# Inside the sweep POST handler, AFTER the request body is parsed
# and BEFORE building the request_payload for ResearchJobManager:
resolved_manifest_path = await _resolve_manifest_path(
    db,
    manifest_path=req.manifest_path,
    algorithm_id=req.algorithm_id,
)
request_payload = {
    "manifest_path": resolved_manifest_path,
    "base_config": req.base_config,
    "parameter_space": req.parameter_space,
    "search": req.search,
    "max_trials": req.max_trials,
    "parallelism": req.parallelism,
    "seed": req.seed,
}
job_id = await container.research_job_manager.create_sweep_job(
    session_id=session_id,
    request_payload=request_payload,
)
```

Apply the symmetric change to the walk-forward handler.

- [ ] **Step 4: Run all research route tests**

```
python3 -m pytest tests/coordinator/api/test_research_routes.py -v
```

Expected: all tests pass — the 5 new ones AND the existing manifest_path tests (the validator still accepts the old shape because `algorithm_id` defaults to None).

- [ ] **Step 5: Commit**

```
git add coordinator/api/routes/research.py tests/coordinator/api/test_research_routes.py
git commit -m "feat(research-api): accept algorithm_id alt to manifest_path on sweep/wf

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `on_job_update` callback on `ResearchJobManager`

**Files:**
- Modify: `coordinator/services/research_job_manager.py`
- Modify: `tests/coordinator/services/test_research_job_manager.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/coordinator/services/test_research_job_manager.py`:

```python
import asyncio
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_on_job_update_called_when_marking_running(db_session_factory):
    """When the manager flips a job to running, the on_job_update callback
    fires with the post-update row payload."""
    on_update = AsyncMock(return_value=None)
    mgr = ResearchJobManager(
        session_factory=db_session_factory,
        sweep_fn=AsyncMock(return_value=None),
        walk_forward_fn=AsyncMock(return_value=None),
        runner_factory=AsyncMock(return_value=None),
        sync_session_factory=None,
        on_job_update=on_update,
    )
    # Seed a session + queued job directly via DB
    session_id = await _seed_session(db_session_factory)
    job_id = await _seed_queued_job(db_session_factory, session_id, kind="sweep")

    await mgr._mark_running(job_id)

    on_update.assert_awaited()
    payload = on_update.call_args[0][0]
    assert payload["job_id"] == job_id
    assert payload["session_id"] == session_id
    assert payload["status"] == "running"


@pytest.mark.asyncio
async def test_on_job_update_called_on_terminal_status(db_session_factory):
    on_update = AsyncMock(return_value=None)
    mgr = ResearchJobManager(
        session_factory=db_session_factory,
        sweep_fn=AsyncMock(),
        walk_forward_fn=AsyncMock(),
        runner_factory=AsyncMock(),
        sync_session_factory=None,
        on_job_update=on_update,
    )
    session_id = await _seed_session(db_session_factory)
    job_id = await _seed_queued_job(db_session_factory, session_id, kind="sweep")

    await mgr._mark_terminal(job_id, "completed")

    on_update.assert_awaited()
    payload = on_update.call_args[0][0]
    assert payload["status"] == "completed"
    assert payload["completed_at"] is not None
    assert payload["progress_pct"] == 1.0


@pytest.mark.asyncio
async def test_on_job_update_called_from_progress_callback(db_session_factory):
    """The progress callback fired per-trial also publishes."""
    on_update = AsyncMock(return_value=None)
    mgr = ResearchJobManager(
        session_factory=db_session_factory,
        sweep_fn=AsyncMock(),
        walk_forward_fn=AsyncMock(),
        runner_factory=AsyncMock(),
        sync_session_factory=None,
        on_job_update=on_update,
    )
    session_id = await _seed_session(db_session_factory)
    job_id = await _seed_queued_job(db_session_factory, session_id, kind="sweep")
    cancel_flag = asyncio.Event()

    cb = mgr._make_progress_callback(job_id, cancel_flag)  # see Step 3 — promoted to method
    await cb(0.5, "trial 5 / 10", ["run-1", "run-2"])

    on_update.assert_awaited()
    payload = on_update.call_args[0][0]
    assert payload["progress_pct"] == 0.5
    assert payload["progress_message"] == "trial 5 / 10"
    assert payload["run_ids"] == ["run-1", "run-2"]


@pytest.mark.asyncio
async def test_on_job_update_exception_is_logged_not_swallowed(db_session_factory, caplog):
    """If the broadcaster raises, the commit still succeeds and the error
    is logged. The job's DB state is the source of truth, not the broadcast.
    """
    async def broken_on_update(payload):
        raise RuntimeError("ws broadcaster boom")

    mgr = ResearchJobManager(
        session_factory=db_session_factory,
        sweep_fn=AsyncMock(),
        walk_forward_fn=AsyncMock(),
        runner_factory=AsyncMock(),
        sync_session_factory=None,
        on_job_update=broken_on_update,
    )
    session_id = await _seed_session(db_session_factory)
    job_id = await _seed_queued_job(db_session_factory, session_id, kind="sweep")

    # Should NOT raise — exception is logged
    await mgr._mark_running(job_id)

    assert "broadcaster" in caplog.text.lower() or "ws" in caplog.text.lower()
    # The job's status was still updated in the DB
    async with db_session_factory() as s:
        row = await s.get(ResearchJob, job_id)
        assert row.status == "running"


@pytest.mark.asyncio
async def test_on_job_update_optional(db_session_factory):
    """Constructing without on_job_update still works (CLI path)."""
    mgr = ResearchJobManager(
        session_factory=db_session_factory,
        sweep_fn=AsyncMock(),
        walk_forward_fn=AsyncMock(),
        runner_factory=AsyncMock(),
        sync_session_factory=None,
        # no on_job_update kwarg
    )
    session_id = await _seed_session(db_session_factory)
    job_id = await _seed_queued_job(db_session_factory, session_id, kind="sweep")
    await mgr._mark_running(job_id)  # must not raise
```

Add helpers if not already in the file (`_seed_session`, `_seed_queued_job`). These insert minimal rows directly via the async session factory and return the IDs.

- [ ] **Step 2: Run tests to verify they fail**

```
python3 -m pytest tests/coordinator/services/test_research_job_manager.py -v -k "on_job_update"
```

Expected: 5 failures (no `on_job_update` kwarg yet, no broadcast wiring).

- [ ] **Step 3: Implement**

Edit `coordinator/services/research_job_manager.py`:

```python
# Imports — add Callable / Awaitable
from typing import Awaitable, Callable

# Update __init__
class ResearchJobManager:
    def __init__(
        self,
        *,
        session_factory,
        sweep_fn,
        walk_forward_fn,
        runner_factory,
        sync_session_factory=None,
        on_job_update: Callable[[dict], Awaitable[None]] | None = None,
    ):
        self._sf = session_factory
        self._sweep_fn = sweep_fn
        self._wf_fn = walk_forward_fn
        self._runner_factory = runner_factory
        self._sync_sf = sync_session_factory
        self._on_job_update = on_job_update
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._cancel_flags: dict[str, asyncio.Event] = {}

    async def _publish_update(self, job_id: str) -> None:
        """Load the row's current state and invoke the broadcaster, if any.
        Exceptions in the broadcaster are logged but never re-raised — the
        DB row is the source of truth; the broadcast is a courtesy."""
        if self._on_job_update is None:
            return
        async with self._sf() as s:
            row = (await s.execute(
                select(ResearchJob).where(ResearchJob.id == job_id)
            )).scalar_one_or_none()
            if row is None:
                return
            payload = _row_to_dict(row)
        try:
            await self._on_job_update(payload)
        except Exception:
            logger.exception("ws broadcaster raised for research_job %s", job_id)
```

Update `_mark_running` to publish after commit:

```python
    async def _mark_running(self, job_id: str) -> None:
        async with self._sf() as s:
            row = (await s.execute(select(ResearchJob).where(ResearchJob.id == job_id))).scalar_one()
            if row.status == "cancelled":
                raise asyncio.CancelledError()
            row.status = "running"
            row.started_at = datetime.now(timezone.utc)
            await s.commit()
        await self._publish_update(job_id)
```

Update `_mark_terminal` to publish after commit:

```python
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
        await self._publish_update(job_id)
```

Promote the module-level `_make_progress_callback` to a method so it can access `self._publish_update`:

```python
    def _make_progress_callback(self, job_id: str, cancel_flag: asyncio.Event):
        """Returns a callable invoked after each completed trial / fold.
        Signature: (pct: float, message: str, run_ids: list[str])."""
        async def cb(pct: float, message: str, run_ids: list[str]) -> None:
            if cancel_flag.is_set():
                raise asyncio.CancelledError()
            async with self._sf() as s:
                row = (await s.execute(
                    select(ResearchJob).where(ResearchJob.id == job_id)
                )).scalar_one_or_none()
                if row is None:
                    return
                row.progress_pct = pct
                row.progress_message = message
                if run_ids:
                    row.run_ids = (row.run_ids or []) + run_ids
                await s.commit()
            await self._publish_update(job_id)
        return cb
```

And update the existing call site in `_run_job`:

```python
        progress_cb = self._make_progress_callback(job_id, cancel_flag)
```

(was `_make_progress_callback(self._sf, job_id, cancel_flag)` — the standalone function can be removed since nothing else uses it.)

- [ ] **Step 4: Run tests to verify they pass**

```
python3 -m pytest tests/coordinator/services/test_research_job_manager.py -v
```

Expected: all tests pass — the 5 new ones AND all pre-existing tests (no behavior change when `on_job_update=None`).

- [ ] **Step 5: Commit**

```
git add coordinator/services/research_job_manager.py tests/coordinator/services/test_research_job_manager.py
git commit -m "feat(research-mgr): on_job_update callback for live status broadcast

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Wire WS broadcast in coordinator lifespan

**Files:**
- Modify: `coordinator/main.py` (around line 484, where `ResearchJobManager` is constructed)

- [ ] **Step 1: Smoke test plan (no automated test for this task)**

This is a pure wiring task. The automated coverage comes from Task 3 (callback invoked) and the existing WS smoke tests. We verify by hand at the end.

- [ ] **Step 2: Implement**

Edit `coordinator/main.py` around line 473-490:

```python
        from coordinator.services.research_job_manager import ResearchJobManager
        from coordinator.services.validation.sweep import run_sweep as _run_sweep_fn
        from coordinator.services.validation.walk_forward import run_walk_forward as _run_walk_forward_fn
        from coordinator.database.session import get_session_factory as _get_sync_session_factory
        from coordinator.api.websocket import manager as _ws_manager

        async def _research_runner_factory(run_id: str) -> None:
            runner = getattr(container, "backtest_runner", None)
            if runner is None:
                raise RuntimeError("backtest_runner not initialized")
            await runner.run(run_id)

        async def _broadcast_research_update(payload: dict) -> None:
            await _ws_manager.broadcast_to_dashboards(
                {"type": "research_job", **payload}
            )

        container.research_job_manager = ResearchJobManager(
            session_factory=session_factory,
            sweep_fn=_run_sweep_fn,
            walk_forward_fn=_run_walk_forward_fn,
            runner_factory=_research_runner_factory,
            sync_session_factory=_get_sync_session_factory(),
            on_job_update=_broadcast_research_update,
        )
        n_recovered_jobs = await container.research_job_manager.recover_orphaned_jobs()
        if n_recovered_jobs > 0:
            logger.info("Recovered %d orphaned research job(s) from previous run", n_recovered_jobs)
```

- [ ] **Step 3: Restart and smoke**

```
quilt coord restart
quilt coord logs 2>&1 | tail -5
```

Expected: clean startup, no errors. The lifespan banner mentions the research manager as before.

Then verify the WS event flows: open the dashboard in one tab, then in another terminal:

```
quilt research session create --name smoke-test --hypothesis "ws smoke" --parameter-space '{"x":[1,2]}' --criteria '{"min_sharpe": 0.0}'
```

(Note the session ID printed.) The session-create itself doesn't fire `research_job` events; you'd need to kick off a sweep, but at this stage the dashboard doesn't yet subscribe. The WS will start carrying `research_job` events as soon as Task 8 lands.

- [ ] **Step 4: Commit**

```
git add coordinator/main.py
git commit -m "feat(coord): wire ResearchJobManager.on_job_update to dashboard WS

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Frontend API client types + methods + query keys

**Files:**
- Modify: `dashboard/src/api/client.ts`
- Modify: `dashboard/src/api/hooks.ts` (just the `keys` object)

- [ ] **Step 1: Add types to client.ts**

Insert near the other interface declarations (after `CoverageRange` is a fine spot):

```typescript
// ── Research / Validation Lab ──
export interface ResearchSession {
  id: number;
  name: string;
  hypothesis: string;
  status: "open" | "running" | "completed" | "failed";
  notes: string;
  created_at: string;
  completed_at: string | null;
  parameter_space: Record<string, unknown>;
  pre_registered_criteria: Record<string, unknown>;
  n_runs: number;
}

export interface ResearchJob {
  job_id: string;
  session_id: number;
  kind: "sweep" | "walk-forward";
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  progress_pct: number;
  progress_message: string | null;
  run_ids: string[];
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string | null;
}

export interface CreateSessionRequest {
  name: string;
  hypothesis: string;
  parameter_space: Record<string, unknown>;
  pre_registered_criteria: Record<string, unknown>;
  notes?: string;
}

export interface CreateSweepRequest {
  algorithm_id: string;
  base_config: Record<string, unknown>;
  parameter_space?: Record<string, unknown> | null;
  search?: "grid" | "random" | "latin" | "tpe";
  max_trials?: number;
  parallelism?: number;
  seed?: number;
}

export interface GenerateReportResponse {
  session_id: number;
  markdown_path: string;
  html_path: string;
}
```

- [ ] **Step 2: Add API methods to the `api` object in client.ts**

Append to the `api = { ... }` object (near other endpoints, doesn't matter exactly where):

```typescript
  // ── Research / Validation Lab ──
  listResearchSessions(): Promise<ResearchSession[]> {
    return request<ResearchSession[]>("/api/research/sessions");
  },
  getResearchSession(id: number): Promise<ResearchSession> {
    return request<ResearchSession>(`/api/research/sessions/${id}`);
  },
  createResearchSession(body: CreateSessionRequest): Promise<ResearchSession> {
    return request<ResearchSession>("/api/research/sessions", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  listResearchJobs(sessionId: number): Promise<ResearchJob[]> {
    return request<ResearchJob[]>(
      `/api/research/sessions/${sessionId}/jobs`
    );
  },
  getResearchJob(sessionId: number, jobId: string): Promise<ResearchJob> {
    return request<ResearchJob>(
      `/api/research/sessions/${sessionId}/jobs/${jobId}`
    );
  },
  createResearchSweep(
    sessionId: number,
    body: CreateSweepRequest,
  ): Promise<ResearchJob> {
    return request<ResearchJob>(
      `/api/research/sessions/${sessionId}/sweep`,
      { method: "POST", body: JSON.stringify(body) },
    );
  },
  cancelResearchJob(sessionId: number, jobId: string): Promise<{ ok: true }> {
    return request<{ ok: true }>(
      `/api/research/sessions/${sessionId}/jobs/${jobId}`,
      { method: "DELETE" },
    );
  },
  generateResearchReport(sessionId: number): Promise<GenerateReportResponse> {
    return request<GenerateReportResponse>(
      `/api/research/sessions/${sessionId}/report`,
      { method: "POST" },
    );
  },
```

- [ ] **Step 3: Add query keys to hooks.ts**

Edit the `keys` object in `dashboard/src/api/hooks.ts`. Add inside the object literal:

```typescript
  researchSessions: () => ["research", "sessions"] as const,
  researchSession: (id: number) => ["research", "sessions", id] as const,
  researchJobs: (sessionId: number) =>
    ["research", "sessions", sessionId, "jobs"] as const,
  researchJob: (sessionId: number, jobId: string) =>
    ["research", "sessions", sessionId, "jobs", jobId] as const,
```

- [ ] **Step 4: Typecheck**

```
cd dashboard && npx tsc --noEmit 2>&1 | head -20
```

Expected: clean (no output).

- [ ] **Step 5: Commit**

```
git add dashboard/src/api/client.ts dashboard/src/api/hooks.ts
git commit -m "feat(dashboard): API client + query keys for research lab

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Query hooks (sessions + session detail + jobs)

**Files:**
- Create: `dashboard/src/hooks/useResearchSessions.ts`
- Create: `dashboard/src/hooks/useResearchSession.ts`

- [ ] **Step 1: Implement `useResearchSessions`**

```typescript
// dashboard/src/hooks/useResearchSessions.ts
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { keys } from "../api/hooks";

export function useResearchSessions() {
  return useQuery({
    queryKey: keys.researchSessions(),
    queryFn: api.listResearchSessions,
  });
}
```

- [ ] **Step 2: Implement `useResearchSession`**

```typescript
// dashboard/src/hooks/useResearchSession.ts
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { keys } from "../api/hooks";

export function useResearchSession(id: number | null) {
  return useQuery({
    queryKey: id !== null ? keys.researchSession(id) : ["research", "sessions", "null"],
    queryFn: () => api.getResearchSession(id as number),
    enabled: id !== null,
  });
}

export function useResearchJobs(sessionId: number | null) {
  return useQuery({
    queryKey: sessionId !== null
      ? keys.researchJobs(sessionId)
      : ["research", "sessions", "null", "jobs"],
    queryFn: () => api.listResearchJobs(sessionId as number),
    enabled: sessionId !== null,
  });
}
```

- [ ] **Step 3: Typecheck**

```
cd dashboard && npx tsc --noEmit
```

Expected: clean.

- [ ] **Step 4: Commit**

```
git add dashboard/src/hooks/useResearchSessions.ts dashboard/src/hooks/useResearchSession.ts
git commit -m "feat(dashboard): query hooks for research sessions + jobs

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Mutation hooks (create session, create sweep, cancel job, generate report)

**Files:**
- Create: `dashboard/src/hooks/useResearchMutations.ts`

- [ ] **Step 1: Implement**

```typescript
// dashboard/src/hooks/useResearchMutations.ts
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type {
  CreateSessionRequest,
  CreateSweepRequest,
} from "../api/client";
import { keys } from "../api/hooks";

export function useCreateResearchSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateSessionRequest) => api.createResearchSession(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.researchSessions() });
    },
  });
}

export function useCreateResearchSweep(sessionId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateSweepRequest) =>
      api.createResearchSweep(sessionId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.researchJobs(sessionId) });
      void qc.invalidateQueries({ queryKey: keys.researchSession(sessionId) });
    },
  });
}

export function useCancelResearchJob(sessionId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => api.cancelResearchJob(sessionId, jobId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.researchJobs(sessionId) });
    },
  });
}

export function useGenerateResearchReport(sessionId: number) {
  return useMutation({
    mutationFn: () => api.generateResearchReport(sessionId),
  });
}
```

- [ ] **Step 2: Typecheck**

```
cd dashboard && npx tsc --noEmit
```

Expected: clean.

- [ ] **Step 3: Commit**

```
git add dashboard/src/hooks/useResearchMutations.ts
git commit -m "feat(dashboard): mutation hooks for research sessions, sweeps, cancel, report

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Extend `useWebSocketSync` with `research_job` subscription

**Files:**
- Modify: `dashboard/src/hooks/useWebSocketSync.ts`

- [ ] **Step 1: Implement**

Read the existing file first to see the exact local-variable names and unsubscribe pattern. Add a new subscription block alongside the others in the same `useEffect`. Sketch (adapt the import + exact `keys` usage to what's already there):

```typescript
// At the top, ensure imports include:
import type { ResearchJob } from "../api/client";

// Inside the useEffect, alongside existing subscriptions:
const unsubscribeResearchJob = wsManager.subscribe(
  "research_job",
  (data) => {
    const msg = data as Partial<ResearchJob> & {
      session_id: number;
      job_id: string;
    };
    // Patch any cached jobs-list query for this session
    queryClient.setQueriesData<ResearchJob[]>(
      { queryKey: keys.researchJobs(msg.session_id) },
      (old) =>
        old?.map((j) =>
          j.job_id === msg.job_id ? { ...j, ...msg } : j,
        ) ?? old,
    );
    // Patch the single-job query if anyone's watching it
    queryClient.setQueryData(
      keys.researchJob(msg.session_id, msg.job_id),
      (old: ResearchJob | undefined) =>
        old ? { ...old, ...msg } : (msg as ResearchJob),
    );
  },
);

// And in the cleanup return:
return () => {
  // ... existing unsubscribes ...
  unsubscribeResearchJob();
};
```

- [ ] **Step 2: Typecheck**

```
cd dashboard && npx tsc --noEmit
```

Expected: clean.

- [ ] **Step 3: Commit**

```
git add dashboard/src/hooks/useWebSocketSync.ts
git commit -m "feat(dashboard): WS subscription for research_job updates

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: `JsonTextField` reusable component

**Files:**
- Create: `dashboard/src/components/JsonTextField.tsx`
- Create: `dashboard/src/components/JsonTextField.test.tsx`

- [ ] **Step 1: Write failing tests**

```typescript
// dashboard/src/components/JsonTextField.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { JsonTextField } from "./JsonTextField";

describe("JsonTextField", () => {
  it("calls onChange with parsed value when input is valid JSON", () => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    render(
      <JsonTextField
        label="Param space"
        value={{}}
        onChange={onChange}
      />,
    );
    const ta = screen.getByLabelText("Param space");
    fireEvent.change(ta, { target: { value: '{"vol":0.1}' } });
    act(() => vi.advanceTimersByTime(250));
    expect(onChange).toHaveBeenCalledWith({ vol: 0.1 });
    expect(screen.queryByText(/invalid|error/i)).toBeNull();
    vi.useRealTimers();
  });

  it("shows error message when JSON is invalid and does NOT call onChange", () => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    render(
      <JsonTextField label="Param space" value={{}} onChange={onChange} />,
    );
    const ta = screen.getByLabelText("Param space");
    fireEvent.change(ta, { target: { value: "{not json" } });
    act(() => vi.advanceTimersByTime(250));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText(/json|expected|error/i)).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("renders as read-only when disabled, shows pretty JSON, no error UI", () => {
    render(
      <JsonTextField
        label="Param space"
        value={{ a: 1, b: 2 }}
        onChange={() => {}}
        disabled
      />,
    );
    const ta = screen.getByLabelText("Param space") as HTMLTextAreaElement;
    expect(ta).toBeDisabled();
    expect(ta.value).toContain("a");
    expect(ta.value).toContain("b");
  });

  it("debounces — typing 5 chars within 100ms produces no onChange", () => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    render(<JsonTextField label="X" value={{}} onChange={onChange} />);
    const ta = screen.getByLabelText("X");
    fireEvent.change(ta, { target: { value: "{" } });
    act(() => vi.advanceTimersByTime(50));
    fireEvent.change(ta, { target: { value: '{"' } });
    act(() => vi.advanceTimersByTime(50));
    expect(onChange).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd dashboard && npx vitest run src/components/JsonTextField.test.tsx
```

Expected: file-not-found / import errors (component doesn't exist yet).

- [ ] **Step 3: Implement**

```typescript
// dashboard/src/components/JsonTextField.tsx
import { useEffect, useRef, useState, useId } from "react";

interface JsonTextFieldProps {
  label: string;
  value: Record<string, unknown> | null;
  onChange: (parsed: Record<string, unknown> | null) => void;
  disabled?: boolean;
  placeholder?: string;
  rows?: number;
  required?: boolean;
}

export function JsonTextField({
  label,
  value,
  onChange,
  disabled,
  placeholder,
  rows = 6,
  required,
}: JsonTextFieldProps) {
  const id = useId();
  const [text, setText] = useState<string>(() =>
    value === null ? "" : JSON.stringify(value, null, 2),
  );
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  // Re-hydrate when the value prop changes from the outside (e.g. modal open
  // with a fresh default).
  useEffect(() => {
    setText(value === null ? "" : JSON.stringify(value, null, 2));
  }, [value]);

  const onTextChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const next = e.target.value;
    setText(next);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      if (next.trim() === "") {
        setError(null);
        onChange(null);
        return;
      }
      try {
        const parsed = JSON.parse(next);
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
          setError("Top-level value must be an object");
          return;
        }
        setError(null);
        onChange(parsed);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Invalid JSON");
      }
    }, 200);
  };

  const valid = error === null;

  return (
    <div className="space-y-1">
      <label htmlFor={id} className="text-sm text-gray-300">
        {label}
        {required && <span className="text-red-400 ml-1">*</span>}
      </label>
      <textarea
        id={id}
        rows={rows}
        value={text}
        onChange={onTextChange}
        disabled={disabled}
        placeholder={placeholder ?? '{"key": "value"}'}
        className={
          "w-full font-mono text-xs bg-gray-800 border rounded px-3 py-2 " +
          "text-gray-100 focus:outline-none focus:ring-1 focus:ring-indigo-500 " +
          (valid ? "border-gray-700" : "border-red-500") +
          (disabled ? " opacity-70 cursor-not-allowed" : "")
        }
      />
      {!disabled && (
        <div className="text-xs">
          {valid ? (
            <span className="text-green-400">✓ valid JSON</span>
          ) : (
            <span className="text-red-400">✗ {error}</span>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd dashboard && npx vitest run src/components/JsonTextField.test.tsx
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```
git add dashboard/src/components/JsonTextField.tsx dashboard/src/components/JsonTextField.test.tsx
git commit -m "feat(dashboard): JsonTextField reusable component

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: `NewSessionModal` component

**Files:**
- Create: `dashboard/src/components/NewSessionModal.tsx`
- Create: `dashboard/src/components/NewSessionModal.test.tsx`

- [ ] **Step 1: Write failing tests**

```typescript
// dashboard/src/components/NewSessionModal.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { NewSessionModal } from "./NewSessionModal";

vi.mock("../api/client", () => ({
  api: {
    createResearchSession: vi.fn().mockResolvedValue({
      id: 42, name: "T", hypothesis: "H", status: "open",
      notes: "", created_at: "2026-05-30",
      completed_at: null, parameter_space: {}, pre_registered_criteria: {},
      n_runs: 0,
    }),
  },
}));

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

describe("NewSessionModal", () => {
  it("submit disabled until all required fields valid", () => {
    render(wrap(<NewSessionModal open={true} onClose={() => {}} onCreated={() => {}} />));
    const submit = screen.getByRole("button", { name: /create session/i });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/^name/i), { target: { value: "T" } });
    fireEvent.change(screen.getByLabelText(/hypothesis/i), { target: { value: "H" } });
    // parameter_space + criteria still empty → invalid (component renders them blank)
    expect(submit).toBeDisabled();
  });

  it("invalid JSON in parameter_space keeps submit disabled and shows error", async () => {
    vi.useFakeTimers();
    render(wrap(<NewSessionModal open={true} onClose={() => {}} onCreated={() => {}} />));
    const params = screen.getByLabelText(/parameter space/i);
    fireEvent.change(params, { target: { value: "{not json" } });
    act(() => vi.advanceTimersByTime(250));
    expect(screen.getByText(/invalid|expected|json/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /create session/i })).toBeDisabled();
    vi.useRealTimers();
  });

  it("successful submit calls API with correct body and invokes onCreated", async () => {
    vi.useFakeTimers();
    const onCreated = vi.fn();
    render(wrap(<NewSessionModal open={true} onClose={() => {}} onCreated={onCreated} />));
    fireEvent.change(screen.getByLabelText(/^name/i), { target: { value: "Smoke" } });
    fireEvent.change(screen.getByLabelText(/hypothesis/i), { target: { value: "test" } });
    fireEvent.change(screen.getByLabelText(/parameter space/i), {
      target: { value: '{"x":[1]}' },
    });
    fireEvent.change(screen.getByLabelText(/criteria/i), {
      target: { value: '{"min_sharpe":1}' },
    });
    act(() => vi.advanceTimersByTime(250));
    vi.useRealTimers();
    const submit = screen.getByRole("button", { name: /create session/i });
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(42));
    const { api } = await import("../api/client");
    expect(api.createResearchSession).toHaveBeenCalledWith({
      name: "Smoke",
      hypothesis: "test",
      parameter_space: { x: [1] },
      pre_registered_criteria: { min_sharpe: 1 },
      notes: "",
    });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd dashboard && npx vitest run src/components/NewSessionModal.test.tsx
```

Expected: file-not-found.

- [ ] **Step 3: Implement**

```typescript
// dashboard/src/components/NewSessionModal.tsx
import { useState } from "react";
import { X } from "lucide-react";
import { JsonTextField } from "./JsonTextField";
import { useCreateResearchSession } from "../hooks/useResearchMutations";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (sessionId: number) => void;
}

export function NewSessionModal({ open, onClose, onCreated }: Props) {
  const [name, setName] = useState("");
  const [hypothesis, setHypothesis] = useState("");
  const [paramSpace, setParamSpace] = useState<Record<string, unknown> | null>(null);
  const [criteria, setCriteria] = useState<Record<string, unknown> | null>(null);
  const [notes, setNotes] = useState("");
  const [submitError, setSubmitError] = useState<string | null>(null);

  const mut = useCreateResearchSession();

  if (!open) return null;

  const canSubmit =
    name.trim().length > 0 &&
    hypothesis.trim().length > 0 &&
    paramSpace !== null &&
    criteria !== null &&
    !mut.isPending;

  const handleSubmit = async () => {
    setSubmitError(null);
    try {
      const session = await mut.mutateAsync({
        name: name.trim(),
        hypothesis: hypothesis.trim(),
        parameter_space: paramSpace!,
        pre_registered_criteria: criteria!,
        notes: notes.trim(),
      });
      onCreated(session.id);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to create session";
      setSubmitError(msg);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70" onClick={onClose} aria-hidden="true" />
      <div className="relative z-10 bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-2xl mx-auto flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800 shrink-0">
          <h2 className="text-xl font-bold text-white">New Research Session</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-white">
            <X size={20} />
          </button>
        </div>
        <div className="overflow-auto px-6 py-4 space-y-4">
          <div className="space-y-1">
            <label htmlFor="rs-name" className="text-sm text-gray-300">
              Name <span className="text-red-400">*</span>
            </label>
            <input
              id="rs-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="rs-hyp" className="text-sm text-gray-300">
              Hypothesis <span className="text-red-400">*</span>
            </label>
            <textarea
              id="rs-hyp"
              rows={4}
              value={hypothesis}
              onChange={(e) => setHypothesis(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
            />
          </div>
          <JsonTextField
            label="Parameter space"
            value={paramSpace}
            onChange={setParamSpace}
            required
            placeholder='{"vol_target": [0.10, 0.15, 0.20]}'
          />
          <JsonTextField
            label="Pre-registered criteria"
            value={criteria}
            onChange={setCriteria}
            required
            placeholder='{"min_sharpe": 1.0, "max_drawdown": 0.20}'
          />
          <div className="space-y-1">
            <label htmlFor="rs-notes" className="text-sm text-gray-300">Notes (optional)</label>
            <textarea
              id="rs-notes"
              rows={3}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
            />
          </div>
          {submitError && (
            <div className="text-red-400 text-sm">{submitError}</div>
          )}
        </div>
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-800 shrink-0">
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white px-3 py-1.5 rounded text-sm"
          >
            Cancel
          </button>
          <button
            onClick={() => void handleSubmit()}
            disabled={!canSubmit}
            className="bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-4 py-2 rounded disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {mut.isPending ? "Creating…" : "Create session"}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd dashboard && npx vitest run src/components/NewSessionModal.test.tsx
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```
git add dashboard/src/components/NewSessionModal.tsx dashboard/src/components/NewSessionModal.test.tsx
git commit -m "feat(dashboard): NewSessionModal — 4-field form with JSON validation

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: `NewSweepModal` component

**Files:**
- Create: `dashboard/src/components/NewSweepModal.tsx`
- Create: `dashboard/src/components/NewSweepModal.test.tsx`

- [ ] **Step 1: Write failing tests**

```typescript
// dashboard/src/components/NewSweepModal.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { NewSweepModal } from "./NewSweepModal";

const sweepFn = vi.fn().mockResolvedValue({
  job_id: "j1", session_id: 7, kind: "sweep", status: "queued",
  progress_pct: 0, progress_message: null, run_ids: [],
  error_message: null, started_at: null, completed_at: null, created_at: null,
});

vi.mock("../api/client", () => ({
  api: {
    createResearchSweep: (sessionId: number, body: unknown) =>
      sweepFn(sessionId, body),
  },
}));

vi.mock("../api/hooks", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../api/hooks")>();
  return {
    ...orig,
    useAlgorithms: () => ({
      data: [
        { id: "algo-a", name: "Algo A", manifest_path: "/p/algo-a/quilt.yaml" },
        { id: "algo-b", name: "Algo B", manifest_path: "/p/algo-b/quilt.yaml" },
      ],
      isLoading: false,
    }),
  };
});

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

describe("NewSweepModal", () => {
  it("algorithm dropdown is populated from useAlgorithms", () => {
    render(wrap(<NewSweepModal open={true} sessionId={7} onClose={() => {}} />));
    const sel = screen.getByLabelText(/algorithm/i);
    expect(sel).toHaveTextContent("Algo A");
    expect(sel).toHaveTextContent("Algo B");
  });

  it("submit body uses algorithm_id, not manifest_path", async () => {
    render(wrap(<NewSweepModal open={true} sessionId={7} onClose={() => {}} />));
    fireEvent.change(screen.getByLabelText(/algorithm/i), { target: { value: "algo-a" } });
    fireEvent.click(screen.getByRole("button", { name: /start sweep/i }));
    await waitFor(() => expect(sweepFn).toHaveBeenCalled());
    const [sessionId, body] = sweepFn.mock.calls[0];
    expect(sessionId).toBe(7);
    expect(body.algorithm_id).toBe("algo-a");
    expect(body.manifest_path).toBeUndefined();
  });

  it("invalid base_config disables submit", async () => {
    vi.useFakeTimers();
    render(wrap(<NewSweepModal open={true} sessionId={7} onClose={() => {}} />));
    fireEvent.change(screen.getByLabelText(/algorithm/i), { target: { value: "algo-a" } });
    fireEvent.change(screen.getByLabelText(/base config/i), { target: { value: "{not json" } });
    act(() => vi.advanceTimersByTime(250));
    expect(screen.getByRole("button", { name: /start sweep/i })).toBeDisabled();
    vi.useRealTimers();
  });

  it("empty parameter_space submits as null", async () => {
    sweepFn.mockClear();
    render(wrap(<NewSweepModal open={true} sessionId={7} onClose={() => {}} />));
    fireEvent.change(screen.getByLabelText(/algorithm/i), { target: { value: "algo-b" } });
    fireEvent.click(screen.getByRole("button", { name: /start sweep/i }));
    await waitFor(() => expect(sweepFn).toHaveBeenCalled());
    const body = sweepFn.mock.calls[0][1];
    expect(body.parameter_space).toBeNull();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd dashboard && npx vitest run src/components/NewSweepModal.test.tsx
```

- [ ] **Step 3: Implement**

```typescript
// dashboard/src/components/NewSweepModal.tsx
import { useState } from "react";
import { X } from "lucide-react";
import { JsonTextField } from "./JsonTextField";
import { useAlgorithms } from "../api/hooks";
import { useCreateResearchSweep } from "../hooks/useResearchMutations";

interface Props {
  open: boolean;
  sessionId: number;
  onClose: () => void;
}

export function NewSweepModal({ open, sessionId, onClose }: Props) {
  const algos = useAlgorithms();
  const mut = useCreateResearchSweep(sessionId);

  const [algorithmId, setAlgorithmId] = useState<string>("");
  const [baseConfig, setBaseConfig] = useState<Record<string, unknown> | null>({});
  const [paramSpace, setParamSpace] = useState<Record<string, unknown> | null>(null);
  const [search, setSearch] = useState<"grid" | "random" | "latin" | "tpe">("grid");
  const [maxTrials, setMaxTrials] = useState(50);
  const [parallelism, setParallelism] = useState(1);
  const [seed, setSeed] = useState<number | "">("");
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [baseConfigValid, setBaseConfigValid] = useState(true);

  if (!open) return null;

  const canSubmit =
    algorithmId !== "" && baseConfig !== null && baseConfigValid && !mut.isPending;

  const handleSubmit = async () => {
    setSubmitError(null);
    try {
      await mut.mutateAsync({
        algorithm_id: algorithmId,
        base_config: baseConfig ?? {},
        parameter_space: paramSpace,
        search,
        max_trials: maxTrials,
        parallelism,
        seed: seed === "" ? undefined : seed,
      });
      onClose();
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : "Failed to start sweep");
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70" onClick={onClose} aria-hidden="true" />
      <div className="relative z-10 bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-2xl mx-auto flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800 shrink-0">
          <h2 className="text-xl font-bold text-white">New Sweep</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-white"><X size={20} /></button>
        </div>
        <div className="overflow-auto px-6 py-4 space-y-4">
          <div className="space-y-1">
            <label htmlFor="sw-algo" className="text-sm text-gray-300">
              Algorithm <span className="text-red-400">*</span>
            </label>
            <select
              id="sw-algo"
              value={algorithmId}
              onChange={(e) => setAlgorithmId(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
            >
              <option value="">— select —</option>
              {(algos.data ?? []).map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} ({a.id})
                </option>
              ))}
            </select>
          </div>
          <JsonTextField
            label="Base config"
            value={baseConfig}
            onChange={(v) => { setBaseConfig(v); setBaseConfigValid(true); }}
            placeholder='{"vol_target": 0.10}'
          />
          <JsonTextField
            label="Parameter space (optional — falls back to session's space)"
            value={paramSpace}
            onChange={setParamSpace}
            placeholder='{"vol_target": [0.10, 0.15]}'
          />
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1">
              <label htmlFor="sw-search" className="text-sm text-gray-300">Search</label>
              <select
                id="sw-search"
                value={search}
                onChange={(e) => setSearch(e.target.value as typeof search)}
                className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
              >
                <option value="grid">grid</option>
                <option value="random">random</option>
                <option value="latin">latin</option>
                <option value="tpe">tpe</option>
              </select>
            </div>
            <div className="space-y-1">
              <label htmlFor="sw-max" className="text-sm text-gray-300">Max trials</label>
              <input
                id="sw-max" type="number" min={1} value={maxTrials}
                onChange={(e) => setMaxTrials(parseInt(e.target.value || "0", 10))}
                className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
              />
            </div>
            <div className="space-y-1">
              <label htmlFor="sw-par" className="text-sm text-gray-300">Parallelism</label>
              <input
                id="sw-par" type="number" min={1} value={parallelism}
                onChange={(e) => setParallelism(parseInt(e.target.value || "1", 10))}
                className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
              />
            </div>
            <div className="space-y-1">
              <label htmlFor="sw-seed" className="text-sm text-gray-300">Seed (optional)</label>
              <input
                id="sw-seed" type="number" value={seed}
                onChange={(e) => setSeed(e.target.value === "" ? "" : parseInt(e.target.value, 10))}
                className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
              />
            </div>
          </div>
          {submitError && <div className="text-red-400 text-sm">{submitError}</div>}
        </div>
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-800 shrink-0">
          <button onClick={onClose} className="text-gray-400 hover:text-white px-3 py-1.5 rounded text-sm">Cancel</button>
          <button
            onClick={() => void handleSubmit()}
            disabled={!canSubmit}
            className="bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-4 py-2 rounded disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {mut.isPending ? "Queuing…" : "Start sweep"}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd dashboard && npx vitest run src/components/NewSweepModal.test.tsx
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```
git add dashboard/src/components/NewSweepModal.tsx dashboard/src/components/NewSweepModal.test.tsx
git commit -m "feat(dashboard): NewSweepModal — algorithm dropdown + JSON config

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: `ResearchJobRow` component

**Files:**
- Create: `dashboard/src/components/ResearchJobRow.tsx`
- Create: `dashboard/src/components/ResearchJobRow.test.tsx`

- [ ] **Step 1: Write failing tests**

```typescript
// dashboard/src/components/ResearchJobRow.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { ResearchJobRow } from "./ResearchJobRow";
import type { ResearchJob } from "../api/client";

const baseJob: ResearchJob = {
  job_id: "j-1",
  session_id: 7,
  kind: "sweep",
  status: "running",
  progress_pct: 0.42,
  progress_message: "trial 12/30",
  run_ids: ["r1", "r2", "r3"],
  error_message: null,
  started_at: "2026-05-30T12:00:00Z",
  completed_at: null,
  created_at: "2026-05-30T11:55:00Z",
};

function wrap(ui: React.ReactNode) {
  return <BrowserRouter>{ui}</BrowserRouter>;
}

describe("ResearchJobRow", () => {
  it("renders status pill and progress bar at the right width", () => {
    render(wrap(<ResearchJobRow job={baseJob} onCancel={() => {}} />));
    expect(screen.getByText(/running/i)).toBeInTheDocument();
    const bar = screen.getByTestId("progress-fill");
    expect(bar.style.width).toBe("42%");
  });

  it("Cancel button visible only when status is queued or running", () => {
    const { rerender } = render(
      wrap(<ResearchJobRow job={baseJob} onCancel={() => {}} />),
    );
    expect(screen.getByRole("button", { name: /cancel/i })).toBeInTheDocument();
    rerender(wrap(<ResearchJobRow job={{ ...baseJob, status: "completed" }} onCancel={() => {}} />));
    expect(screen.queryByRole("button", { name: /cancel/i })).toBeNull();
  });

  it("renders run_ids as links to /backtests/runs/{id}", () => {
    render(wrap(<ResearchJobRow job={baseJob} onCancel={() => {}} />));
    // Expand the row to reveal run links
    fireEvent.click(screen.getByText(/3 runs/i));
    const link = screen.getByRole("link", { name: /r1/ });
    expect(link).toHaveAttribute("href", "/backtests/runs/r1");
  });

  it("expanded row shows error_message when status=failed", () => {
    render(
      wrap(
        <ResearchJobRow
          job={{ ...baseJob, status: "failed", error_message: "Boom" }}
          onCancel={() => {}}
        />,
      ),
    );
    fireEvent.click(screen.getByText(/sweep/i));
    expect(screen.getByText(/boom/i)).toBeInTheDocument();
  });

  it("clicking Cancel invokes onCancel with job_id", () => {
    const onCancel = vi.fn();
    render(wrap(<ResearchJobRow job={baseJob} onCancel={onCancel} />));
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onCancel).toHaveBeenCalledWith("j-1");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd dashboard && npx vitest run src/components/ResearchJobRow.test.tsx
```

- [ ] **Step 3: Implement**

```typescript
// dashboard/src/components/ResearchJobRow.tsx
import { useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { ResearchJob } from "../api/client";

interface Props {
  job: ResearchJob;
  onCancel: (jobId: string) => void;
}

const STATUS_COLORS: Record<ResearchJob["status"], string> = {
  queued:    "bg-gray-700 text-gray-300",
  running:   "bg-blue-700 text-blue-100",
  completed: "bg-green-700 text-green-100",
  failed:    "bg-red-700 text-red-100",
  cancelled: "bg-yellow-700 text-yellow-100",
};

const KIND_COLORS: Record<ResearchJob["kind"], string> = {
  sweep:           "bg-indigo-700 text-indigo-100",
  "walk-forward":  "bg-purple-700 text-purple-100",
};

export function ResearchJobRow({ job, onCancel }: Props) {
  const [expanded, setExpanded] = useState(false);
  const canCancel = job.status === "queued" || job.status === "running";
  const pct = Math.round(job.progress_pct * 100);

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-gray-800/50"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? <ChevronDown size={14} className="text-gray-500" /> : <ChevronRight size={14} className="text-gray-500" />}

        <span className={`text-xs font-medium px-2 py-0.5 rounded ${KIND_COLORS[job.kind]}`}>
          {job.kind}
        </span>
        <span className={`text-xs font-medium px-2 py-0.5 rounded ${STATUS_COLORS[job.status]}`}>
          {job.status}
        </span>

        <div className="flex-1 min-w-0">
          <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
            <div
              data-testid="progress-fill"
              className="h-full bg-indigo-500"
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="text-xs text-gray-500 mt-1 truncate">
            {job.progress_message ?? "—"}
          </div>
        </div>

        <div
          className="text-xs text-gray-400 whitespace-nowrap"
          onClick={(e) => { e.stopPropagation(); setExpanded((v) => !v); }}
        >
          {job.run_ids.length > 0 ? `${job.run_ids.length} runs` : "—"}
        </div>

        {canCancel && (
          <button
            onClick={(e) => { e.stopPropagation(); onCancel(job.job_id); }}
            className="text-xs text-red-400 hover:text-red-300 px-2 py-1 border border-red-900 rounded"
          >
            Cancel
          </button>
        )}
      </div>

      {expanded && (
        <div className="bg-gray-950 border-t border-gray-800 px-4 py-3 text-xs space-y-2">
          <div>
            <span className="text-gray-500">job_id:</span>{" "}
            <code className="text-gray-300">{job.job_id}</code>
          </div>
          {job.started_at && (
            <div>
              <span className="text-gray-500">started:</span>{" "}
              <span className="text-gray-300">{job.started_at}</span>
            </div>
          )}
          {job.completed_at && (
            <div>
              <span className="text-gray-500">completed:</span>{" "}
              <span className="text-gray-300">{job.completed_at}</span>
            </div>
          )}
          {job.error_message && (
            <div className="text-red-400">
              <span className="text-gray-500">error:</span> {job.error_message}
            </div>
          )}
          {job.run_ids.length > 0 && (
            <div>
              <div className="text-gray-500 mb-1">runs:</div>
              <div className="flex flex-wrap gap-1">
                {job.run_ids.map((rid) => (
                  <Link
                    key={rid}
                    to={`/backtests/runs/${rid}`}
                    className="text-indigo-400 hover:text-indigo-300 text-xs px-2 py-0.5 border border-indigo-900 rounded"
                  >
                    {rid}
                  </Link>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd dashboard && npx vitest run src/components/ResearchJobRow.test.tsx
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```
git add dashboard/src/components/ResearchJobRow.tsx dashboard/src/components/ResearchJobRow.test.tsx
git commit -m "feat(dashboard): ResearchJobRow — status, progress, cancel, run links

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: `ResearchSessionSummary` component

**Files:**
- Create: `dashboard/src/components/ResearchSessionSummary.tsx`

(No dedicated tests — exercised via `ResearchSessionDetail.test.tsx` in Task 15.)

- [ ] **Step 1: Implement**

```typescript
// dashboard/src/components/ResearchSessionSummary.tsx
import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { ResearchSession } from "../api/client";
import { JsonTextField } from "./JsonTextField";

interface Props {
  session: ResearchSession;
  onNewSweep: () => void;
  onGenerateReport: () => void;
  reportPending: boolean;
}

const STATUS_COLORS: Record<ResearchSession["status"], string> = {
  open:      "bg-gray-700 text-gray-300",
  running:   "bg-blue-700 text-blue-100",
  completed: "bg-green-700 text-green-100",
  failed:    "bg-red-700 text-red-100",
};

export function ResearchSessionSummary({
  session,
  onNewSweep,
  onGenerateReport,
  reportPending,
}: Props) {
  const [hypExpanded, setHypExpanded] = useState(false);
  const canReport = session.n_runs > 0;

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-white truncate">{session.name}</h1>
            <span className={`text-xs font-medium px-2 py-0.5 rounded ${STATUS_COLORS[session.status]}`}>
              {session.status}
            </span>
          </div>
          <div className="text-xs text-gray-500 mt-1">
            Created {session.created_at} · {session.n_runs} run{session.n_runs === 1 ? "" : "s"}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={onGenerateReport}
            disabled={!canReport || reportPending}
            className="bg-gray-800 hover:bg-gray-700 text-gray-200 text-sm px-3 py-1.5 rounded border border-gray-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {reportPending ? "Generating…" : "Generate Report"}
          </button>
          <button
            onClick={onNewSweep}
            className="bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-4 py-1.5 rounded"
          >
            New Sweep
          </button>
        </div>
      </div>

      <div>
        <button
          onClick={() => setHypExpanded((v) => !v)}
          className="flex items-center gap-1 text-sm text-gray-400 hover:text-gray-200"
        >
          {hypExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          Hypothesis
        </button>
        {hypExpanded && (
          <div className="mt-2 text-sm text-gray-300 whitespace-pre-wrap bg-gray-950 border border-gray-800 rounded p-3">
            {session.hypothesis}
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-4">
        <JsonTextField
          label="Parameter space"
          value={session.parameter_space as Record<string, unknown>}
          onChange={() => {}}
          disabled
          rows={5}
        />
        <JsonTextField
          label="Pre-registered criteria"
          value={session.pre_registered_criteria as Record<string, unknown>}
          onChange={() => {}}
          disabled
          rows={5}
        />
      </div>

      {session.notes && (
        <div>
          <div className="text-xs text-gray-500 mb-1">Notes</div>
          <div className="text-sm text-gray-300 whitespace-pre-wrap bg-gray-950 border border-gray-800 rounded p-3">
            {session.notes}
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

```
cd dashboard && npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```
git add dashboard/src/components/ResearchSessionSummary.tsx
git commit -m "feat(dashboard): ResearchSessionSummary header card

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: `Research.tsx` sessions list page

**Files:**
- Create: `dashboard/src/pages/Research.tsx`
- Create: `dashboard/src/pages/Research.test.tsx`

- [ ] **Step 1: Write failing tests**

```typescript
// dashboard/src/pages/Research.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { Research } from "./Research";

vi.mock("../api/client", () => ({
  api: {
    listResearchSessions: vi.fn(),
    createResearchSession: vi.fn(),
  },
}));

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>{ui}</BrowserRouter>
    </QueryClientProvider>
  );
}

describe("Research", () => {
  it("renders empty state when API returns no sessions", async () => {
    const { api } = await import("../api/client");
    (api.listResearchSessions as any).mockResolvedValue([]);
    render(wrap(<Research />));
    await waitFor(() => {
      expect(screen.getByText(/create your first session/i)).toBeInTheDocument();
    });
  });

  it("renders one row per session", async () => {
    const { api } = await import("../api/client");
    (api.listResearchSessions as any).mockResolvedValue([
      { id: 1, name: "S1", hypothesis: "H1", status: "open", notes: "",
        created_at: "2026-05-30", completed_at: null, parameter_space: {},
        pre_registered_criteria: {}, n_runs: 0 },
      { id: 2, name: "S2", hypothesis: "H2", status: "running", notes: "",
        created_at: "2026-05-30", completed_at: null, parameter_space: {},
        pre_registered_criteria: {}, n_runs: 3 },
    ]);
    render(wrap(<Research />));
    await waitFor(() => {
      expect(screen.getByText("S1")).toBeInTheDocument();
      expect(screen.getByText("S2")).toBeInTheDocument();
    });
  });

  it("New Session button opens the modal", async () => {
    const { api } = await import("../api/client");
    (api.listResearchSessions as any).mockResolvedValue([]);
    render(wrap(<Research />));
    fireEvent.click(screen.getByRole("button", { name: /new session/i }));
    await waitFor(() => {
      expect(screen.getByText(/new research session/i)).toBeInTheDocument();
    });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd dashboard && npx vitest run src/pages/Research.test.tsx
```

- [ ] **Step 3: Implement**

```typescript
// dashboard/src/pages/Research.tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Microscope, Plus } from "lucide-react";
import { useResearchSessions } from "../hooks/useResearchSessions";
import { NewSessionModal } from "../components/NewSessionModal";

export function Research() {
  const [modalOpen, setModalOpen] = useState(false);
  const nav = useNavigate();
  const q = useResearchSessions();

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white flex items-center gap-2">
          <Microscope size={22} /> Research
        </h1>
        <button
          onClick={() => setModalOpen(true)}
          className="bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-3 py-1.5 rounded flex items-center gap-1"
        >
          <Plus size={14} /> New Session
        </button>
      </div>

      {q.isLoading && <div className="text-gray-400 text-sm">Loading…</div>}
      {q.error && <div className="text-red-400 text-sm">Failed to load sessions</div>}

      {q.data && q.data.length === 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-10 text-center">
          <Microscope size={32} className="mx-auto text-gray-600 mb-3" />
          <p className="text-gray-400 mb-4">No research sessions yet.</p>
          <button
            onClick={() => setModalOpen(true)}
            className="bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-4 py-2 rounded"
          >
            Create your first session
          </button>
        </div>
      )}

      {q.data && q.data.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-950 text-gray-400 text-xs">
              <tr>
                <th className="px-4 py-2 text-left">Name</th>
                <th className="px-4 py-2 text-left">Status</th>
                <th className="px-4 py-2 text-left">Hypothesis</th>
                <th className="px-4 py-2 text-right">Runs</th>
                <th className="px-4 py-2 text-left">Created</th>
              </tr>
            </thead>
            <tbody>
              {q.data.map((s) => (
                <tr
                  key={s.id}
                  onClick={() => nav(`/research/sessions/${s.id}`)}
                  className="border-t border-gray-800 cursor-pointer hover:bg-gray-800/50 text-gray-200"
                >
                  <td className="px-4 py-2 font-medium">{s.name}</td>
                  <td className="px-4 py-2">{s.status}</td>
                  <td className="px-4 py-2 text-gray-400 truncate max-w-md" title={s.hypothesis}>
                    {s.hypothesis.length > 80 ? s.hypothesis.slice(0, 80) + "…" : s.hypothesis}
                  </td>
                  <td className="px-4 py-2 text-right">{s.n_runs}</td>
                  <td className="px-4 py-2 text-gray-500">{s.created_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <NewSessionModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onCreated={(id) => { setModalOpen(false); nav(`/research/sessions/${id}`); }}
      />
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd dashboard && npx vitest run src/pages/Research.test.tsx
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```
git add dashboard/src/pages/Research.tsx dashboard/src/pages/Research.test.tsx
git commit -m "feat(dashboard): Research sessions list page with empty state

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 15: `ResearchSessionDetail.tsx` page

**Files:**
- Create: `dashboard/src/pages/ResearchSessionDetail.tsx`
- Create: `dashboard/src/pages/ResearchSessionDetail.test.tsx`

- [ ] **Step 1: Write failing tests**

```typescript
// dashboard/src/pages/ResearchSessionDetail.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { ResearchSessionDetail } from "./ResearchSessionDetail";

vi.mock("../api/client", () => ({
  api: {
    getResearchSession: vi.fn(),
    listResearchJobs: vi.fn(),
    cancelResearchJob: vi.fn(),
    generateResearchReport: vi.fn(),
    createResearchSweep: vi.fn(),
  },
}));

vi.mock("../api/hooks", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../api/hooks")>();
  return {
    ...orig,
    useAlgorithms: () => ({ data: [], isLoading: false }),
  };
});

const SESSION = {
  id: 7, name: "Smoke", hypothesis: "ws works", status: "open" as const,
  notes: "", created_at: "2026-05-30", completed_at: null,
  parameter_space: { x: [1, 2] }, pre_registered_criteria: { min_sharpe: 1 },
  n_runs: 0,
};

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/research/sessions/7"]}>
        <Routes>
          <Route path="/research/sessions/:id" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("ResearchSessionDetail", () => {
  it("renders session summary fields", async () => {
    const { api } = await import("../api/client");
    (api.getResearchSession as any).mockResolvedValue(SESSION);
    (api.listResearchJobs as any).mockResolvedValue([]);
    render(wrap(<ResearchSessionDetail />));
    await waitFor(() => {
      expect(screen.getByText("Smoke")).toBeInTheDocument();
      expect(screen.getByText(/open/i)).toBeInTheDocument();
    });
  });

  it("Generate Report disabled when n_runs === 0; enabled when ≥1", async () => {
    const { api } = await import("../api/client");
    (api.getResearchSession as any).mockResolvedValue(SESSION);
    (api.listResearchJobs as any).mockResolvedValue([]);
    const { rerender } = render(wrap(<ResearchSessionDetail />));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /generate report/i })).toBeDisabled();
    });
    (api.getResearchSession as any).mockResolvedValue({ ...SESSION, n_runs: 3 });
    rerender(wrap(<ResearchSessionDetail />));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /generate report/i })).not.toBeDisabled();
    });
  });

  it("renders one ResearchJobRow per job", async () => {
    const { api } = await import("../api/client");
    (api.getResearchSession as any).mockResolvedValue(SESSION);
    (api.listResearchJobs as any).mockResolvedValue([
      { job_id: "j1", session_id: 7, kind: "sweep", status: "completed",
        progress_pct: 1, progress_message: null, run_ids: ["r1"],
        error_message: null, started_at: null, completed_at: null, created_at: null },
      { job_id: "j2", session_id: 7, kind: "sweep", status: "running",
        progress_pct: 0.5, progress_message: "go", run_ids: [],
        error_message: null, started_at: null, completed_at: null, created_at: null },
    ]);
    render(wrap(<ResearchSessionDetail />));
    await waitFor(() => {
      expect(screen.getAllByText(/sweep/i).length).toBeGreaterThanOrEqual(2);
    });
  });

  it("empty jobs state copy when zero jobs", async () => {
    const { api } = await import("../api/client");
    (api.getResearchSession as any).mockResolvedValue(SESSION);
    (api.listResearchJobs as any).mockResolvedValue([]);
    render(wrap(<ResearchSessionDetail />));
    await waitFor(() => {
      expect(screen.getByText(/no jobs yet/i)).toBeInTheDocument();
    });
  });

  it("New Sweep button opens NewSweepModal", async () => {
    const { api } = await import("../api/client");
    (api.getResearchSession as any).mockResolvedValue(SESSION);
    (api.listResearchJobs as any).mockResolvedValue([]);
    render(wrap(<ResearchSessionDetail />));
    await waitFor(() => screen.getByText("Smoke"));
    fireEvent.click(screen.getByRole("button", { name: /new sweep/i }));
    expect(await screen.findByText(/algorithm/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd dashboard && npx vitest run src/pages/ResearchSessionDetail.test.tsx
```

- [ ] **Step 3: Implement**

```typescript
// dashboard/src/pages/ResearchSessionDetail.tsx
import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { useResearchSession, useResearchJobs } from "../hooks/useResearchSession";
import {
  useCancelResearchJob,
  useGenerateResearchReport,
} from "../hooks/useResearchMutations";
import { ResearchSessionSummary } from "../components/ResearchSessionSummary";
import { ResearchJobRow } from "../components/ResearchJobRow";
import { NewSweepModal } from "../components/NewSweepModal";

export function ResearchSessionDetail() {
  const { id } = useParams<{ id: string }>();
  const sessionId = id ? parseInt(id, 10) : null;
  const sessionQ = useResearchSession(sessionId);
  const jobsQ = useResearchJobs(sessionId);
  const cancelMut = useCancelResearchJob(sessionId ?? 0);
  const reportMut = useGenerateResearchReport(sessionId ?? 0);
  const [sweepOpen, setSweepOpen] = useState(false);
  const [reportMsg, setReportMsg] = useState<string | null>(null);

  if (sessionQ.isLoading) {
    return <div className="text-gray-400">Loading…</div>;
  }
  if (sessionQ.error || !sessionQ.data) {
    return (
      <div className="bg-gray-900 border border-red-900 rounded-lg p-6 text-red-400">
        Session not found.
        <Link to="/research" className="ml-3 text-indigo-400 hover:text-indigo-300">
          ← back to sessions
        </Link>
      </div>
    );
  }
  const session = sessionQ.data;

  return (
    <div className="space-y-4">
      <Link
        to="/research"
        className="text-sm text-gray-400 hover:text-gray-200 flex items-center gap-1"
      >
        <ArrowLeft size={14} /> Back to sessions
      </Link>

      <ResearchSessionSummary
        session={session}
        onNewSweep={() => setSweepOpen(true)}
        reportPending={reportMut.isPending}
        onGenerateReport={async () => {
          setReportMsg(null);
          try {
            const r = await reportMut.mutateAsync();
            setReportMsg(`Report written to ${r.markdown_path} (md) + ${r.html_path} (html)`);
          } catch (e) {
            setReportMsg(`Failed: ${e instanceof Error ? e.message : "unknown"}`);
          }
        }}
      />

      {reportMsg && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 text-xs text-gray-300 font-mono">
          {reportMsg}
        </div>
      )}

      <div className="space-y-2">
        <h2 className="text-lg font-semibold text-gray-200">Jobs</h2>
        {jobsQ.data && jobsQ.data.length === 0 && (
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-6 text-center text-gray-400 text-sm">
            No jobs yet. Click <span className="text-white">New Sweep</span> to start one.
          </div>
        )}
        {jobsQ.data?.map((job) => (
          <ResearchJobRow
            key={job.job_id}
            job={job}
            onCancel={(jobId) => void cancelMut.mutate(jobId)}
          />
        ))}
      </div>

      <NewSweepModal
        open={sweepOpen}
        sessionId={sessionId ?? 0}
        onClose={() => setSweepOpen(false)}
      />
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd dashboard && npx vitest run src/pages/ResearchSessionDetail.test.tsx
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```
git add dashboard/src/pages/ResearchSessionDetail.tsx dashboard/src/pages/ResearchSessionDetail.test.tsx
git commit -m "feat(dashboard): ResearchSessionDetail page (summary + jobs + sweep modal)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 16: Wire sidebar nav + routes

**Files:**
- Modify: `dashboard/src/components/Layout.tsx` (NAV_ITEMS)
- Modify: `dashboard/src/App.tsx` (Routes)

- [ ] **Step 1: Add nav entry**

Edit `dashboard/src/components/Layout.tsx` lines 16-24. Add `Microscope` to the lucide-react imports at the top of the file (alongside `LayoutDashboard`, `Database`, etc.), then insert the entry between Backtests and Settings:

```typescript
const NAV_ITEMS = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/accounts", label: "Accounts", icon: Wallet },
  { to: "/data", label: "Data", icon: Database },
  { to: "/workers", label: "Workers", icon: Server },
  { to: "/algorithms", label: "Algorithms", icon: Bot },
  { to: "/backtests", label: "Backtests", icon: FlaskConical },
  { to: "/research", label: "Research", icon: Microscope },
  { to: "/settings", label: "Settings", icon: Settings },
];
```

- [ ] **Step 2: Register routes**

Edit `dashboard/src/App.tsx`. Add the imports near the existing page imports:

```typescript
import { Research } from "./pages/Research";
import { ResearchSessionDetail } from "./pages/ResearchSessionDetail";
```

Inside the `<Routes>` block (around line 49+), add two routes alongside the existing ones:

```typescript
<Route path="/research" element={<Research />} />
<Route path="/research/sessions/:id" element={<ResearchSessionDetail />} />
```

- [ ] **Step 3: Typecheck + smoke**

```
cd dashboard && npx tsc --noEmit
cd dashboard && npx vitest run
```

Both should be clean / all-green.

- [ ] **Step 4: Build**

```
quilt dashboard build 2>&1 | tail -4
```

Expected: clean build, lists the new bundle hash.

- [ ] **Step 5: Commit**

```
git add dashboard/src/components/Layout.tsx dashboard/src/App.tsx
git commit -m "feat(dashboard): Research sidebar entry + routes

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 17: End-to-end manual smoke test

This is a hand-walk, not an automated test. Skip the commit step at the end since nothing changes.

- [ ] **Step 1: Restart coord, refresh dashboard**

```
quilt coord restart
```

Hard-refresh the dashboard tab (`Ctrl+Shift+R` / `Cmd+Shift+R`).

- [ ] **Step 2: Sidebar shows Research**

Confirm "Research" entry appears between Backtests and Settings, with the Microscope icon.

- [ ] **Step 3: Empty state**

Click Research → expect the empty-state card with "Create your first session" CTA.

- [ ] **Step 4: Create a session**

Click "Create your first session" (or "New Session"). Fill:
- Name: `smoke-test-2026-05-30`
- Hypothesis: `Validate research lab dashboard end-to-end.`
- Parameter space: `{"vol_target": [0.10, 0.15]}`
- Pre-registered criteria: `{"min_sharpe": 0.5}`

Click Create. Expect navigation to `/research/sessions/{id}` with summary populated.

- [ ] **Step 5: Start a sweep**

Click New Sweep. Pick an installed algorithm from the dropdown. Leave defaults. Click Start sweep.

A new `queued` job row appears immediately (optimistic insert). Within a few seconds it transitions to `running` via WS push, the progress bar moves, the message updates.

- [ ] **Step 6: Cancel the sweep mid-run (optional)**

Click Cancel on the running row. Within 1-2s the status flips to `cancelled` via WS.

- [ ] **Step 7: Run a small sweep to completion**

Start another sweep with `max_trials=3`. Wait until completed (progress reaches 100%). Expand the row → see the 3 run links. Click one → navigate to `/backtests/runs/{id}`. Use browser back to return.

- [ ] **Step 8: Generate Report**

Back on the session detail page, click Generate Report. Wait briefly. Toast/text appears showing the markdown + html file paths under `data/research_reports/{session_id}/`.

Verify on disk:

```
ls data/research_reports/{session_id}/
```

Expect `report.md` and `report.html` (or whatever the backend names them).

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| `/api/algorithms` adds `manifest_path` | Task 1 |
| `POST /sessions/{id}/sweep` accepts `algorithm_id` alt | Task 2 |
| `POST /sessions/{id}/walk-forward` accepts `algorithm_id` alt | Task 2 |
| `ResearchJobManager` gains `on_job_update` callback | Task 3 |
| Lifespan wires `on_job_update` to `manager.broadcast_to_dashboards` | Task 4 |
| WS event envelope `{ type: "research_job", session_id, job_id, ... }` | Tasks 3+4 (server) + Task 8 (client) |
| Sidebar nav entry "Research" with Microscope icon | Task 16 |
| Routes `/research` and `/research/sessions/:id` | Task 16 |
| `Research.tsx` list page with empty state | Task 14 |
| `ResearchSessionDetail.tsx` with summary + jobs + actions | Task 15 |
| `NewSessionModal.tsx` (4-field create form) | Task 10 |
| `NewSweepModal.tsx` (6-field sweep form) | Task 11 |
| `ResearchJobRow.tsx` (status, progress, cancel, run links) | Task 12 |
| `ResearchSessionSummary.tsx` (header card) | Task 13 |
| `JsonTextField.tsx` (reusable JSON editor) | Task 9 |
| `useResearchSessions` / `useResearchSession` / `useResearchJobs` | Task 6 |
| `useCreateResearchSession` / `useCreateResearchSweep` / `useCancelResearchJob` / `useGenerateResearchReport` | Task 7 |
| Live updates via `useWebSocketSync` `research_job` subscription | Task 8 |
| Loading / empty / error states | Tasks 14, 15, 9 |
| Backend tests for the 3 backend additions | Tasks 1, 2, 3 |
| Frontend tests for all 5 components + 2 pages | Tasks 9–15 |
| Generate Report surfaces file paths (no in-browser viewer) | Task 15 |

All spec requirements have a task.

**Placeholder scan:** no TBD / TODO / "fill in" tokens; every code-bearing step has actual code.

**Type consistency:**
- `ResearchSession`, `ResearchJob`, `CreateSessionRequest`, `CreateSweepRequest`, `GenerateReportResponse` types defined in Task 5 (client.ts), imported consistently in later tasks.
- `keys.researchSessions()`, `keys.researchSession(id)`, `keys.researchJobs(sid)`, `keys.researchJob(sid, jid)` defined in Task 5, used identically in Tasks 6, 7, 8.
- Status vocabularies match the backend models exactly (verified against `coordinator/database/models.py:474-494`).
- `on_job_update: Callable[[dict], Awaitable[None]] | None` consistent between Task 3 (constructor) and Task 4 (lifespan wiring).

**Plan complete.**
