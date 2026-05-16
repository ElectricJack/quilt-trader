# Running Algorithm UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the instance/run UX with a single "Algorithm Deployment" concept whose detail page is a live, backtest-style report fed by a per-tick streaming pipeline that shares code with the existing backtest finalizer.

**Architecture:** Internal data model (`AlgorithmInstance`, `AlgorithmRun`) is unchanged; the work is (a) public API/UI renames, (b) optimistic + ws-broadcast status updates, (c) a new live streaming pipeline mirroring `backtest_writer`/`backtest_finalizer`, (d) a worker → coordinator → dashboard activity stream backed by a new `WorkerActivity` table, (e) a new `AlgorithmDeploymentReport` table populated by a periodic finalizer, and (f) a rewrite of `InstanceDetail.tsx` into `/deployments/:id` that renders the same report components as the backtest detail page.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, Alembic, websockets, pandas, pyarrow, quantstats, pytest + pytest-asyncio. Dashboard: React 18, TypeScript, React Query, React Router, Zod, react-hook-form, lightweight-charts, Tailwind, vitest + @testing-library/react.

**Reference spec:** `docs/superpowers/specs/2026-05-16-running-algorithm-ux-design.md`.

---

## Conventions

- All commits are atomic per task. Commit message style: `<type>(<area>): <subject>` (e.g., `fix(api): UTC-safe timestamp serialization`).
- Backend test command: `pytest tests/coordinator/<path>.py -v`
- Frontend test command: `cd dashboard && npm test -- <path>`
- Frontend typecheck: `cd dashboard && npm run typecheck`
- Whenever a backend task adds a new column or table, an Alembic migration step is included in that task.

---

## Milestone 1 — Plumbing Fixes (Heartbeat tz, Worker Offline)

Quick wins. These ship correctness fixes that the user will feel immediately, and they unblock the rest of the work.

### Task 1.1: UTC-safe timestamp serialization helper

**Files:**
- Create: `coordinator/api/serialization.py`
- Test: `tests/coordinator/test_api_serialization.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_api_serialization.py
from datetime import datetime, timezone, timedelta
from coordinator.api.serialization import to_iso_utc


def test_to_iso_utc_handles_none():
    assert to_iso_utc(None) is None


def test_to_iso_utc_assumes_utc_for_naive_datetimes():
    dt = datetime(2026, 5, 16, 12, 34, 56)  # naive
    assert to_iso_utc(dt) == "2026-05-16T12:34:56Z"


def test_to_iso_utc_converts_aware_datetimes_to_utc():
    dt = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone(timedelta(hours=-7)))
    assert to_iso_utc(dt) == "2026-05-16T19:00:00Z"


def test_to_iso_utc_preserves_utc_datetimes():
    dt = datetime(2026, 5, 16, 12, 34, 56, tzinfo=timezone.utc)
    assert to_iso_utc(dt) == "2026-05-16T12:34:56Z"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/coordinator/test_api_serialization.py -v`
Expected: ImportError — `coordinator.api.serialization` doesn't exist.

- [ ] **Step 3: Write the implementation**

```python
# coordinator/api/serialization.py
"""Timestamp serialization helpers for API responses.

Why this exists: SQLite returns naive datetimes even when columns are declared
DateTime(timezone=True), so .isoformat() emits offset-less strings that the
browser interprets as local time. This produces wildly wrong "ago" math —
e.g. -25187s ago for a UTC-7 user. Always route timestamps through this
helper before serializing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def to_iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    iso = dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    return iso.replace("+00:00", "Z")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/coordinator/test_api_serialization.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add coordinator/api/serialization.py tests/coordinator/test_api_serialization.py
git commit -m "feat(api): add UTC-safe timestamp serialization helper"
```

### Task 1.2: Apply `to_iso_utc` across all route response builders

**Files:**
- Modify: `coordinator/api/routes/workers.py` (every `.isoformat()` call)
- Modify: `coordinator/api/routes/algorithms.py`
- Modify: `coordinator/api/routes/accounts.py`
- Modify: `coordinator/api/routes/runs.py`
- Modify: `coordinator/api/routes/backtest_runs.py`
- Modify: any other route file in `coordinator/api/routes/` that calls `.isoformat()`
- Test: `tests/coordinator/test_workers_api_tz.py` (new)

- [ ] **Step 1: Find every `.isoformat()` in routes**

Run: `grep -rn "\.isoformat()" coordinator/api/routes/`

- [ ] **Step 2: Write a failing test asserting the workers endpoint emits a Z suffix**

```python
# tests/coordinator/test_workers_api_tz.py
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from coordinator.main import create_app
from coordinator.database.models import Worker
from coordinator.api.dependencies import get_container


@pytest.mark.asyncio
async def test_worker_response_emits_utc_z_suffix():
    app = create_app()
    async with app.router.lifespan_context(app):
        container = get_container()
        async with container.session_factory() as session:
            from datetime import datetime, timezone
            w = Worker(name="t", status="online",
                       last_heartbeat=datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc))
            session.add(w)
            await session.commit()
            wid = w.id
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get(f"/api/workers/{wid}")
        assert r.status_code == 200
        body = r.json()
        assert body["last_heartbeat"].endswith("Z"), body["last_heartbeat"]
```

- [ ] **Step 3: Run the test, expect it to fail**

Run: `pytest tests/coordinator/test_workers_api_tz.py -v`
Expected: FAIL — current isoformat emits no Z.

- [ ] **Step 4: Replace `.isoformat()` calls with `to_iso_utc(dt)` in every routes file**

In each file from Step 1, change:
```python
"last_heartbeat": worker.last_heartbeat.isoformat() if worker.last_heartbeat else None,
```
to:
```python
"last_heartbeat": to_iso_utc(worker.last_heartbeat),
```
And add to the imports at the top of each modified file:
```python
from coordinator.api.serialization import to_iso_utc
```
Apply to `created_at`, `updated_at`, `started_at`, `stopped_at`, `completed_at`, `opened_at`, `closed_at`, `last_success`, `installed_at`, `timestamp` etc. — every datetime.

- [ ] **Step 5: Re-run the test and the full routes test suite**

Run: `pytest tests/coordinator/test_workers_api_tz.py tests/coordinator -v -k "api"`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add coordinator/api/routes tests/coordinator/test_workers_api_tz.py
git commit -m "fix(api): emit UTC Z-suffixed timestamps in all route responses"
```

### Task 1.3: Worker offline transition on websocket disconnect

**Files:**
- Modify: `coordinator/api/websocket.py` (disconnect handler, ConnectionManager)
- Test: `tests/coordinator/test_websocket_handlers.py` (extend)

- [ ] **Step 1: Write the failing test**

Add to `tests/coordinator/test_websocket_handlers.py`:
```python
@pytest.mark.asyncio
async def test_worker_marked_offline_on_disconnect(app_with_db):
    app, container = app_with_db
    async with container.session_factory() as session:
        worker = Worker(name="w", status="online")
        session.add(worker)
        await session.commit()
        wid = worker.id

    from coordinator.api.websocket import manager, handle_worker_disconnect
    fake = FakeWebSocket()
    manager.register_worker(wid, fake)
    await handle_worker_disconnect(fake)

    async with container.session_factory() as session:
        w = (await session.execute(select(Worker).where(Worker.id == wid))).scalar_one()
        assert w.status == "offline"
```

(Add an `app_with_db` fixture in `conftest.py` if not already present; check the file before adding.)

- [ ] **Step 2: Run the test, verify it fails**

Run: `pytest tests/coordinator/test_websocket_handlers.py::test_worker_marked_offline_on_disconnect -v`
Expected: FAIL — `handle_worker_disconnect` doesn't exist.

- [ ] **Step 3: Implement disconnect handler**

In `coordinator/api/websocket.py`, replace the `worker_websocket` route's disconnect path. Extract the disconnect logic into a function so it's testable:

```python
async def handle_worker_disconnect(websocket: WebSocket) -> None:
    """Mark a worker offline when its websocket disconnects and broadcast."""
    from sqlalchemy import select
    from coordinator.database.models import Worker
    # Find the worker id from the connection map *before* removing
    worker_id = None
    for wid, ws in list(manager.worker_connections.items()):
        if ws is websocket:
            worker_id = wid
            break
    manager.disconnect_worker_by_socket(websocket)
    if worker_id is None:
        return
    try:
        container = get_container()
        async with container.session_factory() as session:
            worker = (await session.execute(
                select(Worker).where(Worker.id == worker_id)
            )).scalar_one_or_none()
            if worker is not None and worker.status != "offline":
                worker.status = "offline"
                await session.commit()
                await manager.broadcast_to_dashboards({
                    "type": "worker_disconnected",
                    "worker_id": worker_id,
                })
    except Exception:
        logger.exception("Failed to mark worker %s offline on disconnect", worker_id)


@router.websocket("/ws/worker")
async def worker_websocket(websocket: WebSocket):
    await manager.accept_worker(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            await handle_worker_message(websocket, data)
    except WebSocketDisconnect:
        logger.info("Worker disconnected")
    finally:
        await handle_worker_disconnect(websocket)
```

- [ ] **Step 4: Run test, verify it passes**

Run: `pytest tests/coordinator/test_websocket_handlers.py::test_worker_marked_offline_on_disconnect -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add coordinator/api/websocket.py tests/coordinator/test_websocket_handlers.py
git commit -m "fix(ws): mark workers offline on websocket disconnect"
```

### Task 1.4: Periodic stale-heartbeat sweeper

**Files:**
- Create: `coordinator/services/worker_health.py`
- Modify: `coordinator/main.py` (start the sweeper in lifespan)
- Test: `tests/coordinator/services/test_worker_health.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/services/test_worker_health.py
import asyncio
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import select

from coordinator.database.models import Worker


@pytest.mark.asyncio
async def test_sweeper_marks_stale_workers_offline(app_with_db):
    app, container = app_with_db
    async with container.session_factory() as session:
        stale = Worker(
            name="stale", status="online",
            last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=120),
        )
        fresh = Worker(
            name="fresh", status="online",
            last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=10),
        )
        session.add_all([stale, fresh])
        await session.commit()
        stale_id, fresh_id = stale.id, fresh.id

    from coordinator.services.worker_health import sweep_stale_workers
    await sweep_stale_workers(container.session_factory, offline_after_seconds=60)

    async with container.session_factory() as session:
        s = (await session.execute(select(Worker).where(Worker.id == stale_id))).scalar_one()
        f = (await session.execute(select(Worker).where(Worker.id == fresh_id))).scalar_one()
        assert s.status == "offline"
        assert f.status == "online"
```

- [ ] **Step 2: Run test, verify it fails**

Run: `pytest tests/coordinator/services/test_worker_health.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement the sweeper**

```python
# coordinator/services/worker_health.py
"""Background task that marks workers offline when their heartbeat goes stale."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from coordinator.database.models import Worker

logger = logging.getLogger(__name__)


async def sweep_stale_workers(
    session_factory: async_sessionmaker[AsyncSession],
    offline_after_seconds: int,
) -> list[str]:
    """Mark any 'online' worker whose heartbeat is older than the threshold offline.

    Returns the list of worker ids that were transitioned.
    """
    threshold = datetime.now(timezone.utc) - timedelta(seconds=offline_after_seconds)
    transitioned: list[str] = []
    async with session_factory() as session:
        result = await session.execute(
            select(Worker).where(
                Worker.status == "online",
                Worker.last_heartbeat < threshold,
            )
        )
        for worker in result.scalars().all():
            worker.status = "offline"
            transitioned.append(worker.id)
        if transitioned:
            await session.commit()
    return transitioned


async def run_worker_health_loop(
    session_factory: async_sessionmaker[AsyncSession],
    interval_seconds: int = 30,
    offline_after_seconds: int = 60,
) -> None:
    """Run the sweeper on a periodic loop. Cancellable via the task."""
    while True:
        try:
            transitioned = await sweep_stale_workers(
                session_factory, offline_after_seconds
            )
            for wid in transitioned:
                logger.info("Marked stale worker %s offline", wid)
        except Exception:
            logger.exception("Worker health sweep failed")
        await asyncio.sleep(interval_seconds)
```

- [ ] **Step 4: Wire the loop into `coordinator/main.py` lifespan**

In `coordinator/main.py`, find the `lifespan` async context and add the background task:
```python
from coordinator.services.worker_health import run_worker_health_loop

# ... inside lifespan, after container is built:
health_task = asyncio.create_task(
    run_worker_health_loop(
        container.session_factory,
        interval_seconds=int(os.environ.get("QT_WORKER_HEALTH_INTERVAL_SECONDS", "30")),
        offline_after_seconds=int(os.environ.get("QT_WORKER_OFFLINE_TIMEOUT_SECONDS", "60")),
    )
)
try:
    yield
finally:
    health_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await health_task
```

Add the necessary imports (`asyncio`, `contextlib`, `os`) at the top if not present.

- [ ] **Step 5: Run the sweeper test and the lifespan test**

Run: `pytest tests/coordinator/services/test_worker_health.py tests/coordinator/test_lifespan_wiring.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add coordinator/services/worker_health.py coordinator/main.py tests/coordinator/services/test_worker_health.py
git commit -m "feat(coordinator): periodic worker-health sweeper transitions stale workers to offline"
```

---

## Milestone 2 — Status Vocabulary + Hydrated Names on List Endpoints

Foundation for the rest of the UI work.

### Task 2.1: Canonical deployment status vocabulary in StatusBadge

**Files:**
- Modify: `dashboard/src/components/StatusBadge.tsx`
- Test: `dashboard/src/components/StatusBadge.test.tsx` (new)

- [ ] **Step 1: Read the current StatusBadge implementation**

Run: `Read dashboard/src/components/StatusBadge.tsx`

- [ ] **Step 2: Write the failing tests**

```tsx
// dashboard/src/components/StatusBadge.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it.each([
    ["stopped", "Stopped"],
    ["starting", "Starting"],
    ["running", "Running"],
    ["stopping", "Stopping"],
    ["error", "Error"],
    ["offline", "Offline"],
    ["online", "Online"],
  ])("renders %s as %s", (status, label) => {
    render(<StatusBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("falls back to a neutral pill for unknown statuses", () => {
    render(<StatusBadge status="something_weird" />);
    expect(screen.getByText("something_weird")).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run, verify fail**

Run: `cd dashboard && npm test -- StatusBadge`
Expected: missing labels or missing fallback.

- [ ] **Step 4: Implement explicit mapping**

Update `StatusBadge.tsx` so the component has a `STATUSES` lookup table:
```tsx
const STATUSES: Record<string, { label: string; classes: string }> = {
  stopped:  { label: "Stopped",  classes: "bg-gray-800 text-gray-300 border-gray-700" },
  starting: { label: "Starting", classes: "bg-yellow-900/40 text-yellow-300 border-yellow-800" },
  running:  { label: "Running",  classes: "bg-green-900/40 text-green-300 border-green-800" },
  stopping: { label: "Stopping", classes: "bg-yellow-900/40 text-yellow-300 border-yellow-800" },
  error:    { label: "Error",    classes: "bg-red-900/40 text-red-300 border-red-800" },
  offline:  { label: "Offline",  classes: "bg-gray-800 text-gray-400 border-gray-700" },
  online:   { label: "Online",   classes: "bg-green-900/40 text-green-300 border-green-800" },
  // Backtest run statuses kept for that page:
  queued:           { label: "Queued",          classes: "bg-gray-800 text-gray-300 border-gray-700" },
  downloading_data: { label: "Downloading",     classes: "bg-blue-900/40 text-blue-300 border-blue-800" },
  completed:        { label: "Completed",       classes: "bg-green-900/40 text-green-300 border-green-800" },
  failed:           { label: "Failed",          classes: "bg-red-900/40 text-red-300 border-red-800" },
  cancelled:        { label: "Cancelled",       classes: "bg-gray-800 text-gray-400 border-gray-700" },
  // Install statuses:
  pending:   { label: "Pending",   classes: "bg-amber-900/40 text-amber-300 border-amber-800" },
  installed: { label: "Installed", classes: "bg-green-900/40 text-green-300 border-green-800" },
  claimed:   { label: "Claimed",   classes: "bg-green-900/40 text-green-300 border-green-800" },
};

export function StatusBadge({ status }: { status: string }) {
  const cfg = STATUSES[status];
  if (!cfg) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs border bg-gray-800 text-gray-400 border-gray-700">
        {status}
      </span>
    );
  }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs border ${cfg.classes}`}>
      {cfg.label}
    </span>
  );
}
```

- [ ] **Step 5: Run tests + typecheck**

Run: `cd dashboard && npm test -- StatusBadge && npm run typecheck`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/components/StatusBadge.tsx dashboard/src/components/StatusBadge.test.tsx
git commit -m "feat(ui): canonical status vocabulary with neutral fallback in StatusBadge"
```

### Task 2.2: Backend — hydrate deployment list with names

**Files:**
- Create: `coordinator/api/routes/deployments.py`
- Modify: `coordinator/main.py` (register the new router)
- Test: `tests/coordinator/test_deployments_api.py`

The internal table stays `algorithm_instances`. This task adds a *new* `/api/deployments*` namespace that wraps the same data and adds hydrated names. Old `/api/instances*` routes stay untouched in this task — we'll remove them in Milestone 7.

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_deployments_api.py
import pytest
from httpx import AsyncClient, ASGITransport
from coordinator.main import create_app
from coordinator.api.dependencies import get_container
from coordinator.database.models import Worker, Account, Algorithm, AlgorithmInstance


@pytest.mark.asyncio
async def test_list_deployments_includes_hydrated_names():
    app = create_app()
    async with app.router.lifespan_context(app):
        container = get_container()
        async with container.session_factory() as session:
            algo = Algorithm(repo_url="x", name="TrendBot")
            acct = Account(name="Paper-1", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
            worker = Worker(name="Pi-1", status="online")
            session.add_all([algo, acct, worker])
            await session.flush()
            inst = AlgorithmInstance(algorithm_id=algo.id, account_id=acct.id, worker_id=worker.id, status="stopped")
            session.add(inst)
            await session.commit()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/deployments")
        body = r.json()
        assert r.status_code == 200
        assert len(body) == 1
        d = body[0]
        assert d["algorithm_name"] == "TrendBot"
        assert d["account_name"] == "Paper-1"
        assert d["worker_name"] == "Pi-1"
        assert d["status"] == "stopped"
```

- [ ] **Step 2: Run test, verify fail**

Run: `pytest tests/coordinator/test_deployments_api.py -v`
Expected: 404 / module missing.

- [ ] **Step 3: Implement the new router**

```python
# coordinator/api/routes/deployments.py
"""Public 'deployments' API — the user-facing name for AlgorithmInstance.

Wraps the existing instance model and joins in algorithm/account/worker names
so the frontend never has to display GUIDs. The original /api/instances/*
routes still exist for one release for backwards compatibility.
"""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.api.serialization import to_iso_utc
from coordinator.database.models import (
    Account, Algorithm, AlgorithmInstance, AlgorithmRun, Worker,
)

router = APIRouter(prefix="/api/deployments", tags=["deployments"])


def _deployment_to_response(
    inst: AlgorithmInstance,
    algo_name: str,
    account_name: str,
    worker_name: str,
) -> dict:
    return {
        "id": inst.id,
        "algorithm_id": inst.algorithm_id,
        "account_id": inst.account_id,
        "worker_id": inst.worker_id,
        "algorithm_name": algo_name,
        "account_name": account_name,
        "worker_name": worker_name,
        "status": inst.status,
        "active_run_id": inst.active_run_id,
        "config_values": inst.config_values,
        "lifetime_metrics": inst.lifetime_metrics,
        "created_at": to_iso_utc(inst.created_at),
        "updated_at": to_iso_utc(inst.updated_at),
    }


class DeploymentUpdate(BaseModel):
    config_values: Optional[dict] = None


@router.get("")
async def list_deployments(
    algorithm_id: Optional[str] = None,
    worker_id: Optional[str] = None,
    account_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(AlgorithmInstance, Algorithm.name, Account.name, Worker.name)
        .join(Algorithm, AlgorithmInstance.algorithm_id == Algorithm.id)
        .join(Account, AlgorithmInstance.account_id == Account.id)
        .join(Worker, AlgorithmInstance.worker_id == Worker.id)
    )
    if algorithm_id:
        stmt = stmt.where(AlgorithmInstance.algorithm_id == algorithm_id)
    if worker_id:
        stmt = stmt.where(AlgorithmInstance.worker_id == worker_id)
    if account_id:
        stmt = stmt.where(AlgorithmInstance.account_id == account_id)
    rows = (await db.execute(stmt)).all()
    return [_deployment_to_response(inst, a, ac, w) for inst, a, ac, w in rows]


@router.get("/{deployment_id}")
async def get_deployment(deployment_id: str, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(AlgorithmInstance, Algorithm.name, Account.name, Worker.name)
        .join(Algorithm, AlgorithmInstance.algorithm_id == Algorithm.id)
        .join(Account, AlgorithmInstance.account_id == Account.id)
        .join(Worker, AlgorithmInstance.worker_id == Worker.id)
        .where(AlgorithmInstance.id == deployment_id)
    )
    row = (await db.execute(stmt)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    inst, a, ac, w = row
    return _deployment_to_response(inst, a, ac, w)


@router.patch("/{deployment_id}")
async def update_deployment(
    deployment_id: str, body: DeploymentUpdate, db: AsyncSession = Depends(get_db),
):
    inst = (await db.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.id == deployment_id)
    )).scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    if body.config_values is not None:
        inst.config_values = body.config_values
    await db.flush()
    return {"ok": True}


@router.delete("/{deployment_id}", status_code=204)
async def delete_deployment(deployment_id: str, db: AsyncSession = Depends(get_db)):
    inst = (await db.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.id == deployment_id)
    )).scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    await db.delete(inst)


@router.get("/{deployment_id}/runs")
async def list_runs(deployment_id: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(AlgorithmRun)
        .where(AlgorithmRun.instance_id == deployment_id)
        .order_by(AlgorithmRun.run_number.desc())
    )).scalars().all()
    return [
        {
            "id": r.id,
            "run_number": r.run_number,
            "status": r.status,
            "started_at": to_iso_utc(r.started_at),
            "stopped_at": to_iso_utc(r.stopped_at),
            "starting_equity": r.starting_equity,
            "ending_equity": r.ending_equity,
            "net_pnl": r.net_pnl,
            "unrealized_pnl": r.unrealized_pnl,
            "total_fees": r.total_fees,
            "total_slippage": r.total_slippage,
            "trade_count": r.trade_count,
            "metrics": r.metrics,
        }
        for r in rows
    ]
```

- [ ] **Step 4: Register the router in `coordinator/main.py`**

Find where other routers are registered (`app.include_router(...)`) and add:
```python
from coordinator.api.routes import deployments as deployments_routes
app.include_router(deployments_routes.router)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/coordinator/test_deployments_api.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add coordinator/api/routes/deployments.py coordinator/main.py tests/coordinator/test_deployments_api.py
git commit -m "feat(api): /api/deployments routes with hydrated algorithm/account/worker names"
```

### Task 2.3: Frontend types + hooks for deployments

**Files:**
- Modify: `dashboard/src/types/index.ts`
- Modify: `dashboard/src/api/hooks.ts`
- Modify: `dashboard/src/api/client.ts` (if endpoints are listed there)

- [ ] **Step 1: Add the `Deployment` type alongside `AlgorithmInstance`**

In `dashboard/src/types/index.ts`, add:
```typescript
export interface Deployment {
  id: string;
  algorithm_id: string;
  account_id: string;
  worker_id: string;
  algorithm_name: string;
  account_name: string;
  worker_name: string;
  status: string;
  active_run_id: string | null;
  config_values: Record<string, unknown> | null;
  lifetime_metrics: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}
```

Keep the existing `AlgorithmInstance` type — it's still used internally in some places. We'll converge later.

- [ ] **Step 2: Add hooks `useDeployments`, `useDeployment`, `useDeploymentRuns`, `useUpdateDeployment`, `useDeleteDeployment`**

In `dashboard/src/api/hooks.ts`, mirror existing instance hooks:
```typescript
export const useDeployments = (params?: { algorithm_id?: string; worker_id?: string; account_id?: string }) =>
  useQuery({
    queryKey: ["deployments", params],
    queryFn: async () => {
      const qs = new URLSearchParams();
      if (params?.algorithm_id) qs.set("algorithm_id", params.algorithm_id);
      if (params?.worker_id) qs.set("worker_id", params.worker_id);
      if (params?.account_id) qs.set("account_id", params.account_id);
      const r = await client.get<Deployment[]>(`/api/deployments?${qs}`);
      return r.data;
    },
  });

export const useDeployment = (id: string) =>
  useQuery({
    queryKey: ["deployment", id],
    queryFn: async () => (await client.get<Deployment>(`/api/deployments/${id}`)).data,
    enabled: !!id,
  });

export const useDeploymentRuns = (id: string) =>
  useQuery({
    queryKey: ["deployment-runs", id],
    queryFn: async () => (await client.get<AlgorithmRun[]>(`/api/deployments/${id}/runs`)).data,
    enabled: !!id,
  });

export const useUpdateDeployment = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: { config_values?: Record<string, unknown> } }) =>
      (await client.patch(`/api/deployments/${id}`, body)).data,
    onSuccess: (_d, { id }) => {
      qc.invalidateQueries({ queryKey: ["deployment", id] });
      qc.invalidateQueries({ queryKey: ["deployments"] });
    },
  });
};

export const useDeleteDeployment = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await client.delete(`/api/deployments/${id}`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["deployments"] }),
  });
};
```

- [ ] **Step 3: Typecheck + run frontend tests**

Run: `cd dashboard && npm run typecheck && npm test`
Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/types/index.ts dashboard/src/api/hooks.ts
git commit -m "feat(dashboard): Deployment type and React Query hooks"
```

---

## Milestone 3 — Optimistic + Broadcast Deployment Status

Solves the "UI is slow to start" complaint.

### Task 3.1: Start/Stop endpoints with optimistic writes

**Files:**
- Modify: `coordinator/api/routes/deployments.py` (extend with start/stop)
- Modify: `coordinator/api/websocket.py` (replace `start_instance` ws verb with broadcast helpers)
- Test: `tests/coordinator/test_deployments_start_stop.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_deployments_start_stop.py
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from coordinator.main import create_app
from coordinator.api.dependencies import get_container
from coordinator.api.websocket import manager
from coordinator.database.models import (
    Worker, Account, Algorithm, AlgorithmInstance, AlgorithmRun,
)


class FakeWorkerWS:
    def __init__(self):
        self.sent = []
    async def send_json(self, data):
        self.sent.append(data)


@pytest.mark.asyncio
async def test_start_writes_starting_status_and_creates_run_immediately():
    app = create_app()
    async with app.router.lifespan_context(app):
        container = get_container()
        async with container.session_factory() as session:
            algo = Algorithm(repo_url="x", name="A")
            acct = Account(name="A", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
            worker = Worker(name="W", status="online")
            session.add_all([algo, acct, worker])
            await session.flush()
            inst = AlgorithmInstance(
                algorithm_id=algo.id, account_id=acct.id,
                worker_id=worker.id, status="stopped",
            )
            session.add(inst)
            await session.commit()
            wid, did = worker.id, inst.id
        fake_ws = FakeWorkerWS()
        manager.register_worker(wid, fake_ws)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(f"/api/deployments/{did}/start")
        assert r.status_code == 200
        async with container.session_factory() as session:
            inst = (await session.execute(
                select(AlgorithmInstance).where(AlgorithmInstance.id == did)
            )).scalar_one()
            assert inst.status == "starting"
            assert inst.active_run_id is not None
            run = (await session.execute(
                select(AlgorithmRun).where(AlgorithmRun.id == inst.active_run_id)
            )).scalar_one()
            assert run.status == "running"
            assert run.run_number == 1
        assert any(m["type"] == "start_instance" for m in fake_ws.sent)


@pytest.mark.asyncio
async def test_start_when_worker_offline_returns_502():
    app = create_app()
    async with app.router.lifespan_context(app):
        container = get_container()
        async with container.session_factory() as session:
            algo = Algorithm(repo_url="x", name="A")
            acct = Account(name="A", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
            worker = Worker(name="W", status="offline")
            session.add_all([algo, acct, worker])
            await session.flush()
            inst = AlgorithmInstance(
                algorithm_id=algo.id, account_id=acct.id,
                worker_id=worker.id, status="stopped",
            )
            session.add(inst)
            await session.commit()
            did = inst.id
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(f"/api/deployments/{did}/start")
        assert r.status_code == 502
        async with container.session_factory() as session:
            inst = (await session.execute(
                select(AlgorithmInstance).where(AlgorithmInstance.id == did)
            )).scalar_one()
            assert inst.status == "stopped"
```

- [ ] **Step 2: Run, verify fail**

Run: `pytest tests/coordinator/test_deployments_start_stop.py -v`
Expected: 404 / not implemented.

- [ ] **Step 3: Implement start/stop in `deployments.py`**

Add to `coordinator/api/routes/deployments.py`:
```python
from sqlalchemy import func
from coordinator.api.websocket import manager as ws_manager


async def _broadcast_status_changed(deployment_id: str, status: str, active_run_id: Optional[str]) -> None:
    await ws_manager.broadcast_to_dashboards({
        "type": "deployment_status_changed",
        "deployment_id": deployment_id,
        "status": status,
        "active_run_id": active_run_id,
    })


@router.post("/{deployment_id}/start")
async def start_deployment(deployment_id: str, db: AsyncSession = Depends(get_db)):
    inst = (await db.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.id == deployment_id)
    )).scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    if inst.status not in ("stopped", "error"):
        raise HTTPException(status_code=409, detail=f"Cannot start deployment in status {inst.status!r}")

    worker_ws = ws_manager.worker_connections.get(inst.worker_id)
    if worker_ws is None:
        raise HTTPException(status_code=502, detail="Worker offline")

    # Allocate next run number
    next_n = (await db.execute(
        select(func.coalesce(func.max(AlgorithmRun.run_number), 0))
        .where(AlgorithmRun.instance_id == inst.id)
    )).scalar_one() + 1
    run = AlgorithmRun(instance_id=inst.id, run_number=next_n, status="running")
    db.add(run)
    await db.flush()

    inst.status = "starting"
    inst.active_run_id = run.id
    await db.commit()

    await _broadcast_status_changed(inst.id, "starting", run.id)
    try:
        await worker_ws.send_json({
            "type": "start_instance",
            "instance_id": inst.id,
            "config": inst.config_values or {},
            "persisted_state": inst.persisted_state,
        })
    except Exception:
        # Mark error if we couldn't reach the worker
        inst.status = "error"
        run.status = "error"
        await db.commit()
        await _broadcast_status_changed(inst.id, "error", run.id)
        raise HTTPException(status_code=502, detail="Failed to reach worker")
    return {"ok": True, "active_run_id": run.id}


@router.post("/{deployment_id}/stop")
async def stop_deployment(deployment_id: str, db: AsyncSession = Depends(get_db)):
    inst = (await db.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.id == deployment_id)
    )).scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    if inst.status not in ("running", "starting"):
        raise HTTPException(status_code=409, detail=f"Cannot stop deployment in status {inst.status!r}")

    worker_ws = ws_manager.worker_connections.get(inst.worker_id)
    inst.status = "stopping"
    await db.commit()
    await _broadcast_status_changed(inst.id, "stopping", inst.active_run_id)
    if worker_ws is not None:
        try:
            await worker_ws.send_json({"type": "stop_instance", "instance_id": inst.id})
        except Exception:
            pass  # The next status update from the worker (or timeout) reconciles
    return {"ok": True}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/coordinator/test_deployments_start_stop.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add coordinator/api/routes/deployments.py tests/coordinator/test_deployments_start_stop.py
git commit -m "feat(api): optimistic start/stop endpoints with status broadcast"
```

### Task 3.2: Worker → coordinator status messages broadcast to dashboards

**Files:**
- Modify: `coordinator/api/websocket.py` (`instance_started`, `instance_stopped`, `instance_error` handlers add a broadcast call)
- Test: `tests/coordinator/test_websocket_handlers.py` (extend)

- [ ] **Step 1: Add failing test**

```python
@pytest.mark.asyncio
async def test_instance_started_broadcasts_status_change(app_with_db):
    app, container = app_with_db
    async with container.session_factory() as session:
        algo = Algorithm(repo_url="x", name="A")
        acct = Account(name="A", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
        w = Worker(name="W", status="online")
        session.add_all([algo, acct, w]); await session.flush()
        inst = AlgorithmInstance(algorithm_id=algo.id, account_id=acct.id, worker_id=w.id, status="starting")
        session.add(inst); await session.commit()
        did = inst.id

    from coordinator.api.websocket import manager, handle_worker_message
    dashboard_ws = FakeWebSocket()
    manager.dashboard_connections.append(dashboard_ws)

    worker_ws = FakeWebSocket()
    await handle_worker_message(worker_ws, {"type": "instance_started", "instance_id": did})

    assert any(
        m.get("type") == "deployment_status_changed"
        and m.get("deployment_id") == did
        and m.get("status") == "running"
        for m in dashboard_ws.sent
    )
```

- [ ] **Step 2: Run, verify fail**

Run: `pytest tests/coordinator/test_websocket_handlers.py::test_instance_started_broadcasts_status_change -v`
Expected: FAIL.

- [ ] **Step 3: Modify `handle_worker_message` to broadcast on status changes**

In `coordinator/api/websocket.py`, for each of `instance_started`, `instance_stopped`, `instance_error`, after the DB commit:

```python
elif msg_type == "instance_started":
    instance_id = data.get("instance_id")
    if instance_id is not None:
        try:
            container = get_container()
            async with container.session_factory() as session:
                result = await session.execute(
                    select(AlgorithmInstance).where(AlgorithmInstance.id == instance_id)
                )
                instance = result.scalar_one_or_none()
                if instance:
                    instance.status = "running"
                    await session.commit()
                    await manager.broadcast_to_dashboards({
                        "type": "deployment_status_changed",
                        "deployment_id": instance_id,
                        "status": "running",
                        "active_run_id": instance.active_run_id,
                    })
        except Exception:
            logger.exception("Failed to update instance_started for instance %s", instance_id)
```

Mirror the broadcast call for `instance_stopped` (`status = "stopped"`, after also marking the active run as `stopped` with `stopped_at = now`) and `instance_error` (`status = "error"`, active run as `error`).

- [ ] **Step 4: Run all websocket tests**

Run: `pytest tests/coordinator/test_websocket_handlers.py -v`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add coordinator/api/websocket.py tests/coordinator/test_websocket_handlers.py
git commit -m "feat(ws): broadcast deployment_status_changed on worker-reported state transitions"
```

### Task 3.3: Frontend ws handler + optimistic mutation hooks

**Files:**
- Modify: `dashboard/src/api/websocket.ts` (add `deployment_status_changed` handler invalidating caches)
- Modify: `dashboard/src/api/hooks.ts` (add `useStartDeployment`, `useStopDeployment`)
- Modify: `dashboard/src/pages/InstanceDetail.tsx` (use new hooks)
- Test: `dashboard/src/api/websocket.test.ts` (new)

- [ ] **Step 1: Add `useStartDeployment` and `useStopDeployment` with optimistic updates**

In `dashboard/src/api/hooks.ts`:
```typescript
export const useStartDeployment = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await client.post(`/api/deployments/${id}/start`)).data,
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: ["deployment", id] });
      const prev = qc.getQueryData<Deployment>(["deployment", id]);
      if (prev) qc.setQueryData(["deployment", id], { ...prev, status: "starting" });
      return { prev };
    },
    onError: (_e, id, ctx) => {
      if (ctx?.prev) qc.setQueryData(["deployment", id], ctx.prev);
    },
    onSettled: (_d, _e, id) => {
      qc.invalidateQueries({ queryKey: ["deployment", id] });
      qc.invalidateQueries({ queryKey: ["deployments"] });
    },
  });
};

export const useStopDeployment = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await client.post(`/api/deployments/${id}/stop`)).data,
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: ["deployment", id] });
      const prev = qc.getQueryData<Deployment>(["deployment", id]);
      if (prev) qc.setQueryData(["deployment", id], { ...prev, status: "stopping" });
      return { prev };
    },
    onError: (_e, id, ctx) => {
      if (ctx?.prev) qc.setQueryData(["deployment", id], ctx.prev);
    },
    onSettled: (_d, _e, id) => {
      qc.invalidateQueries({ queryKey: ["deployment", id] });
      qc.invalidateQueries({ queryKey: ["deployments"] });
    },
  });
};
```

- [ ] **Step 2: Add the ws handler**

Read `dashboard/src/api/websocket.ts` first to learn its current structure, then add a handler for `deployment_status_changed`:
```typescript
case "deployment_status_changed":
  queryClient.invalidateQueries({ queryKey: ["deployment", msg.deployment_id] });
  queryClient.invalidateQueries({ queryKey: ["deployments"] });
  queryClient.invalidateQueries({ queryKey: ["deployment-runs", msg.deployment_id] });
  break;
```

- [ ] **Step 3: Test the handler**

```typescript
// dashboard/src/api/websocket.test.ts
import { describe, it, expect, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { dispatchDashboardMessage } from "./websocket";

describe("dashboard ws dispatch", () => {
  it("invalidates deployment queries on deployment_status_changed", () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    dispatchDashboardMessage({
      type: "deployment_status_changed",
      deployment_id: "abc",
      status: "running",
      active_run_id: null,
    }, qc);
    expect(spy).toHaveBeenCalledWith({ queryKey: ["deployment", "abc"] });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["deployments"] });
  });
});
```

(If `dispatchDashboardMessage` doesn't exist as a separate function, extract it from `websocket.ts` so it's testable, then re-export.)

- [ ] **Step 4: Run frontend tests + typecheck**

Run: `cd dashboard && npm run typecheck && npm test`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/api dashboard/src/pages/InstanceDetail.tsx
git commit -m "feat(dashboard): optimistic start/stop + ws-driven cache invalidation"
```

---

## Milestone 4 — Worker Activity Stream

End-to-end: new table → worker emit → coordinator persist + broadcast → REST + WS APIs → ActivityPanel component → worker page integration.

### Task 4.1: `WorkerActivity` model + Alembic migration

**Files:**
- Modify: `coordinator/database/models.py` (add `WorkerActivity`)
- Create: `coordinator/database/migrations/versions/<timestamp>_worker_activity.py`
- Test: `tests/coordinator/test_worker_activity_model.py`

- [ ] **Step 1: Add the model**

Append to `coordinator/database/models.py`:
```python
class WorkerActivity(Base):
    __tablename__ = "worker_activity"
    __table_args__ = (
        Index("ix_worker_activity_worker_ts", "worker_id", "timestamp"),
        Index("ix_worker_activity_instance_ts", "instance_id", "timestamp"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    worker_id: Mapped[str] = mapped_column(String, ForeignKey("workers.id"), nullable=False)
    instance_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("algorithm_instances.id"), nullable=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # "event" | "log"
    event_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    severity: Mapped[str] = mapped_column(String, nullable=False, default="info")
    logger_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
```

- [ ] **Step 2: Generate the migration**

Run: `alembic -c alembic.ini revision --autogenerate -m "add worker_activity"`
Inspect the generated file in `coordinator/database/migrations/versions/`. Verify it includes `op.create_table("worker_activity", ...)` and the two indexes.

- [ ] **Step 3: Run the migration**

Run: `alembic -c alembic.ini upgrade head`
Expected: succeeds.

- [ ] **Step 4: Write a smoke test**

```python
# tests/coordinator/test_worker_activity_model.py
import pytest
from datetime import datetime, timezone
from sqlalchemy import select
from coordinator.database.models import WorkerActivity, Worker
from coordinator.api.dependencies import get_container
from coordinator.main import create_app


@pytest.mark.asyncio
async def test_worker_activity_round_trip():
    app = create_app()
    async with app.router.lifespan_context(app):
        container = get_container()
        async with container.session_factory() as session:
            w = Worker(name="w", status="online")
            session.add(w); await session.flush()
            row = WorkerActivity(
                worker_id=w.id, kind="event", event_type="trade_executed",
                severity="info", message="BUY 10 AAPL", payload={"symbol": "AAPL"},
            )
            session.add(row); await session.commit()
            fetched = (await session.execute(
                select(WorkerActivity).where(WorkerActivity.worker_id == w.id)
            )).scalar_one()
            assert fetched.payload == {"symbol": "AAPL"}
            assert fetched.kind == "event"
```

- [ ] **Step 5: Run the test**

Run: `pytest tests/coordinator/test_worker_activity_model.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add coordinator/database/models.py coordinator/database/migrations/versions tests/coordinator/test_worker_activity_model.py
git commit -m "feat(db): WorkerActivity table for the worker activity stream"
```

### Task 4.2: Coordinator handlers for `activity_event` and `algo_log`

**Files:**
- Modify: `coordinator/api/websocket.py` (add handlers + subscription model)
- Test: `tests/coordinator/test_websocket_activity.py`

- [ ] **Step 1: Failing test**

```python
# tests/coordinator/test_websocket_activity.py
import pytest
from sqlalchemy import select
from coordinator.database.models import Worker, WorkerActivity


class FakeWebSocket:
    def __init__(self):
        self.sent = []
    async def send_json(self, data):
        self.sent.append(data)


@pytest.mark.asyncio
async def test_activity_event_is_persisted_and_broadcast(app_with_db):
    app, container = app_with_db
    async with container.session_factory() as session:
        w = Worker(name="w", status="online")
        session.add(w); await session.commit()
        wid = w.id
    from coordinator.api.websocket import handle_worker_message, manager
    dashboard_ws = FakeWebSocket()
    manager.subscribe(dashboard_ws, f"worker:{wid}")

    await handle_worker_message(FakeWebSocket(), {
        "type": "activity_event", "worker_id": wid, "instance_id": None,
        "timestamp": "2026-05-16T12:00:00Z",
        "event_type": "instance_started", "severity": "info",
        "payload": {"foo": "bar"},
    })

    async with container.session_factory() as session:
        rows = (await session.execute(
            select(WorkerActivity).where(WorkerActivity.worker_id == wid)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].event_type == "instance_started"
    assert any(m.get("type") == "activity_event" and m.get("event_type") == "instance_started"
               for m in dashboard_ws.sent)
```

- [ ] **Step 2: Run, fail**

Run: `pytest tests/coordinator/test_websocket_activity.py -v`
Expected: FAIL.

- [ ] **Step 3: Extend `ConnectionManager` with target-based subscriptions**

In `coordinator/api/websocket.py`:
```python
class ConnectionManager:
    def __init__(self) -> None:
        self.dashboard_connections: list[WebSocket] = []
        self.worker_connections: dict[str, WebSocket] = {}
        self.subscriptions: dict[str, set[WebSocket]] = {}

    def subscribe(self, ws: WebSocket, target: str) -> None:
        self.subscriptions.setdefault(target, set()).add(ws)

    def unsubscribe(self, ws: WebSocket, target: str) -> None:
        if target in self.subscriptions:
            self.subscriptions[target].discard(ws)
            if not self.subscriptions[target]:
                self.subscriptions.pop(target, None)

    def unsubscribe_all(self, ws: WebSocket) -> None:
        for target in list(self.subscriptions.keys()):
            self.unsubscribe(ws, target)

    async def broadcast_to_target(self, target: str, message: dict) -> None:
        for ws in list(self.subscriptions.get(target, ())):
            try:
                await ws.send_json(message)
            except Exception:
                self.unsubscribe(ws, target)
```

In `disconnect_dashboard`, also call `manager.unsubscribe_all(websocket)`.

- [ ] **Step 4: Handle `subscribe` / `unsubscribe` dashboard messages**

In `handle_dashboard_message`:
```python
if msg_type == "subscribe":
    target = data.get("target")
    if target:
        manager.subscribe(websocket, target)
        await websocket.send_json({"type": "subscribed", "target": target})
    return

if msg_type == "unsubscribe":
    target = data.get("target")
    if target:
        manager.unsubscribe(websocket, target)
    return
```

- [ ] **Step 5: Handle `activity_event` and `algo_log` worker messages**

In `handle_worker_message`, add:
```python
elif msg_type in ("activity_event", "algo_log"):
    from coordinator.database.models import WorkerActivity
    worker_id = data.get("worker_id")
    instance_id = data.get("instance_id")
    kind = "event" if msg_type == "activity_event" else "log"
    severity = data.get("severity", "info").lower()
    if kind == "log":
        # Map Python logging levels to our severity vocabulary.
        level = (data.get("level") or "INFO").upper()
        severity = {"DEBUG": "debug", "INFO": "info", "WARNING": "warn",
                     "ERROR": "error", "CRITICAL": "error"}.get(level, "info")
    raw_ts = data.get("timestamp")
    try:
        ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")) if isinstance(raw_ts, str) else datetime.now(timezone.utc)
    except Exception:
        ts = datetime.now(timezone.utc)

    container = get_container()
    async with container.session_factory() as session:
        row = WorkerActivity(
            worker_id=worker_id, instance_id=instance_id, timestamp=ts,
            kind=kind, severity=severity,
            event_type=data.get("event_type") if kind == "event" else None,
            logger_name=data.get("logger_name") if kind == "log" else None,
            message=data.get("message"),
            payload=data.get("payload") if kind == "event" else None,
        )
        session.add(row)
        await session.commit()

    broadcast_msg = {
        "type": msg_type, "worker_id": worker_id, "instance_id": instance_id,
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "severity": severity,
    }
    if kind == "event":
        broadcast_msg["event_type"] = data.get("event_type")
        broadcast_msg["payload"] = data.get("payload")
    else:
        broadcast_msg["logger_name"] = data.get("logger_name")
        broadcast_msg["level"] = data.get("level")
        broadcast_msg["message"] = data.get("message")

    if worker_id:
        await manager.broadcast_to_target(f"worker:{worker_id}", broadcast_msg)
    if instance_id:
        await manager.broadcast_to_target(f"deployment:{instance_id}", broadcast_msg)
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/coordinator/test_websocket_activity.py -v`
Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add coordinator/api/websocket.py tests/coordinator/test_websocket_activity.py
git commit -m "feat(ws): persist + broadcast activity_event and algo_log messages"
```

### Task 4.3: REST endpoints for activity

**Files:**
- Modify: `coordinator/api/routes/workers.py` (add `/activity`)
- Modify: `coordinator/api/routes/deployments.py` (add `/activity`)
- Test: `tests/coordinator/test_activity_api.py`

- [ ] **Step 1: Failing test**

```python
# tests/coordinator/test_activity_api.py
import pytest
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone, timedelta

from coordinator.main import create_app
from coordinator.api.dependencies import get_container
from coordinator.database.models import Worker, WorkerActivity


@pytest.mark.asyncio
async def test_list_worker_activity_newest_first_with_severity_filter():
    app = create_app()
    async with app.router.lifespan_context(app):
        container = get_container()
        async with container.session_factory() as session:
            w = Worker(name="w", status="online")
            session.add(w); await session.flush()
            t0 = datetime.now(timezone.utc)
            session.add_all([
                WorkerActivity(worker_id=w.id, kind="event", event_type="x", severity="debug", timestamp=t0 - timedelta(seconds=30)),
                WorkerActivity(worker_id=w.id, kind="event", event_type="y", severity="info",  timestamp=t0 - timedelta(seconds=20)),
                WorkerActivity(worker_id=w.id, kind="event", event_type="z", severity="error", timestamp=t0 - timedelta(seconds=10)),
            ])
            await session.commit()
            wid = w.id
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get(f"/api/workers/{wid}/activity?severity=info")
        rows = r.json()["items"]
        assert [r_["event_type"] for r_ in rows] == ["z", "y"]  # newest-first, debug filtered out
```

- [ ] **Step 2: Run, fail**

Run: `pytest tests/coordinator/test_activity_api.py -v`
Expected: 404.

- [ ] **Step 3: Add the endpoint to `workers.py`**

```python
SEVERITY_ORDER = {"debug": 0, "info": 1, "warn": 2, "error": 3}


@router.get("/{worker_id}/activity")
async def list_worker_activity(
    worker_id: str,
    limit: int = 100,
    before: Optional[str] = None,
    severity: str = "info",
    event_types: Optional[str] = None,
    kind: str = "all",
    db: AsyncSession = Depends(get_db),
):
    from coordinator.database.models import WorkerActivity
    from datetime import datetime, timezone

    limit = max(1, min(500, limit))
    min_sev = SEVERITY_ORDER.get(severity, 1)
    allowed_sev = [s for s, n in SEVERITY_ORDER.items() if n >= min_sev]

    stmt = (
        select(WorkerActivity)
        .where(WorkerActivity.worker_id == worker_id)
        .where(WorkerActivity.severity.in_(allowed_sev))
    )
    if kind in ("event", "log"):
        stmt = stmt.where(WorkerActivity.kind == kind)
    if event_types:
        stmt = stmt.where(WorkerActivity.event_type.in_(event_types.split(",")))
    if before:
        try:
            before_dt = datetime.fromisoformat(before.replace("Z", "+00:00"))
            stmt = stmt.where(WorkerActivity.timestamp < before_dt)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid `before`")
    stmt = stmt.order_by(WorkerActivity.timestamp.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "id": r.id,
                "worker_id": r.worker_id,
                "instance_id": r.instance_id,
                "timestamp": to_iso_utc(r.timestamp),
                "kind": r.kind,
                "event_type": r.event_type,
                "severity": r.severity,
                "logger_name": r.logger_name,
                "message": r.message,
                "payload": r.payload,
            }
            for r in rows
        ]
    }
```

Add `from coordinator.api.serialization import to_iso_utc` if not already imported.

- [ ] **Step 4: Add the equivalent endpoint to `deployments.py`** (filter by `instance_id`)

```python
@router.get("/{deployment_id}/activity")
async def list_deployment_activity(
    deployment_id: str,
    limit: int = 100,
    before: Optional[str] = None,
    severity: str = "info",
    event_types: Optional[str] = None,
    kind: str = "all",
    db: AsyncSession = Depends(get_db),
):
    # Same as list_worker_activity but filtered by instance_id.
    # ... (paste the identical implementation, replacing the worker_id filter
    # with `WorkerActivity.instance_id == deployment_id`)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/coordinator/test_activity_api.py -v`
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add coordinator/api/routes tests/coordinator/test_activity_api.py
git commit -m "feat(api): activity list endpoints for workers and deployments"
```

### Task 4.4: Worker emits activity events and captures algo logs

**Files:**
- Modify: `worker/agent.py` (new `send_activity_event` + idle-tick tracking)
- Modify: `worker/tick_loop.py` (emit per-tick events when non-silent)
- Modify: `worker/runner.py` (install a logging handler scoped to algo module)
- Test: `tests/worker/test_activity_emit.py`

- [ ] **Step 1: Failing test**

```python
# tests/worker/test_activity_emit.py
import pytest
import asyncio
from unittest.mock import AsyncMock
from worker.agent import WorkerAgent


@pytest.mark.asyncio
async def test_send_activity_event_emits_well_formed_message():
    ws = AsyncMock()
    ws.send = AsyncMock()
    agent = WorkerAgent(worker_id="w1", worker_name="W", websocket=ws)
    await agent.send_activity_event(
        instance_id="d1", event_type="trade_executed",
        severity="info", payload={"symbol": "AAPL"},
    )
    sent = ws.send.call_args.args[0]
    import json
    msg = json.loads(sent)
    assert msg["type"] == "activity_event"
    assert msg["worker_id"] == "w1"
    assert msg["instance_id"] == "d1"
    assert msg["event_type"] == "trade_executed"
    assert msg["severity"] == "info"
    assert msg["payload"] == {"symbol": "AAPL"}
    assert "timestamp" in msg
```

- [ ] **Step 2: Run, fail**

Run: `pytest tests/worker/test_activity_emit.py -v`
Expected: AttributeError.

- [ ] **Step 3: Add `send_activity_event` and `send_algo_log` to `WorkerAgent`**

```python
async def send_activity_event(
    self, instance_id: Optional[str], event_type: str,
    severity: str = "info", payload: Optional[dict] = None,
) -> None:
    await self._send({
        "type": "activity_event",
        "worker_id": self.worker_id,
        "instance_id": instance_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "severity": severity,
        "payload": payload or {},
    })

async def send_algo_log(
    self, instance_id: str, logger_name: str, level: str, message: str,
) -> None:
    await self._send({
        "type": "algo_log",
        "worker_id": self.worker_id,
        "instance_id": instance_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "logger_name": logger_name,
        "level": level,
        "message": message,
    })
```

- [ ] **Step 4: Emit lifecycle events in existing handlers**

In `WorkerAgent._handle_start_instance`, after `self.send_event("instance_started", ...)`, also call:
```python
await self.send_activity_event(instance_id, "instance_started", severity="info")
```

Same pattern for `_handle_stop_instance` → `instance_stopped`.

- [ ] **Step 5: Per-tick event emission in `tick_loop.py`**

After `process_tick` builds `TickResult`, emit:
- A `tick_processed` event with `{signals_produced, trades_executed, trades_rejected}` only when *any* of those is nonzero.
- For each `signal in signals`: a `signal_produced` event.
- For each executed trade: a `trade_executed` event with symbol/side/quantity.
- Track an idle counter — after 60s of silent ticks emit one `idle_tick` event, then reset.

Add a helper in `tick_loop.py` for the idle bookkeeping; idle threshold is configurable via constructor parameter (default 60 seconds).

- [ ] **Step 6: Algorithm logging handler in `runner.py`**

When `AlgorithmRunner` is constructed for an instance:
```python
class _AlgoLogShipper(logging.Handler):
    def __init__(self, agent, instance_id, loop):
        super().__init__()
        self._agent = agent
        self._instance_id = instance_id
        self._loop = loop
    def emit(self, record: logging.LogRecord) -> None:
        try:
            asyncio.run_coroutine_threadsafe(
                self._agent.send_algo_log(
                    instance_id=self._instance_id,
                    logger_name=record.name,
                    level=record.levelname,
                    message=record.getMessage(),
                ),
                self._loop,
            )
        except Exception:
            pass

# Attach the handler to the algorithm's top-level package logger.
# The shipper is stored on the runner so it can be detached on stop.
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/worker -v`
Expected: pass.

- [ ] **Step 8: Commit**

```bash
git add worker tests/worker/test_activity_emit.py
git commit -m "feat(worker): emit activity_event + ship algorithm log records to coordinator"
```

### Task 4.5: `ActivityPanel` component + worker page integration

**Files:**
- Create: `dashboard/src/components/ActivityPanel.tsx`
- Create: `dashboard/src/components/ActivityPanel.test.tsx`
- Modify: `dashboard/src/pages/WorkerDetail.tsx` (include the panel)

- [ ] **Step 1: Write the failing test**

```tsx
// dashboard/src/components/ActivityPanel.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ActivityPanel } from "./ActivityPanel";

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient();
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

describe("ActivityPanel", () => {
  it("renders an empty state when there are no rows", () => {
    render(wrap(<ActivityPanel target="worker:fake" initialRows={[]} />));
    expect(screen.getByText(/no activity/i)).toBeInTheDocument();
  });

  it("renders an activity row", () => {
    render(wrap(<ActivityPanel target="worker:fake" initialRows={[{
      id: "1", worker_id: "w", instance_id: null,
      timestamp: "2026-05-16T12:00:00Z", kind: "event",
      event_type: "trade_executed", severity: "info",
      logger_name: null, message: "BUY 10 AAPL", payload: {},
    }]} />));
    expect(screen.getByText(/trade_executed/i)).toBeInTheDocument();
    expect(screen.getByText(/BUY 10 AAPL/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run, fail**

Run: `cd dashboard && npm test -- ActivityPanel`
Expected: missing module.

- [ ] **Step 3: Implement `ActivityPanel`**

```tsx
// dashboard/src/components/ActivityPanel.tsx
import { useEffect, useState } from "react";
import { client } from "../api/client";
import { wsManager } from "../api/websocket";

export type ActivityRow = {
  id: string;
  worker_id: string;
  instance_id: string | null;
  timestamp: string;
  kind: "event" | "log";
  event_type: string | null;
  severity: "debug" | "info" | "warn" | "error";
  logger_name: string | null;
  message: string | null;
  payload: Record<string, unknown> | null;
};

const SEVERITY_DOT: Record<string, string> = {
  debug: "bg-gray-500",
  info: "bg-blue-400",
  warn: "bg-yellow-400",
  error: "bg-red-500",
};

export function ActivityPanel({
  target,
  initialRows,
}: {
  target: `worker:${string}` | `deployment:${string}`;
  initialRows?: ActivityRow[];
}) {
  const [rows, setRows] = useState<ActivityRow[]>(initialRows ?? []);
  const [severity, setSeverity] = useState<"debug" | "info" | "warn" | "error">("info");
  const [kind, setKind] = useState<"all" | "event" | "log">("all");

  useEffect(() => {
    if (initialRows) return; // test path
    const url = target.startsWith("worker:")
      ? `/api/workers/${target.slice(7)}/activity`
      : `/api/deployments/${target.slice(11)}/activity`;
    client
      .get<{ items: ActivityRow[] }>(`${url}?severity=${severity}&kind=${kind}&limit=100`)
      .then((r) => setRows(r.data.items));
  }, [target, severity, kind, initialRows]);

  useEffect(() => {
    wsManager.send({ type: "subscribe", target });
    const off = wsManager.on(
      ["activity_event", "algo_log"],
      (msg: Record<string, unknown>) => {
        const row: ActivityRow = {
          id: String(msg.id ?? crypto.randomUUID()),
          worker_id: String(msg.worker_id),
          instance_id: (msg.instance_id ?? null) as string | null,
          timestamp: String(msg.timestamp),
          kind: msg.type === "activity_event" ? "event" : "log",
          event_type: (msg.event_type ?? null) as string | null,
          severity: (msg.severity ?? "info") as ActivityRow["severity"],
          logger_name: (msg.logger_name ?? null) as string | null,
          message: (msg.message ?? null) as string | null,
          payload: (msg.payload ?? null) as Record<string, unknown> | null,
        };
        setRows((prev) => [row, ...prev].slice(0, 500));
      },
    );
    return () => {
      wsManager.send({ type: "unsubscribe", target });
      off();
    };
  }, [target]);

  if (rows.length === 0) {
    return (
      <div className="text-gray-500 text-sm py-4">No activity in the last hour.</div>
    );
  }
  return (
    <div className="bg-gray-900 border border-gray-800 rounded">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-800 text-xs">
        <label className="text-gray-400">Severity</label>
        <select
          value={severity}
          onChange={(e) => setSeverity(e.target.value as ActivityRow["severity"])}
          className="bg-gray-800 border border-gray-700 rounded px-2 py-1"
        >
          <option value="debug">debug+</option>
          <option value="info">info+</option>
          <option value="warn">warn+</option>
          <option value="error">error only</option>
        </select>
        <label className="text-gray-400 ml-3">Kind</label>
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value as typeof kind)}
          className="bg-gray-800 border border-gray-700 rounded px-2 py-1"
        >
          <option value="all">all</option>
          <option value="event">events</option>
          <option value="log">logs</option>
        </select>
      </div>
      <ul className="max-h-96 overflow-y-auto font-mono text-xs">
        {rows.map((r) => (
          <li key={r.id} className="px-3 py-1 border-b border-gray-800 last:border-b-0">
            <span className="text-gray-500">{new Date(r.timestamp).toLocaleTimeString()}</span>
            <span className={`inline-block w-2 h-2 rounded-full mx-2 ${SEVERITY_DOT[r.severity]}`} />
            <span className="text-gray-300">{r.event_type ?? r.logger_name}</span>
            {r.message && <span className="text-gray-200 ml-2">{r.message}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
```

The `wsManager.on(types, handler)` helper may not exist; read `dashboard/src/api/websocket.ts`. If it doesn't, add it as a simple subscribe-with-cleanup utility before using it.

- [ ] **Step 4: Add the panel to `WorkerDetail.tsx`**

Below the "Assigned Instances" section in `dashboard/src/pages/WorkerDetail.tsx`:
```tsx
<section>
  <h2 className="text-sm font-semibold text-gray-400 uppercase mb-3">Activity</h2>
  <ActivityPanel target={`worker:${id ?? ""}` as const} />
</section>
```

- [ ] **Step 5: Run tests + typecheck**

Run: `cd dashboard && npm run typecheck && npm test -- ActivityPanel`
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/components/ActivityPanel.tsx dashboard/src/components/ActivityPanel.test.tsx dashboard/src/pages/WorkerDetail.tsx
git commit -m "feat(dashboard): ActivityPanel component on WorkerDetail page"
```

### Task 4.6: Retention sweep for `WorkerActivity`

**Files:**
- Modify: `coordinator/services/archival.py` (add `prune_worker_activity`)
- Modify: `coordinator/main.py` (run on the existing archival schedule)
- Test: `tests/coordinator/test_archival.py` (extend)

- [ ] **Step 1: Failing test**

```python
@pytest.mark.asyncio
async def test_prune_worker_activity_deletes_old_rows(app_with_db):
    app, container = app_with_db
    from datetime import datetime, timezone, timedelta
    async with container.session_factory() as session:
        w = Worker(name="w", status="online"); session.add(w); await session.flush()
        old = WorkerActivity(worker_id=w.id, kind="event", event_type="x",
                             severity="info", timestamp=datetime.now(timezone.utc) - timedelta(days=8))
        new = WorkerActivity(worker_id=w.id, kind="event", event_type="y",
                             severity="info", timestamp=datetime.now(timezone.utc) - timedelta(days=1))
        session.add_all([old, new]); await session.commit()
        wid = w.id

    from coordinator.services.archival import prune_worker_activity
    deleted = await prune_worker_activity(container.session_factory, retention_days=7)
    assert deleted == 1
    async with container.session_factory() as session:
        rows = (await session.execute(select(WorkerActivity).where(WorkerActivity.worker_id == wid))).scalars().all()
        assert [r.event_type for r in rows] == ["y"]
```

- [ ] **Step 2: Fail**

Run the test, expect ImportError.

- [ ] **Step 3: Implement**

```python
# coordinator/services/archival.py
async def prune_worker_activity(session_factory, retention_days: int) -> int:
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import delete
    from coordinator.database.models import WorkerActivity
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    async with session_factory() as session:
        result = await session.execute(
            delete(WorkerActivity).where(WorkerActivity.timestamp < cutoff)
        )
        await session.commit()
        return result.rowcount or 0
```

Schedule it from the existing archival loop in `main.py` (or wherever archival is already kicked off) with default `retention_days=int(os.environ.get("QT_WORKER_ACTIVITY_RETENTION_DAYS", "7"))`.

- [ ] **Step 4: Pass + commit**

```bash
git add coordinator/services/archival.py tests/coordinator/test_archival.py coordinator/main.py
git commit -m "feat(coordinator): retention sweep for worker_activity"
```

---

## Milestone 5 — Live Data Pipeline

### Task 5.1: Shared parquet schemas

**Files:**
- Create: `coordinator/services/streaming_schemas.py`
- Modify: `coordinator/services/backtest_writer.py` (import from the new module instead of defining inline)
- Test: existing backtest tests must still pass

- [ ] **Step 1: Move `_EQUITY_SCHEMA` and `_TRADE_SCHEMA` into the new module**

```python
# coordinator/services/streaming_schemas.py
"""Shared parquet schemas for backtest and live streaming pipelines."""
import pyarrow as pa

EQUITY_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("ns")),
    ("portfolio_value", pa.float64()),
    ("cash", pa.float64()),
])

TRADE_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("ns")),
    ("symbol", pa.string()),
    ("asset_type", pa.string()),
    ("side", pa.string()),
    ("quantity", pa.float64()),
    ("requested_price", pa.float64()),
    ("fill_price", pa.float64()),
    ("slippage_dollars", pa.float64()),
    ("slippage_bps_applied", pa.float64()),
    ("fees", pa.float64()),
    ("fee_breakdown", pa.string()),
    ("signal_id", pa.string()),
    ("realized_pnl", pa.float64()),
])
```

- [ ] **Step 2: Update `backtest_writer.py`**

Replace the inline schema constants with:
```python
from coordinator.services.streaming_schemas import EQUITY_SCHEMA as _EQUITY_SCHEMA, TRADE_SCHEMA as _TRADE_SCHEMA
```

- [ ] **Step 3: Run backtest tests**

Run: `pytest tests/coordinator/services -v -k "backtest"`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add coordinator/services/streaming_schemas.py coordinator/services/backtest_writer.py
git commit -m "refactor(services): extract parquet schemas to streaming_schemas"
```

### Task 5.2: `AlgorithmDeploymentReport` model + migration

**Files:**
- Modify: `coordinator/database/models.py`
- Create: alembic migration
- Test: `tests/coordinator/test_deployment_report_model.py`

- [ ] **Step 1: Add the model**

```python
class AlgorithmDeploymentReport(Base):
    __tablename__ = "algorithm_deployment_reports"
    deployment_id: Mapped[str] = mapped_column(
        String, ForeignKey("algorithm_instances.id"), primary_key=True
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    # Scalar metrics — mirror BacktestRun
    total_return: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cagr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volatility: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sharpe_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sortino_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    calmar_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_drawdown_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    romad: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_fees_paid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_slippage_dollars: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trade_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    win_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    profit_factor: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_win: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    expectancy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longest_drawdown_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    longest_winning_streak: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    longest_losing_streak: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Blob columns
    equity_curve: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    drawdown_curve: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    drawdown_periods: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    key_metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    rolling_metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    monthly_returns_matrix: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    eoy_returns: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    runs_index: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
```

- [ ] **Step 2: Migration**

Run: `alembic -c alembic.ini revision --autogenerate -m "add algorithm_deployment_reports"` and inspect; then `alembic -c alembic.ini upgrade head`.

- [ ] **Step 3: Smoke test**

```python
# tests/coordinator/test_deployment_report_model.py
import pytest
from sqlalchemy import select
from coordinator.api.dependencies import get_container
from coordinator.main import create_app
from coordinator.database.models import AlgorithmDeploymentReport, AlgorithmInstance, Algorithm, Account, Worker


@pytest.mark.asyncio
async def test_deployment_report_round_trip():
    app = create_app()
    async with app.router.lifespan_context(app):
        container = get_container()
        async with container.session_factory() as session:
            algo = Algorithm(repo_url="x", name="A")
            acct = Account(name="A", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
            w = Worker(name="W", status="online")
            session.add_all([algo, acct, w]); await session.flush()
            inst = AlgorithmInstance(algorithm_id=algo.id, account_id=acct.id, worker_id=w.id, status="stopped")
            session.add(inst); await session.commit()
            session.add(AlgorithmDeploymentReport(
                deployment_id=inst.id, total_return=0.05, sharpe_ratio=1.2,
                equity_curve=[{"timestamp": "...", "portfolio_value": 100.0}],
            ))
            await session.commit()
            got = (await session.execute(
                select(AlgorithmDeploymentReport).where(AlgorithmDeploymentReport.deployment_id == inst.id)
            )).scalar_one()
            assert got.total_return == 0.05
```

- [ ] **Step 4: Pass + commit**

```bash
git add coordinator/database/models.py coordinator/database/migrations/versions tests/coordinator/test_deployment_report_model.py
git commit -m "feat(db): AlgorithmDeploymentReport table for live finalizer output"
```

### Task 5.3: `LiveSampleSink` — parquet writer for live samples

**Files:**
- Create: `coordinator/services/live_sample_sink.py`
- Test: `tests/coordinator/services/test_live_sample_sink.py`

- [ ] **Step 1: Failing test**

```python
# tests/coordinator/services/test_live_sample_sink.py
from pathlib import Path
import asyncio
import pytest
import pyarrow.parquet as pq


@pytest.mark.asyncio
async def test_sink_buffers_and_flushes_per_run_parquet(tmp_path: Path):
    from coordinator.services.live_sample_sink import LiveSampleSink
    sink = LiveSampleSink(base_dir=tmp_path, buffer_size=3, flush_interval_seconds=60)
    for i in range(3):
        await sink.add_equity_sample("d1", "r1", {
            "timestamp": f"2026-05-16T12:00:0{i}Z",
            "portfolio_value": 100.0 + i,
            "cash": 50.0 + i,
        })
    out = tmp_path / "d1" / "r1" / "equity.parquet"
    assert out.exists()
    df = pq.read_table(out).to_pandas()
    assert list(df["portfolio_value"]) == [100.0, 101.0, 102.0]


@pytest.mark.asyncio
async def test_force_flush_writes_pending_rows(tmp_path: Path):
    from coordinator.services.live_sample_sink import LiveSampleSink
    sink = LiveSampleSink(base_dir=tmp_path, buffer_size=100, flush_interval_seconds=60)
    await sink.add_trade_sample("d1", "r1", {
        "timestamp": "2026-05-16T12:00:00Z", "symbol": "AAPL",
        "asset_type": "equities", "side": "buy", "quantity": 10.0,
        "requested_price": 100.0, "fill_price": 100.5,
        "slippage_dollars": 5.0, "slippage_bps_applied": 0.5,
        "fees": 1.0, "fee_breakdown": "{}", "signal_id": "s1",
        "realized_pnl": None,
    })
    await sink.flush()
    out = tmp_path / "d1" / "r1" / "trades.parquet"
    assert out.exists()
    df = pq.read_table(out).to_pandas()
    assert df.iloc[0]["symbol"] == "AAPL"
```

- [ ] **Step 2: Fail**

Run: `pytest tests/coordinator/services/test_live_sample_sink.py -v`

- [ ] **Step 3: Implement**

```python
# coordinator/services/live_sample_sink.py
"""Append-only parquet writer for live algorithm samples.

One pair of parquet files per (deployment, run): equity.parquet and
trades.parquet. Buffers in-memory until `buffer_size` rows or
`flush_interval_seconds` elapses, whichever comes first. Force-flush on stop.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from coordinator.services.streaming_schemas import EQUITY_SCHEMA, TRADE_SCHEMA

logger = logging.getLogger(__name__)


def _coerce_ts(s: str | None) -> pd.Timestamp:
    if not s:
        return pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
    p = pd.Timestamp(s)
    if p.tz is not None:
        p = p.tz_convert("UTC").tz_localize(None)
    return p


class LiveSampleSink:
    def __init__(
        self, base_dir: Path,
        buffer_size: int = 200, flush_interval_seconds: int = 10,
    ) -> None:
        self._base = Path(base_dir)
        self._buf_size = buffer_size
        self._interval = flush_interval_seconds
        self._equity_buf: dict[tuple[str, str], list[dict]] = {}
        self._trade_buf: dict[tuple[str, str], list[dict]] = {}
        self._lock = asyncio.Lock()
        self._last_flush = datetime.now(timezone.utc)

    def _equity_path(self, dep_id: str, run_id: str) -> Path:
        return self._base / dep_id / run_id / "equity.parquet"

    def _trades_path(self, dep_id: str, run_id: str) -> Path:
        return self._base / dep_id / run_id / "trades.parquet"

    async def add_equity_sample(self, dep_id: str, run_id: str, sample: dict) -> None:
        async with self._lock:
            self._equity_buf.setdefault((dep_id, run_id), []).append(sample)
            if len(self._equity_buf[(dep_id, run_id)]) >= self._buf_size:
                await self._flush_equity(dep_id, run_id)

    async def add_trade_sample(self, dep_id: str, run_id: str, sample: dict) -> None:
        async with self._lock:
            self._trade_buf.setdefault((dep_id, run_id), []).append(sample)
            if len(self._trade_buf[(dep_id, run_id)]) >= self._buf_size:
                await self._flush_trades(dep_id, run_id)

    async def flush(self) -> None:
        async with self._lock:
            for key in list(self._equity_buf.keys()):
                await self._flush_equity(*key)
            for key in list(self._trade_buf.keys()):
                await self._flush_trades(*key)

    async def _flush_equity(self, dep_id: str, run_id: str) -> None:
        rows = self._equity_buf.pop((dep_id, run_id), [])
        if not rows:
            return
        df = pd.DataFrame([{
            "timestamp": _coerce_ts(r.get("timestamp")),
            "portfolio_value": float(r["portfolio_value"]),
            "cash": float(r.get("cash") or 0.0),
        } for r in rows])
        await asyncio.to_thread(self._append_parquet,
                                self._equity_path(dep_id, run_id),
                                df, EQUITY_SCHEMA)

    async def _flush_trades(self, dep_id: str, run_id: str) -> None:
        rows = self._trade_buf.pop((dep_id, run_id), [])
        if not rows:
            return
        df = pd.DataFrame([{
            "timestamp": _coerce_ts(r.get("timestamp")),
            "symbol": r["symbol"], "asset_type": r.get("asset_type", "equities"),
            "side": r["side"], "quantity": float(r["quantity"]),
            "requested_price": float(r.get("requested_price") or 0.0),
            "fill_price": float(r.get("fill_price") or 0.0),
            "slippage_dollars": float(r.get("slippage_dollars") or 0.0),
            "slippage_bps_applied": float(r.get("slippage_bps_applied") or 0.0),
            "fees": float(r.get("fees") or 0.0),
            "fee_breakdown": r.get("fee_breakdown") or "{}",
            "signal_id": r.get("signal_id") or "",
            "realized_pnl": float(r["realized_pnl"]) if r.get("realized_pnl") is not None else None,
        } for r in rows])
        await asyncio.to_thread(self._append_parquet,
                                self._trades_path(dep_id, run_id),
                                df, TRADE_SCHEMA)

    @staticmethod
    def _append_parquet(path: Path, df: pd.DataFrame, schema: pa.Schema) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
        if path.exists():
            existing = pq.read_table(path, schema=schema)
            table = pa.concat_tables([existing, table])
        pq.write_table(table, path, compression="snappy")
```

- [ ] **Step 4: Pass + commit**

Run: `pytest tests/coordinator/services/test_live_sample_sink.py -v`
Expected: pass.

```bash
git add coordinator/services/live_sample_sink.py tests/coordinator/services/test_live_sample_sink.py
git commit -m "feat(coordinator): LiveSampleSink — parquet writer for live equity/trade samples"
```

### Task 5.4: Coordinator wires `equity_sample` and `trade_sample` to the sink

**Files:**
- Modify: `coordinator/api/websocket.py` (new handlers)
- Modify: `coordinator/main.py` (instantiate sink + add to container)
- Test: `tests/coordinator/test_websocket_live_samples.py`

- [ ] **Step 1: Failing test**

```python
# tests/coordinator/test_websocket_live_samples.py
import pytest
from pathlib import Path


@pytest.mark.asyncio
async def test_equity_sample_routed_to_sink(app_with_db, monkeypatch, tmp_path):
    app, container = app_with_db
    # Replace the container's live sample sink with one rooted in tmp_path.
    from coordinator.services.live_sample_sink import LiveSampleSink
    container.live_sample_sink = LiveSampleSink(base_dir=tmp_path, buffer_size=1, flush_interval_seconds=60)

    from coordinator.api.websocket import handle_worker_message
    await handle_worker_message(None, {
        "type": "equity_sample",
        "worker_id": "w1", "instance_id": "d1", "run_id": "r1",
        "timestamp": "2026-05-16T12:00:00Z",
        "portfolio_value": 100.0, "cash": 50.0,
    })
    assert (tmp_path / "d1" / "r1" / "equity.parquet").exists()
```

- [ ] **Step 2: Fail**

- [ ] **Step 3: Implement**

In `coordinator/main.py`'s lifespan, instantiate:
```python
from coordinator.services.live_sample_sink import LiveSampleSink
container.live_sample_sink = LiveSampleSink(
    base_dir=Path("data/live"),
    buffer_size=int(os.environ.get("QT_LIVE_SAMPLE_BUFFER_SIZE", "200")),
    flush_interval_seconds=int(os.environ.get("QT_LIVE_SAMPLE_FLUSH_INTERVAL_SECONDS", "10")),
)
```
Make sure the `Container` dataclass / object has a `live_sample_sink` attribute. Read `coordinator/api/dependencies.py` for the existing container shape and add it.

In `coordinator/api/websocket.py`, add handlers:
```python
elif msg_type in ("equity_sample", "trade_sample"):
    container = get_container()
    sink = getattr(container, "live_sample_sink", None)
    if sink is None:
        return
    dep_id = data.get("instance_id")
    run_id = data.get("run_id")
    if not dep_id or not run_id:
        return
    if msg_type == "equity_sample":
        await sink.add_equity_sample(dep_id, run_id, {
            "timestamp": data.get("timestamp"),
            "portfolio_value": data.get("portfolio_value"),
            "cash": data.get("cash", 0.0),
        })
    else:
        await sink.add_trade_sample(dep_id, run_id, data)
```

- [ ] **Step 4: Pass + commit**

```bash
git add coordinator/api/websocket.py coordinator/main.py coordinator/api/dependencies.py tests/coordinator/test_websocket_live_samples.py
git commit -m "feat(ws): route equity_sample/trade_sample messages to LiveSampleSink"
```

### Task 5.5: Worker `LiveObserver` emits per-tick samples

**Files:**
- Create: `worker/live_observer.py`
- Modify: `worker/tick_loop.py` (wire to the observer)
- Test: `tests/worker/test_live_observer.py`

- [ ] **Step 1: Failing test**

```python
# tests/worker/test_live_observer.py
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_observer_sends_equity_sample_per_tick():
    from worker.live_observer import LiveObserver
    agent = MagicMock()
    agent.worker_id = "w1"
    agent._send = AsyncMock()
    broker = MagicMock()
    broker.get_account_state = MagicMock(return_value={"cash": 100.0, "positions_value": 50.0})
    obs = LiveObserver(agent=agent, broker=broker, instance_id="d1", run_id="r1")

    await obs.on_tick(timestamp="2026-05-16T12:00:00Z")
    sent = agent._send.call_args.args[0]
    assert sent["type"] == "equity_sample"
    assert sent["portfolio_value"] == 150.0
    assert sent["cash"] == 100.0
    assert sent["run_id"] == "r1"
```

- [ ] **Step 2: Fail**

- [ ] **Step 3: Implement**

```python
# worker/live_observer.py
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class LiveObserver:
    """Per-tick live observer that emits equity and trade samples via the agent."""
    def __init__(self, *, agent, broker, instance_id: str, run_id: str) -> None:
        self._agent = agent
        self._broker = broker
        self._dep = instance_id
        self._run = run_id

    async def on_tick(self, *, timestamp: str | None = None) -> None:
        state = self._broker.get_account_state()
        cash = float(state.get("cash") or 0.0)
        positions_value = float(state.get("positions_value") or 0.0)
        await self._agent._send({
            "type": "equity_sample",
            "worker_id": self._agent.worker_id,
            "instance_id": self._dep,
            "run_id": self._run,
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
            "portfolio_value": cash + positions_value,
            "cash": cash,
        })

    async def on_trade(self, *, trade: dict) -> None:
        await self._agent._send({
            "type": "trade_sample",
            "worker_id": self._agent.worker_id,
            "instance_id": self._dep,
            "run_id": self._run,
            **trade,
        })
```

Wire it from `tick_loop.py`: when the runner starts a live instance, construct a `LiveObserver` and call `await obs.on_tick(...)` after each tick, `await obs.on_trade(...)` for each executed trade.

Add a `get_account_state()` method on `BrokerAdapter` if not already present (cash + positions_value); use existing broker methods.

- [ ] **Step 4: Pass + commit**

```bash
git add worker/live_observer.py worker/tick_loop.py worker/broker_adapter.py tests/worker/test_live_observer.py
git commit -m "feat(worker): LiveObserver streams per-tick equity samples to coordinator"
```

### Task 5.6: `LiveFinalizer` — periodic report builder

**Files:**
- Create: `coordinator/services/live_finalizer.py`
- Modify: `coordinator/main.py` (start the loop in lifespan)
- Test: `tests/coordinator/services/test_live_finalizer.py`

- [ ] **Step 1: Failing test**

```python
# tests/coordinator/services/test_live_finalizer.py
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from sqlalchemy import select


@pytest.mark.asyncio
async def test_finalizer_writes_report_for_running_deployment(app_with_db, tmp_path):
    app, container = app_with_db
    from coordinator.services.live_sample_sink import LiveSampleSink
    container.live_sample_sink = LiveSampleSink(base_dir=tmp_path, buffer_size=1, flush_interval_seconds=60)
    # Seed: a running deployment with one run and a handful of equity samples
    from coordinator.database.models import Algorithm, Account, Worker, AlgorithmInstance, AlgorithmRun, AlgorithmDeploymentReport
    async with container.session_factory() as session:
        algo = Algorithm(repo_url="x", name="A")
        acct = Account(name="A", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
        w = Worker(name="W", status="online")
        session.add_all([algo, acct, w]); await session.flush()
        inst = AlgorithmInstance(algorithm_id=algo.id, account_id=acct.id, worker_id=w.id, status="running")
        session.add(inst); await session.flush()
        run = AlgorithmRun(instance_id=inst.id, run_number=1, status="running",
                           started_at=datetime.now(timezone.utc) - timedelta(days=10))
        session.add(run)
        inst.active_run_id = run.id
        await session.commit()
        did, rid = inst.id, run.id

    # Write a small equity curve
    t0 = datetime.now(timezone.utc) - timedelta(days=10)
    for i in range(10):
        await container.live_sample_sink.add_equity_sample(did, rid, {
            "timestamp": (t0 + timedelta(days=i)).isoformat(),
            "portfolio_value": 100.0 * (1 + 0.01 * i),
            "cash": 50.0,
        })
    await container.live_sample_sink.flush()

    from coordinator.services.live_finalizer import LiveFinalizer
    fin = LiveFinalizer(
        session_factory=container.session_factory,
        sink=container.live_sample_sink,
        base_dir=tmp_path,
    )
    await fin.finalize_one(did)

    async with container.session_factory() as session:
        rep = (await session.execute(
            select(AlgorithmDeploymentReport).where(AlgorithmDeploymentReport.deployment_id == did)
        )).scalar_one()
        assert rep.equity_curve is not None
        assert len(rep.equity_curve) >= 1
        assert rep.runs_index and rep.runs_index[0]["run_id"] == rid
```

- [ ] **Step 2: Fail**

- [ ] **Step 3: Implement**

```python
# coordinator/services/live_finalizer.py
"""Periodic finalizer that reads per-run parquets, computes the report blob,
and upserts AlgorithmDeploymentReport.

Reuses backtest_finalizer helpers so the report payload has the same shape as
backtest results.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from coordinator.database.models import (
    AlgorithmDeploymentReport, AlgorithmInstance, AlgorithmRun,
)
from coordinator.services import backtest_finalizer as bf
from coordinator.services.backtest_metrics_qs import compute_all
from coordinator.services.live_sample_sink import LiveSampleSink

logger = logging.getLogger(__name__)


class LiveFinalizer:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession],
        sink: LiveSampleSink, base_dir: Path,
        interval_seconds: int = 15,
    ) -> None:
        self._sf = session_factory
        self._sink = sink
        self._base = Path(base_dir)
        self._interval = interval_seconds

    async def run_loop(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception:
                logger.exception("LiveFinalizer tick failed")
            await asyncio.sleep(self._interval)

    async def _tick(self) -> None:
        async with self._sf() as session:
            deps = (await session.execute(
                select(AlgorithmInstance.id).where(AlgorithmInstance.status == "running")
            )).scalars().all()
        await self._sink.flush()
        for did in deps:
            try:
                await self.finalize_one(did)
            except Exception:
                logger.exception("Failed to finalize deployment %s", did)

    async def finalize_one(self, deployment_id: str) -> None:
        await self._sink.flush()
        async with self._sf() as session:
            runs = (await session.execute(
                select(AlgorithmRun)
                .where(AlgorithmRun.instance_id == deployment_id)
                .order_by(AlgorithmRun.run_number.asc())
            )).scalars().all()
            run_meta = [
                {"run_id": r.id, "run_number": r.run_number,
                 "started_at": r.started_at, "stopped_at": r.stopped_at,
                 "status": r.status}
                for r in runs
            ]

        # Concatenate per-run parquets, inserting gap rows between runs
        frames: list[pd.DataFrame] = []
        for prev, curr in zip([None] + run_meta, run_meta):
            p = self._base / deployment_id / curr["run_id"] / "equity.parquet"
            if not p.exists():
                continue
            df = pq.read_table(p).to_pandas()
            if prev is not None and prev.get("stopped_at"):
                gap_ts = prev["stopped_at"] + (curr["started_at"] - prev["stopped_at"]) / 2
                frames.append(pd.DataFrame([{
                    "timestamp": pd.Timestamp(gap_ts).tz_localize(None) if pd.Timestamp(gap_ts).tz else pd.Timestamp(gap_ts),
                    "portfolio_value": float("nan"), "cash": float("nan"),
                }]))
            frames.append(df)
        if not frames:
            return
        full = pd.concat(frames, ignore_index=True)
        # Resample to daily, drop NaN gap rows for resample.last()
        daily = full.set_index("timestamp").resample("D").last().reset_index()
        if daily["portfolio_value"].dropna().empty:
            return

        # Reuse backtest helpers
        key_metrics = compute_all(daily)  # same shape as backtest report's key_metrics
        equity_curve = [
            {"timestamp": (ts.isoformat() if pd.notna(ts) else None),
             "portfolio_value": (None if pd.isna(v) else float(v))}
            for ts, v in zip(daily["timestamp"], daily["portfolio_value"])
        ]
        drawdown_curve = bf.build_drawdown_curve(daily.dropna())
        monthly_matrix = bf.build_monthly_matrix(daily.dropna())

        scalar = key_metrics.get("strategy", {})

        async with self._sf() as session:
            existing = (await session.execute(
                select(AlgorithmDeploymentReport).where(AlgorithmDeploymentReport.deployment_id == deployment_id)
            )).scalar_one_or_none()
            if existing is None:
                existing = AlgorithmDeploymentReport(deployment_id=deployment_id)
                session.add(existing)
            existing.generated_at = datetime.now(timezone.utc)
            existing.total_return = scalar.get("total_return")
            existing.cagr = scalar.get("cagr")
            existing.volatility = scalar.get("volatility")
            existing.sharpe_ratio = scalar.get("sharpe_ratio")
            existing.sortino_ratio = scalar.get("sortino_ratio")
            existing.calmar_ratio = scalar.get("calmar_ratio")
            existing.max_drawdown = scalar.get("max_drawdown")
            existing.romad = scalar.get("romad")
            existing.trade_count = scalar.get("trade_count")
            existing.win_rate = scalar.get("win_rate")
            existing.profit_factor = scalar.get("profit_factor")
            existing.avg_win = scalar.get("avg_win")
            existing.avg_loss = scalar.get("avg_loss")
            existing.expectancy = scalar.get("expectancy")
            existing.longest_drawdown_days = scalar.get("longest_drawdown_days")
            existing.equity_curve = equity_curve
            existing.drawdown_curve = drawdown_curve
            existing.key_metrics = key_metrics
            existing.monthly_returns_matrix = monthly_matrix
            existing.runs_index = [
                {"run_id": r["run_id"], "run_number": r["run_number"],
                 "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                 "stopped_at": r["stopped_at"].isoformat() if r["stopped_at"] else None,
                 "status": r["status"]}
                for r in run_meta
            ]
            await session.commit()
```

Start the loop in `coordinator/main.py` lifespan:
```python
from coordinator.services.live_finalizer import LiveFinalizer
container.live_finalizer = LiveFinalizer(
    session_factory=container.session_factory,
    sink=container.live_sample_sink,
    base_dir=Path("data/live"),
    interval_seconds=int(os.environ.get("QT_LIVE_FINALIZE_INTERVAL_SECONDS", "15")),
)
finalizer_task = asyncio.create_task(container.live_finalizer.run_loop())
# ... cancel in finally block alongside other background tasks
```

- [ ] **Step 4: Pass + commit**

```bash
git add coordinator/services/live_finalizer.py coordinator/main.py tests/coordinator/services/test_live_finalizer.py
git commit -m "feat(coordinator): LiveFinalizer periodic report computation"
```

### Task 5.7: Report endpoint

**Files:**
- Modify: `coordinator/api/routes/deployments.py` (add `/report`)
- Test: `tests/coordinator/test_deployment_report_api.py`

- [ ] **Step 1: Failing test**

```python
# tests/coordinator/test_deployment_report_api.py
import pytest
from httpx import AsyncClient, ASGITransport
from coordinator.main import create_app
from coordinator.api.dependencies import get_container
from coordinator.database.models import Algorithm, Account, Worker, AlgorithmInstance, AlgorithmDeploymentReport


@pytest.mark.asyncio
async def test_get_report_returns_404_when_no_report():
    app = create_app()
    async with app.router.lifespan_context(app):
        container = get_container()
        async with container.session_factory() as session:
            algo = Algorithm(repo_url="x", name="A")
            acct = Account(name="A", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
            w = Worker(name="W", status="online")
            session.add_all([algo, acct, w]); await session.flush()
            inst = AlgorithmInstance(algorithm_id=algo.id, account_id=acct.id, worker_id=w.id, status="stopped")
            session.add(inst); await session.commit()
            did = inst.id
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get(f"/api/deployments/{did}/report")
        assert r.status_code == 404
```

- [ ] **Step 2: Fail**

- [ ] **Step 3: Implement**

```python
@router.get("/{deployment_id}/report")
async def get_report(deployment_id: str, db: AsyncSession = Depends(get_db)):
    rep = (await db.execute(
        select(AlgorithmDeploymentReport).where(AlgorithmDeploymentReport.deployment_id == deployment_id)
    )).scalar_one_or_none()
    if rep is None:
        raise HTTPException(status_code=404, detail="No report yet — deployment has not produced samples")
    return {
        "deployment_id": rep.deployment_id,
        "generated_at": to_iso_utc(rep.generated_at),
        "total_return": rep.total_return, "cagr": rep.cagr,
        "volatility": rep.volatility, "sharpe_ratio": rep.sharpe_ratio,
        "sortino_ratio": rep.sortino_ratio, "calmar_ratio": rep.calmar_ratio,
        "max_drawdown": rep.max_drawdown, "romad": rep.romad,
        "trade_count": rep.trade_count, "win_rate": rep.win_rate,
        "profit_factor": rep.profit_factor, "avg_win": rep.avg_win,
        "avg_loss": rep.avg_loss, "expectancy": rep.expectancy,
        "longest_drawdown_days": rep.longest_drawdown_days,
        "equity_curve": rep.equity_curve,
        "drawdown_curve": rep.drawdown_curve,
        "drawdown_periods": rep.drawdown_periods,
        "key_metrics": rep.key_metrics,
        "rolling_metrics": rep.rolling_metrics,
        "monthly_returns_matrix": rep.monthly_returns_matrix,
        "eoy_returns": rep.eoy_returns,
        "runs_index": rep.runs_index,
    }
```

Also add `/trades` endpoint that queries `TradeLog` filtered by `instance_id=deployment_id`, paged.

- [ ] **Step 4: Pass + commit**

```bash
git add coordinator/api/routes/deployments.py tests/coordinator/test_deployment_report_api.py
git commit -m "feat(api): /api/deployments/:id/report endpoint"
```

---

## Milestone 6 — Deployment Page (UI)

### Task 6.1: Page scaffold at `/deployments/:id` with header + actions

**Files:**
- Create: `dashboard/src/pages/DeploymentDetail.tsx`
- Modify: `dashboard/src/App.tsx` (add route)

- [ ] **Step 1: Add the route**

In `dashboard/src/App.tsx`:
```tsx
import { DeploymentDetail } from "./pages/DeploymentDetail";
// ...
<Route path="/deployments/:id" element={<DeploymentDetail />} />
<Route path="/instances/:id" element={<Navigate to={`/deployments/${id}`} replace />} />
```

For the redirect, use a wrapper that reads `useParams()`.

- [ ] **Step 2: Implement the header + actions**

```tsx
// dashboard/src/pages/DeploymentDetail.tsx
import { useParams, Link } from "react-router-dom";
import { ChevronLeft } from "lucide-react";
import {
  useDeployment, useDeploymentRuns, useStartDeployment, useStopDeployment,
} from "../api/hooks";
import { StatusBadge } from "../components/StatusBadge";
import { useUIStore } from "../stores/ui";

export function DeploymentDetail() {
  const { id = "" } = useParams<{ id: string }>();
  const { data: dep, isLoading } = useDeployment(id);
  const start = useStartDeployment();
  const stop = useStopDeployment();
  const addAlert = useUIStore((s) => s.addAlert);

  if (isLoading) return <p className="text-gray-400 text-sm">Loading…</p>;
  if (!dep)      return <p className="text-gray-400 text-sm">Deployment not found.</p>;

  const canStart = dep.status === "stopped" || dep.status === "error";
  const isRunning = dep.status === "running";
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3 flex-wrap">
        <Link to={`/algorithms/${dep.algorithm_id}`} className="text-gray-400 hover:text-gray-200 text-sm flex items-center gap-1">
          <ChevronLeft size={16} /> {dep.algorithm_name}
        </Link>
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <h1 className="text-2xl font-bold text-white truncate">{dep.algorithm_name}</h1>
          <StatusBadge status={dep.status} />
        </div>
        <div className="text-sm text-gray-400">
          <Link to={`/accounts/${dep.account_id}`} className="hover:text-gray-200">{dep.account_name}</Link>
          {" · "}
          <Link to={`/workers/${dep.worker_id}`} className="hover:text-gray-200">{dep.worker_name}</Link>
        </div>
        <div className="flex gap-2 ml-auto">
          {canStart && (
            <button
              onClick={() => start.mutate(id, {
                onError: () => addAlert({ message: "Failed to start.", severity: "error" }),
              })}
              className="bg-green-600 hover:bg-green-500 text-white text-sm px-3 py-1.5 rounded"
            >Start</button>
          )}
          {isRunning && (
            <button
              onClick={() => stop.mutate(id)}
              className="bg-red-600 hover:bg-red-500 text-white text-sm px-3 py-1.5 rounded"
            >Stop</button>
          )}
        </div>
      </div>
      {/* ... rest of page in following tasks */}
    </div>
  );
}
```

- [ ] **Step 3: Smoke test**

```tsx
// dashboard/src/pages/DeploymentDetail.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DeploymentDetail } from "./DeploymentDetail";

vi.mock("../api/hooks", () => ({
  useDeployment: () => ({ data: {
    id: "d1", algorithm_id: "a1", account_id: "ac1", worker_id: "w1",
    algorithm_name: "TrendBot", account_name: "Paper", worker_name: "Pi-1",
    status: "running", active_run_id: "r1", config_values: {},
    lifetime_metrics: {}, created_at: "2026-05-16T12:00:00Z", updated_at: "2026-05-16T12:00:00Z",
  }, isLoading: false }),
  useDeploymentRuns: () => ({ data: [], isLoading: false }),
  useStartDeployment: () => ({ mutate: vi.fn() }),
  useStopDeployment: () => ({ mutate: vi.fn() }),
}));

describe("DeploymentDetail", () => {
  it("renders header with algo, account, worker names and a Stop button", () => {
    const qc = new QueryClient();
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/deployments/d1"]}>
          <Routes><Route path="/deployments/:id" element={<DeploymentDetail />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByText("TrendBot")).toBeInTheDocument();
    expect(screen.getByText("Paper")).toBeInTheDocument();
    expect(screen.getByText("Pi-1")).toBeInTheDocument();
    expect(screen.getByText("Stop")).toBeInTheDocument();
  });
});
```

Run: `cd dashboard && npm test -- DeploymentDetail && npm run typecheck`
Commit:
```bash
git add dashboard/src/pages/DeploymentDetail.tsx dashboard/src/pages/DeploymentDetail.test.tsx dashboard/src/App.tsx
git commit -m "feat(dashboard): DeploymentDetail page scaffold with header and start/stop"
```

### Task 6.2: KPI row + chart grid using existing backtest report components

**Files:**
- Modify: `dashboard/src/pages/DeploymentDetail.tsx`
- Add a `useDeploymentReport(id, { refetchInterval })` hook

- [ ] **Step 1: Add the hook**

In `dashboard/src/api/hooks.ts`:
```typescript
export const useDeploymentReport = (id: string, opts?: { refetchInterval?: number | false }) =>
  useQuery({
    queryKey: ["deployment-report", id],
    queryFn: async () => (await client.get(`/api/deployments/${id}/report`)).data,
    enabled: !!id,
    refetchInterval: opts?.refetchInterval,
    retry: false,
  });
```

- [ ] **Step 2: Render KPIs and the 2×2 chart grid**

In `DeploymentDetail.tsx`, after the header section:
```tsx
const isLive = dep.status === "running" || dep.status === "starting" || dep.status === "stopping";
const { data: report } = useDeploymentReport(id, { refetchInterval: isLive ? 2000 : false });
const km = report?.key_metrics?.strategy;
return (
  <>
    {/* header... */}
    {report ? (
      <>
        <div className="grid grid-cols-1 md:grid-cols-7 gap-3">
          <KpiCard variant="hero" label="Annual Return" value={fmtPct(km?.cagr)} hint="CAGR" />
          <KpiCard label="Total Return" value={fmtPct(km?.total_return)} />
          <KpiCard label="Max Drawdown" value={fmtPct(km?.max_drawdown)} />
          <KpiCard label="RoMaD" value={fmtNum(km?.romad)} hint="CAGR / Max Drawdown" />
          <KpiCard label="Sharpe" value={fmtNum(km?.sharpe_ratio)} />
          <KpiCard label="Sortino" value={fmtNum(km?.sortino_ratio)} />
          <KpiCard label="Longest DD Days" value={fmtInt(km?.longest_drawdown_days)} />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <EquitySlot report={report} trades={[]} />
          <DrawdownSlot report={report} />
          <ReturnsDistributionSlot report={report} />
          <RollingMetricsSlot report={report} />
        </div>
      </>
    ) : (
      <p className="text-gray-500 text-sm">No samples yet — start the deployment to begin recording.</p>
    )}
  </>
);
```

Import `KpiCard`, `EquitySlot`, `DrawdownSlot`, `ReturnsDistributionSlot`, `RollingMetricsSlot` from `../components/report/...`. Lift the `fmtPct`, `fmtInt`, `fmtNum` helpers from `BacktestRunDetail.tsx` into a shared utility module `dashboard/src/lib/formatNumbers.ts` rather than duplicating.

- [ ] **Step 3: Run typecheck + commit**

```bash
git add dashboard/src/pages/DeploymentDetail.tsx dashboard/src/api/hooks.ts dashboard/src/lib/formatNumbers.ts dashboard/src/components/report
git commit -m "feat(dashboard): DeploymentDetail KPI row + 2x2 chart grid with live polling"
```

### Task 6.3: Run boundary markers on the EquitySlot

**Files:**
- Modify: `dashboard/src/components/report/EquitySlot.tsx` (accept optional `runsIndex` and draw markers)
- Modify: `dashboard/src/pages/DeploymentDetail.tsx` (pass `report.runs_index`)

- [ ] **Step 1: Read the current EquitySlot to see how lightweight-charts is used**

Run: `Read dashboard/src/components/report/EquitySlot.tsx`

- [ ] **Step 2: Add an optional `runsIndex` prop and render priceLine markers per boundary**

Use `lightweight-charts`' `createPriceLine` or per-series markers (`setMarkers`) to draw a vertical-style indicator at each `started_at` and `stopped_at`. Color: green for start, gray for stop. Tooltip carries `Run #N · <ts>`.

- [ ] **Step 3: Snapshot test that markers are computed correctly**

```tsx
// dashboard/src/components/report/EquitySlot.markers.test.ts
import { describe, it, expect } from "vitest";
import { buildRunMarkers } from "./EquitySlot";

describe("buildRunMarkers", () => {
  it("emits a marker per start and per stop", () => {
    const markers = buildRunMarkers([
      { run_id: "r1", run_number: 1, started_at: "2026-05-01T00:00:00Z", stopped_at: "2026-05-03T00:00:00Z", status: "stopped" },
      { run_id: "r2", run_number: 2, started_at: "2026-05-04T00:00:00Z", stopped_at: null, status: "running" },
    ]);
    expect(markers).toHaveLength(3);
    expect(markers[0]).toMatchObject({ position: "aboveBar", text: "Run #1 start" });
  });
});
```

Export `buildRunMarkers` from `EquitySlot.tsx` so it's unit-testable.

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/components/report/EquitySlot.tsx dashboard/src/pages/DeploymentDetail.tsx dashboard/src/components/report/EquitySlot.markers.test.ts
git commit -m "feat(dashboard): run boundary markers on the deployment equity chart"
```

### Task 6.4: Side tables, metrics + trades, runs list

**Files:**
- Modify: `dashboard/src/pages/DeploymentDetail.tsx`
- Add `useDeploymentTrades(id, opts)` hook

- [ ] **Step 1: Add the trades hook**

```typescript
export const useDeploymentTrades = (id: string, opts?: { limit?: number; refetchInterval?: number | false }) =>
  useQuery({
    queryKey: ["deployment-trades", id, opts?.limit],
    queryFn: async () => (await client.get(`/api/deployments/${id}/trades?limit=${opts?.limit ?? 500}`)).data,
    enabled: !!id, refetchInterval: opts?.refetchInterval,
  });
```

- [ ] **Step 2: Render side tables (Parameters, EoY, Drawdowns), MetricsTable, trades table, runs list**

Mirror `BacktestRunDetail.tsx` body almost exactly, replacing:
- `report.config_overrides` → `dep.config_values`
- The bare HTML trade table with a `<DataTable>`-driven one if desired (or keep inline for parity with backtest page).

Add the runs list at the bottom:
```tsx
<section>
  <h2 className="text-sm font-semibold text-gray-400 uppercase mb-3">Runs</h2>
  <DataTable data={runs ?? []} columns={runsColumns} emptyMessage="No runs yet." />
</section>
```

Define `runsColumns` with: Run #, Status (StatusBadge), Started, Ended, Duration, Net P&L, Trades.

- [ ] **Step 3: Run filter dropdown**

Add a small dropdown above the KPI row that drives a `?run=<id>` query param. When set, `useDeploymentReport(id, { ..., params: { run_id } })` re-fetches the report. (Update the backend endpoint to accept `run_id` if not done in 5.7 — if not, implement it now: query only the single run's parquet.)

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/pages/DeploymentDetail.tsx dashboard/src/api/hooks.ts
git commit -m "feat(dashboard): DeploymentDetail side tables, metrics, trades, runs list, run filter"
```

### Task 6.5: Activity panel on the deployment page

**Files:**
- Modify: `dashboard/src/pages/DeploymentDetail.tsx`

- [ ] **Step 1: Add the panel in a collapsible section**

```tsx
import { ActivityPanel } from "../components/ActivityPanel";
// ... at the bottom of the JSX:
<details className="bg-gray-900 border border-gray-800 rounded p-4">
  <summary className="cursor-pointer text-sm font-semibold text-gray-300">Activity</summary>
  <div className="mt-3">
    <ActivityPanel target={`deployment:${id}` as const} />
  </div>
</details>
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/src/pages/DeploymentDetail.tsx
git commit -m "feat(dashboard): activity panel on the deployment page"
```

---

## Milestone 7 — Page Polish (Worker, Algorithm, Overview)

### Task 7.1: WorkerDetail "Running Algorithms" section

**Files:**
- Modify: `dashboard/src/pages/WorkerDetail.tsx`

- [ ] **Step 1: Replace "Assigned Instances" with "Running Algorithms"**

Read the current `WorkerDetail.tsx`. Replace the section using `useAllInstances` with `useDeployments({ worker_id: id })`. Columns: status badge, algorithm name (linked), account name (linked), started_at (current run only, relative time), lifetime P&L. Row click → `/deployments/:id`. No GUIDs.

- [ ] **Step 2: Commit**

```bash
git add dashboard/src/pages/WorkerDetail.tsx
git commit -m "feat(dashboard): WorkerDetail Running Algorithms section with human-readable names"
```

### Task 7.2: AlgorithmDetail "Running Algorithm Deployments" + Deploy button

**Files:**
- Modify: `dashboard/src/pages/AlgorithmDetail.tsx`

- [ ] **Step 1: Use `useDeployments({ algorithm_id })` and rebuild the columns**

Columns: `[status, account, worker, started_at, lifetime_pnl]`. Row click → `/deployments/:id`.

- [ ] **Step 2: Rename "Create Instance" → "Deploy"; modal title → "Deploy Algorithm"**

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/pages/AlgorithmDetail.tsx
git commit -m "feat(dashboard): AlgorithmDetail Deployments section + Deploy button rename"
```

### Task 7.3: Overview "Running Algorithms" widget

**Files:**
- Modify: `dashboard/src/pages/Overview.tsx` (find the "Running Instances" widget, rename + columns)

- [ ] **Step 1: Rename + reshape**

Replace the widget's data source with `useDeployments()` (all). Render: algorithm name · account name · worker name · status · lifetime P&L. Click → `/deployments/:id`.

- [ ] **Step 2: Commit**

```bash
git add dashboard/src/pages/Overview.tsx
git commit -m "feat(dashboard): Overview Running Algorithms widget rename + name columns"
```

### Task 7.4: Sidebar route rename + redirect cleanup

**Files:**
- Modify: `dashboard/src/components/Layout.tsx` (if it has a nav entry for `/instances`)
- Modify: `dashboard/src/App.tsx` (legacy redirects)

- [ ] **Step 1: If a top-level `/instances` link exists, rename to `Deployments` → `/deployments`** (no top-level page yet, just deep links from algo/worker for now — leave the link out if there was none).

- [ ] **Step 2: Confirm the `/instances/:id` → `/deployments/:id` redirect added in 6.1 still works.**

- [ ] **Step 3: Run dev server, manually walk through Worker → Deployment → Run filter → back. Run final smoke test:**

```bash
cd dashboard && npm run build && npm run typecheck
```

- [ ] **Step 4: Commit any minor fixes**

```bash
git add -A
git commit -m "chore(dashboard): finalize navigation rename"
```

### Task 7.5: Remove legacy `/api/instances*` and `/api/runs/:id` UI references

**Files:**
- Remove unused hooks `useInstance`, `useInstances`, `useAllInstances` from `dashboard/src/api/hooks.ts` if nothing references them.
- Delete `dashboard/src/pages/InstanceDetail.tsx` and `dashboard/src/pages/RunDetail.tsx`.
- Remove their routes from `dashboard/src/App.tsx` (replaced by the redirect to deployments).
- Backend: keep `coordinator/api/routes/runs.py` and the `instances` routes for one more release (they're harmless and unused by the UI).

- [ ] **Step 1: Grep for residual references**

Run: `grep -rn "InstanceDetail\|RunDetail\|useInstance\|/api/instances" dashboard/src`

- [ ] **Step 2: Delete dead pages + dead imports**

- [ ] **Step 3: Build + typecheck**

Run: `cd dashboard && npm run typecheck && npm run build`

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore(dashboard): remove legacy InstanceDetail/RunDetail pages and hooks"
```

---

## Final acceptance checklist

- [ ] `pytest tests/coordinator tests/worker -v` — all green.
- [ ] `cd dashboard && npm run typecheck && npm test && npm run build` — all green.
- [ ] Worker page shows: worker info, Running Algorithms with human names, Activity stream. Heartbeat displays a sensible relative time.
- [ ] Algorithm page lists Running Algorithm Deployments with account + worker names.
- [ ] Clicking Start on a deployment causes the badge to read "Starting" within ~50ms in all visible pages (Worker, Algorithm, Overview, Deployment).
- [ ] Worker websocket disconnect causes the worker badge to flip to "Offline" within ~30s.
- [ ] Deployment page shows the KPI row, equity curve with run markers, drawdown / returns / rolling charts, metrics + trades, runs list, and an activity panel.
- [ ] Killing a worker mid-run causes a `worker_disconnected` broadcast and surfaces an `instance_error` event in Activity within 60s.

---

## Spec Coverage Map

| Spec section | Tasks |
|---|---|
| §2 Information Architecture | 2.2, 6.1, 7.1–7.5 |
| §3 Deployment Page | 6.1–6.5 |
| §4 Live Data Pipeline | 5.1–5.7 |
| §5 Worker Activity Stream | 4.1–4.6 |
| §6.1 Heartbeat tz | 1.1, 1.2 |
| §6.2 Offline transition | 1.3, 1.4 |
| §6.3 Optimistic + broadcast | 3.1, 3.2, 3.3 |
| §6.4 Acks | covered inside 3.1 error paths |
| §7 Page Polish | 7.1–7.5 |
