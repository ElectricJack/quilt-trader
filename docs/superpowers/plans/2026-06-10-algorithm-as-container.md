# Algorithm-as-Container UX Restructure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** [docs/superpowers/specs/2026-06-10-algorithm-as-container-design.md](../specs/2026-06-10-algorithm-as-container-design.md)

**Goal:** Make the algorithm the only sidebar entry point for strategy work; nest backtests, research, and deployments under `/algorithms/:id`, with legacy URLs redirecting through entity lookup.

**Architecture:** Frontend route nesting under a new `<AlgorithmShell>` layout component, plus a card-grid list page and a single-page hub. Backend gains one new `AlgorithmSummaryService` (with TTLCache) producing rollup fields injected into the existing algorithm GET endpoints, plus `algorithm_id` / `status` / `limit` query params on `GET /api/research/sessions`. No DB migrations.

**Tech Stack:** Frontend — React 18, React Router v6, TanStack Query v5, TanStack Table v8, Tailwind CSS, Lucide icons, Vitest + React Testing Library. Backend — FastAPI async, SQLAlchemy ORM (mixed async + sync sessions), pytest + pytest-asyncio + httpx.AsyncClient, existing `coordinator/api/_ttl_cache.py:TTLCache`.

---

## File structure

**Backend — create**
- `coordinator/services/algorithm_summary.py` — `AlgorithmSummaryService` aggregating per-algorithm rollup
- `tests/coordinator/api/test_algorithm_summary_endpoint.py` — integration tests for the summary field

**Backend — modify**
- `coordinator/api/routes/algorithms.py` — add `summary` field to `_algo_to_response`; instantiate cache; wire service into `list_algorithms` and `get_algorithm`
- `coordinator/api/routes/research.py` — add `algorithm_id`, `status`, `limit` query params to `list_sessions_endpoint`
- `tests/coordinator/api/test_research_routes.py` — extend with filter tests

**Frontend — create**
- `dashboard/src/components/AlgorithmShell.tsx` — layout wrapper rendering header band + `<Outlet />`
- `dashboard/src/components/AlgorithmCard.tsx` — single card on the list grid
- `dashboard/src/components/AlgorithmStatusBadge.tsx` — `● live | ● paper | ● idle`
- `dashboard/src/components/EquitySparkline.tsx` — inline SVG sparkline
- `dashboard/src/components/AlgorithmKpiRow.tsx` — KPI cards row used on hub
- `dashboard/src/pages/AlgorithmsGrid.tsx` — new list page (replaces `Algorithms.tsx`)
- `dashboard/src/pages/AlgorithmHub.tsx` — new hub page (replaces `AlgorithmDetail.tsx`)
- `dashboard/src/pages/AlgorithmBacktestsList.tsx` — `/algorithms/:id/backtests`
- `dashboard/src/pages/AlgorithmResearchList.tsx` — `/algorithms/:id/research`
- `dashboard/src/pages/AlgorithmDeploymentsList.tsx` — `/algorithms/:id/deployments`
- `dashboard/src/pages/AlgorithmConfig.tsx` — `/algorithms/:id/config`
- `dashboard/src/pages/redirects/LegacyBacktestRunRedirect.tsx`
- `dashboard/src/pages/redirects/LegacyBacktestsCompareRedirect.tsx`
- `dashboard/src/pages/redirects/LegacyResearchSessionRedirect.tsx`
- `dashboard/src/pages/redirects/LegacyDeploymentRedirect.tsx`
- Sibling `*.test.tsx` files for new components.

**Frontend — modify**
- `dashboard/src/App.tsx` — add nested routes under `/algorithms/:id`, legacy redirect routes
- `dashboard/src/components/Layout.tsx` — remove `Backtests` and `Research` from `NAV_ITEMS`
- `dashboard/src/api/hooks.ts` — add `useAlgorithmSummary(id)`, extend `useResearchSessions({algorithm_id, status, limit})`, add key factories
- `dashboard/src/api/client.ts` (or equivalent) — add typed call signatures for new query params

**Frontend — delete (after Phase E lands)**
- `dashboard/src/pages/Algorithms.tsx` (replaced by `AlgorithmsGrid.tsx`)
- `dashboard/src/pages/AlgorithmDetail.tsx` (replaced by `AlgorithmHub.tsx`)

---

## Phase A — Backend foundations

### Task 1: Add filters to `GET /api/research/sessions`

**Files:**
- Modify: `coordinator/api/routes/research.py` — `list_sessions_endpoint` at lines 252-268
- Modify: `tests/coordinator/api/test_research_routes.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/coordinator/api/test_research_routes.py`:

```python
@pytest.mark.asyncio
async def test_list_sessions_filters_by_algorithm_id(test_client, db_session_factory, seeded_algorithm):
    """algorithm_id query param scopes the result list."""
    other_id = f"algo-{uuid.uuid4().hex[:8]}"
    async with db_session_factory() as s:
        s.add(Algorithm(id=other_id, repo_url="https://x.com/o", name="other", source_path="/tmp/o"))
        # Two sessions for seeded_algorithm
        for i in range(2):
            s.add(OptimizationSession(
                name=f"mine-{i}-{uuid.uuid4().hex[:6]}",
                hypothesis="h",
                algorithm_id=seeded_algorithm.id,
                base_config={},
                parameter_space=json.dumps({}),
                pre_registered_criteria=json.dumps({}),
                status="open",
                date_range_start=date(2023, 1, 1),
                date_range_end=date(2024, 12, 31),
            ))
        s.add(OptimizationSession(
            name=f"theirs-{uuid.uuid4().hex[:6]}",
            hypothesis="h",
            algorithm_id=other_id,
            base_config={},
            parameter_space=json.dumps({}),
            pre_registered_criteria=json.dumps({}),
            status="open",
            date_range_start=date(2023, 1, 1),
            date_range_end=date(2024, 12, 31),
        ))
        await s.commit()

    resp = await test_client.get(f"/api/research/sessions?algorithm_id={seeded_algorithm.id}")
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()]
    assert sum(1 for n in names if n.startswith("mine-")) == 2
    assert not any(n.startswith("theirs-") for n in names)


@pytest.mark.asyncio
async def test_list_sessions_status_multi_value(test_client, db_session_factory, seeded_algorithm):
    """status query accepts comma-separated values."""
    async with db_session_factory() as s:
        for status in ("open", "completed", "archived"):
            s.add(OptimizationSession(
                name=f"st-{status}-{uuid.uuid4().hex[:6]}",
                hypothesis="h",
                algorithm_id=seeded_algorithm.id,
                base_config={},
                parameter_space=json.dumps({}),
                pre_registered_criteria=json.dumps({}),
                status=status,
                date_range_start=date(2023, 1, 1),
                date_range_end=date(2024, 12, 31),
            ))
        await s.commit()

    resp = await test_client.get("/api/research/sessions?status=open,completed")
    assert resp.status_code == 200
    statuses = {s["status"] for s in resp.json()}
    assert statuses == {"open", "completed"}


@pytest.mark.asyncio
async def test_list_sessions_limit(test_client, db_session_factory, seeded_algorithm):
    async with db_session_factory() as s:
        for i in range(7):
            s.add(OptimizationSession(
                name=f"lim-{i}-{uuid.uuid4().hex[:6]}",
                hypothesis="h",
                algorithm_id=seeded_algorithm.id,
                base_config={},
                parameter_space=json.dumps({}),
                pre_registered_criteria=json.dumps({}),
                status="open",
                date_range_start=date(2023, 1, 1),
                date_range_end=date(2024, 12, 31),
            ))
        await s.commit()

    resp = await test_client.get("/api/research/sessions?limit=3")
    assert resp.status_code == 200
    assert len(resp.json()) == 3
```

- [ ] **Step 2: Run tests, expect failure**

```bash
cd /home/jkern/dev/quilt-trader && uv run pytest tests/coordinator/api/test_research_routes.py::test_list_sessions_filters_by_algorithm_id tests/coordinator/api/test_research_routes.py::test_list_sessions_status_multi_value tests/coordinator/api/test_research_routes.py::test_list_sessions_limit -v
```

Expected: FAIL — the endpoint ignores the new params today.

- [ ] **Step 3: Add filter support**

Replace `list_sessions_endpoint` in `coordinator/api/routes/research.py` (lines 252-268) with:

```python
@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions_endpoint(
    algorithm_id: str | None = Query(None),
    status: str | None = Query(None, description="Comma-separated status values"),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> list[SessionResponse]:
    """List OptimizationSessions, newest first, optionally filtered."""
    q = select(OptimizationSession)
    if algorithm_id:
        q = q.where(OptimizationSession.algorithm_id == algorithm_id)
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if statuses:
            q = q.where(OptimizationSession.status.in_(statuses))
    q = q.order_by(OptimizationSession.created_at.desc()).limit(limit)
    sessions = (await db.execute(q)).scalars().all()
    out = []
    for s in sessions:
        cnt_result = await db.execute(
            select(BacktestRun).where(BacktestRun.optimization_session_id == s.id)
        )
        n_runs = len(cnt_result.scalars().all())
        out.append(_session_to_response(s, n_runs))
    return out
```

Confirm `Query` is already imported from `fastapi`; if not, add it to the imports at the top of the file.

- [ ] **Step 4: Run tests, expect pass**

```bash
cd /home/jkern/dev/quilt-trader && uv run pytest tests/coordinator/api/test_research_routes.py -v
```

Expected: PASS for the three new tests, plus pre-existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add coordinator/api/routes/research.py tests/coordinator/api/test_research_routes.py
git commit -m "feat(research): filter sessions by algorithm_id, status, limit"
```

---

### Task 2: Build `AlgorithmSummaryService`

**Files:**
- Create: `coordinator/services/algorithm_summary.py`
- Create: `tests/coordinator/services/test_algorithm_summary.py`

The service produces a JSON-serializable summary dict per algorithm:

```python
{
    "status": "live" | "paper" | "idle",
    "status_source": str | None,        # instance_id when not idle
    "headline_sharpe": float | None,
    "headline_sharpe_source": "live_30d" | "last_backtest" | None,
    "equity_sparkline": list[float],     # up to 60 downsampled points
    "equity_sparkline_source": "live" | "backtest" | None,
    "counts": {
        "deployments": int,
        "backtests": int,
        "research_sessions": int,
    },
    "last_activity_at": str | None,      # ISO 8601 UTC
}
```

Status precedence: any instance with `status == "live"` → `live`; else any instance with `status == "paper"` → `paper`; else `idle`. The `status_source` is the chosen instance's id (or None if idle).

Sharpe precedence: if `live` and the active instance has `lifetime_metrics.sharpe_30d`, use that with source `live_30d`; else most-recent completed `BacktestRun.sharpe_ratio` with source `last_backtest`; else None.

Sparkline precedence: latest `AlgorithmRun.equity_curve` for any instance (downsampled to ≤60 points using existing `_downsample` helper in `algorithms.py`); else latest completed `BacktestRun.equity_curve`; else `[]`. Source flag reports which.

`last_activity_at`: max of latest `AlgorithmRun.updated_at`, latest `BacktestRun.created_at`, latest `OptimizationSession.created_at`. None if none exist.

- [ ] **Step 1: Create test file**

```bash
mkdir -p /home/jkern/dev/quilt-trader/tests/coordinator/services
touch /home/jkern/dev/quilt-trader/tests/coordinator/services/__init__.py
```

Write `tests/coordinator/services/test_algorithm_summary.py`:

```python
import uuid
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import sessionmaker

from coordinator.database.models import (
    Base, Algorithm, AlgorithmInstance, AlgorithmRun,
    BacktestRun, OptimizationSession,
)
from coordinator.services.algorithm_summary import AlgorithmSummaryService


@pytest_asyncio.fixture
async def async_session_factory(tmp_path):
    db_file = tmp_path / "summary.db"
    sync_url = f"sqlite:///{db_file}"
    async_url = f"sqlite+aiosqlite:///{db_file}"
    engine = create_engine(sync_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    engine.dispose()
    async_engine = create_async_engine(async_url)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    yield factory
    await async_engine.dispose()


def _make_algo(s, **kw):
    algo_id = kw.pop("id", f"a-{uuid.uuid4().hex[:6]}")
    algo = Algorithm(
        id=algo_id, repo_url="https://x.com/r", name=kw.pop("name", "test"),
        source_path="/tmp/a", install_status="installed",
        **kw,
    )
    s.add(algo)
    return algo


@pytest.mark.asyncio
async def test_idle_algorithm_no_data(async_session_factory):
    async with async_session_factory() as s:
        algo = _make_algo(s)
        await s.commit()
        svc = AlgorithmSummaryService()
        summary = await svc.build_for(algo.id, s)

    assert summary["status"] == "idle"
    assert summary["status_source"] is None
    assert summary["headline_sharpe"] is None
    assert summary["equity_sparkline"] == []
    assert summary["counts"] == {"deployments": 0, "backtests": 0, "research_sessions": 0}
    assert summary["last_activity_at"] is None


@pytest.mark.asyncio
async def test_live_status_promoted_over_paper(async_session_factory):
    async with async_session_factory() as s:
        algo = _make_algo(s)
        s.add(AlgorithmInstance(
            id=f"i-{uuid.uuid4().hex[:6]}", algorithm_id=algo.id,
            account_id="acct-1", worker_id="w-1", status="paper",
        ))
        live = AlgorithmInstance(
            id=f"i-{uuid.uuid4().hex[:6]}", algorithm_id=algo.id,
            account_id="acct-2", worker_id="w-1", status="live",
        )
        s.add(live)
        await s.commit()
        svc = AlgorithmSummaryService()
        summary = await svc.build_for(algo.id, s)

    assert summary["status"] == "live"
    assert summary["status_source"] == live.id
    assert summary["counts"]["deployments"] == 2


@pytest.mark.asyncio
async def test_sharpe_falls_back_to_last_backtest(async_session_factory):
    async with async_session_factory() as s:
        algo = _make_algo(s)
        s.add(BacktestRun(
            id="r-1", algorithm_id=algo.id, status="completed",
            sharpe_ratio=1.4, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ))
        await s.commit()
        svc = AlgorithmSummaryService()
        summary = await svc.build_for(algo.id, s)

    assert summary["headline_sharpe"] == pytest.approx(1.4)
    assert summary["headline_sharpe_source"] == "last_backtest"


@pytest.mark.asyncio
async def test_sparkline_downsampled_to_60(async_session_factory):
    async with async_session_factory() as s:
        algo = _make_algo(s)
        s.add(BacktestRun(
            id="r-1", algorithm_id=algo.id, status="completed",
            equity_curve=[{"equity": float(i)} for i in range(500)],
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ))
        await s.commit()
        svc = AlgorithmSummaryService()
        summary = await svc.build_for(algo.id, s)

    assert len(summary["equity_sparkline"]) == 60
    assert summary["equity_sparkline"][0] == 0.0
    assert summary["equity_sparkline"][-1] >= 480.0


@pytest.mark.asyncio
async def test_counts_and_last_activity(async_session_factory):
    async with async_session_factory() as s:
        algo = _make_algo(s)
        s.add(BacktestRun(
            id="r-1", algorithm_id=algo.id, status="completed",
            created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        ))
        s.add(BacktestRun(
            id="r-2", algorithm_id=algo.id, status="completed",
            created_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        ))
        s.add(OptimizationSession(
            name="sess-1", hypothesis="h", algorithm_id=algo.id,
            base_config={}, parameter_space="{}", pre_registered_criteria="{}",
            status="open",
            date_range_start=date(2023, 1, 1), date_range_end=date(2024, 12, 31),
            created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        ))
        await s.commit()
        svc = AlgorithmSummaryService()
        summary = await svc.build_for(algo.id, s)

    assert summary["counts"] == {"deployments": 0, "backtests": 2, "research_sessions": 1}
    assert summary["last_activity_at"].startswith("2026-06-01")
```

- [ ] **Step 2: Run tests, expect failure (import error)**

```bash
cd /home/jkern/dev/quilt-trader && uv run pytest tests/coordinator/services/test_algorithm_summary.py -v
```

Expected: FAIL — `AlgorithmSummaryService` does not exist.

- [ ] **Step 3: Implement the service**

Create `coordinator/services/algorithm_summary.py`:

```python
"""Aggregate per-algorithm rollup for list/hub pages."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.database.models import (
    Algorithm,
    AlgorithmInstance,
    AlgorithmRun,
    BacktestRun,
    OptimizationSession,
)
from coordinator.api.utils.serialization import to_iso_utc


def _downsample(curve: list[dict], target: int = 60) -> list[float]:
    if not curve:
        return []
    points = [float(p.get("equity", 0.0)) for p in curve]
    if len(points) <= target:
        return points
    step = len(points) / target
    return [points[int(i * step)] for i in range(target)]


def _pick_status_instance(instances: list[AlgorithmInstance]) -> AlgorithmInstance | None:
    for status in ("live", "paper"):
        for inst in instances:
            if (inst.status or "").lower() == status:
                return inst
    return None


class AlgorithmSummaryService:
    """Produces a rollup dict for an algorithm.

    Pure aggregation — caller is responsible for caching.
    """

    async def build_for(self, algorithm_id: str, db: AsyncSession) -> dict[str, Any]:
        instances = (
            await db.execute(
                select(AlgorithmInstance).where(
                    AlgorithmInstance.algorithm_id == algorithm_id
                )
            )
        ).scalars().all()

        backtests_count = (
            await db.execute(
                select(func.count(BacktestRun.id)).where(
                    BacktestRun.algorithm_id == algorithm_id
                )
            )
        ).scalar_one()

        sessions_count = (
            await db.execute(
                select(func.count(OptimizationSession.id)).where(
                    OptimizationSession.algorithm_id == algorithm_id
                )
            )
        ).scalar_one()

        # Status
        active = _pick_status_instance(instances)
        if active is None:
            status = "idle"
            status_source = None
        else:
            status = (active.status or "idle").lower()
            status_source = active.id

        # Latest live run (for sharpe + sparkline)
        latest_run = None
        if active is not None:
            latest_run = (
                await db.execute(
                    select(AlgorithmRun)
                    .where(AlgorithmRun.instance_id == active.id)
                    .order_by(desc(AlgorithmRun.run_number))
                    .limit(1)
                )
            ).scalar_one_or_none()

        # Latest completed backtest (used for sharpe + sparkline fallback)
        latest_bt = (
            await db.execute(
                select(BacktestRun)
                .where(BacktestRun.algorithm_id == algorithm_id)
                .where(BacktestRun.status == "completed")
                .order_by(desc(BacktestRun.created_at))
                .limit(1)
            )
        ).scalar_one_or_none()

        # Headline sharpe
        headline_sharpe: float | None = None
        headline_sharpe_source: str | None = None
        if active is not None and active.lifetime_metrics:
            live_sharpe = active.lifetime_metrics.get("sharpe_30d")
            if live_sharpe is not None:
                headline_sharpe = float(live_sharpe)
                headline_sharpe_source = "live_30d"
        if headline_sharpe is None and latest_bt is not None and latest_bt.sharpe_ratio is not None:
            headline_sharpe = float(latest_bt.sharpe_ratio)
            headline_sharpe_source = "last_backtest"

        # Sparkline
        sparkline: list[float] = []
        sparkline_source: str | None = None
        if latest_run is not None and latest_run.equity_curve:
            sparkline = _downsample(latest_run.equity_curve, target=60)
            sparkline_source = "live"
        elif latest_bt is not None and latest_bt.equity_curve:
            sparkline = _downsample(latest_bt.equity_curve, target=60)
            sparkline_source = "backtest"

        # Last activity
        last_activity_dt: datetime | None = None
        for candidate in (
            latest_run.updated_at if latest_run else None,
            latest_bt.created_at if latest_bt else None,
        ):
            if candidate and (last_activity_dt is None or candidate > last_activity_dt):
                last_activity_dt = candidate
        latest_session = (
            await db.execute(
                select(OptimizationSession)
                .where(OptimizationSession.algorithm_id == algorithm_id)
                .order_by(desc(OptimizationSession.created_at))
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest_session and (
            last_activity_dt is None or latest_session.created_at > last_activity_dt
        ):
            last_activity_dt = latest_session.created_at

        return {
            "status": status,
            "status_source": status_source,
            "headline_sharpe": headline_sharpe,
            "headline_sharpe_source": headline_sharpe_source,
            "equity_sparkline": sparkline,
            "equity_sparkline_source": sparkline_source,
            "counts": {
                "deployments": len(instances),
                "backtests": int(backtests_count),
                "research_sessions": int(sessions_count),
            },
            "last_activity_at": to_iso_utc(last_activity_dt) if last_activity_dt else None,
        }
```

If `coordinator/api/utils/serialization.py:to_iso_utc` doesn't exist at the imported path, find the real path by grepping for `def to_iso_utc` and fix the import.

- [ ] **Step 4: Run tests, expect pass**

```bash
cd /home/jkern/dev/quilt-trader && uv run pytest tests/coordinator/services/test_algorithm_summary.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/algorithm_summary.py tests/coordinator/services/__init__.py tests/coordinator/services/test_algorithm_summary.py
git commit -m "feat(algorithms): AlgorithmSummaryService for list/hub rollup"
```

---

### Task 3: Wire summary into `/api/algorithms` endpoints

**Files:**
- Modify: `coordinator/api/routes/algorithms.py`
- Create: `tests/coordinator/api/test_algorithm_summary_endpoint.py`

- [ ] **Step 1: Write failing endpoint tests**

Create `tests/coordinator/api/test_algorithm_summary_endpoint.py`:

```python
import uuid
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from httpx import ASGITransport, AsyncClient

from coordinator.database.models import Algorithm, BacktestRun


@pytest_asyncio.fixture
async def test_client(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def db_session_factory(test_app):
    from coordinator.api.dependencies import get_container
    return get_container().session_factory


@pytest.mark.asyncio
async def test_list_algorithms_includes_summary(test_client, db_session_factory):
    aid = f"a-{uuid.uuid4().hex[:6]}"
    async with db_session_factory() as s:
        s.add(Algorithm(
            id=aid, repo_url="https://x.com/r", name="x", source_path="/tmp/x",
            install_status="installed",
        ))
        s.add(BacktestRun(
            id="r-1", algorithm_id=aid, status="completed", sharpe_ratio=1.1,
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        ))
        await s.commit()

    resp = await test_client.get("/api/algorithms")
    assert resp.status_code == 200
    body = resp.json()
    mine = next(a for a in body if a["id"] == aid)
    assert "summary" in mine
    assert mine["summary"]["status"] == "idle"
    assert mine["summary"]["counts"]["backtests"] == 1
    assert mine["summary"]["headline_sharpe"] == pytest.approx(1.1)
    assert mine["summary"]["headline_sharpe_source"] == "last_backtest"


@pytest.mark.asyncio
async def test_get_algorithm_includes_summary(test_client, db_session_factory):
    aid = f"a-{uuid.uuid4().hex[:6]}"
    async with db_session_factory() as s:
        s.add(Algorithm(
            id=aid, repo_url="https://x.com/r", name="x", source_path="/tmp/x",
            install_status="installed",
        ))
        await s.commit()

    resp = await test_client.get(f"/api/algorithms/{aid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == aid
    assert "summary" in body
    assert body["summary"]["status"] == "idle"
```

- [ ] **Step 2: Run tests, expect failure**

```bash
cd /home/jkern/dev/quilt-trader && uv run pytest tests/coordinator/api/test_algorithm_summary_endpoint.py -v
```

Expected: FAIL — `summary` key missing.

- [ ] **Step 3: Wire the service**

In `coordinator/api/routes/algorithms.py`, near the existing `_git_status_cache = TTLCache(ttl_seconds=60.0)` (around line 326), add:

```python
from coordinator.services.algorithm_summary import AlgorithmSummaryService

_summary_cache = TTLCache(ttl_seconds=60.0)
_summary_service = AlgorithmSummaryService()


async def _summary_for(algorithm_id: str, db: AsyncSession) -> dict:
    async def _build():
        return await _summary_service.build_for(algorithm_id, db)
    return await _summary_cache.get(algorithm_id, _build)
```

Then modify `list_algorithms` (currently lines 257-260) to inject summary into each row:

```python
@router.get("/api/algorithms")
async def list_algorithms(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Algorithm))
    algos = result.scalars().all()
    out = []
    for a in algos:
        row = _algo_to_response(a)
        row["summary"] = await _summary_for(a.id, db)
        out.append(row)
    return out
```

And modify `get_algorithm` (currently at line 263) to inject summary on the single-item response. Locate the `return _algo_to_response(algo)` line in `get_algorithm` and change it to:

```python
    row = _algo_to_response(algo)
    row["summary"] = await _summary_for(algo.id, db)
    return row
```

- [ ] **Step 4: Run tests, expect pass**

```bash
cd /home/jkern/dev/quilt-trader && uv run pytest tests/coordinator/api/test_algorithm_summary_endpoint.py tests/coordinator/api/ -v
```

Expected: both new tests PASS, plus pre-existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add coordinator/api/routes/algorithms.py tests/coordinator/api/test_algorithm_summary_endpoint.py
git commit -m "feat(api): inject summary rollup into GET /api/algorithms{,/:id}"
```

---

## Phase B — Frontend foundation (route shell, hooks)

### Task 4: Extend API hooks for summary + research filters

**Files:**
- Modify: `dashboard/src/api/hooks.ts`
- Modify: `dashboard/src/api/client.ts` (or wherever `api.listAlgorithms` lives — confirm by grep)

- [ ] **Step 1: Check current client signatures**

```bash
cd /home/jkern/dev/quilt-trader && grep -n "listAlgorithms\|listResearchSessions\|getAlgorithm " dashboard/src/api/client.ts dashboard/src/api/hooks.ts
```

Note the exact function names and types.

- [ ] **Step 2: Add TypeScript type for `summary`**

In `dashboard/src/api/client.ts` (or the file holding the `Algorithm` type), extend the `Algorithm` interface to include:

```typescript
export interface AlgorithmSummary {
  status: "live" | "paper" | "idle";
  status_source: string | null;
  headline_sharpe: number | null;
  headline_sharpe_source: "live_30d" | "last_backtest" | null;
  equity_sparkline: number[];
  equity_sparkline_source: "live" | "backtest" | null;
  counts: {
    deployments: number;
    backtests: number;
    research_sessions: number;
  };
  last_activity_at: string | null;
}

// Extend existing Algorithm interface:
export interface Algorithm {
  // ... existing fields ...
  summary?: AlgorithmSummary;
}
```

If `Algorithm` is defined in a separate `types.ts` file, edit there instead.

- [ ] **Step 3: Extend `listResearchSessions` client call**

Find `listResearchSessions` in `dashboard/src/api/client.ts` and change its signature to accept optional filters:

```typescript
export async function listResearchSessions(
  filters?: { algorithm_id?: string; status?: string; limit?: number },
): Promise<ResearchSession[]> {
  const params = new URLSearchParams();
  if (filters?.algorithm_id) params.set("algorithm_id", filters.algorithm_id);
  if (filters?.status) params.set("status", filters.status);
  if (filters?.limit !== undefined) params.set("limit", String(filters.limit));
  const qs = params.toString();
  const url = qs ? `/api/research/sessions?${qs}` : "/api/research/sessions";
  return fetchJson<ResearchSession[]>(url);
}
```

(Match the existing fetcher pattern in that file — if it uses `client.get(url)` or similar, adapt.)

- [ ] **Step 4: Extend hook key + signature**

In `dashboard/src/api/hooks.ts`, replace the existing `researchSessions` key + `useResearchSessions` hook with a filter-aware version:

```typescript
// In keys factory:
export const keys = {
  // ... existing keys ...
  researchSessions: (filters?: { algorithm_id?: string; status?: string; limit?: number }) =>
    ["research", "sessions", filters ?? {}] as const,
  // ... existing keys ...
};

export function useResearchSessions(filters?: {
  algorithm_id?: string;
  status?: string;
  limit?: number;
}) {
  return useQuery({
    queryKey: keys.researchSessions(filters),
    queryFn: () => api.listResearchSessions(filters),
  });
}
```

Update any existing call sites (e.g., the global `Research` page) to pass `undefined` so behavior stays the same.

- [ ] **Step 5: Run the dashboard test suite**

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm test -- --run
```

Expected: PASS, including any existing `useResearchSessions` tests (filters are optional so callers unchanged).

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/api/hooks.ts dashboard/src/api/client.ts
git commit -m "feat(dashboard): hook + client support for algorithm summary + research filters"
```

---

### Task 5: Build `<AlgorithmShell>` layout component

**Files:**
- Create: `dashboard/src/components/AlgorithmShell.tsx`
- Create: `dashboard/src/components/AlgorithmShell.test.tsx`

The shell fetches the algorithm by `id` from the URL, renders a header band (name, version, status badge, action buttons), and renders `<Outlet />` underneath. While loading, show a centered spinner. On error, show "Algorithm not found" with a "Back to algorithms" link.

- [ ] **Step 1: Write the test**

Create `dashboard/src/components/AlgorithmShell.test.tsx`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AlgorithmShell } from "./AlgorithmShell";

vi.mock("../api/hooks", () => ({
  useAlgorithm: (id: string) => {
    if (id === "missing") return { data: undefined, isLoading: false, error: new Error("404") };
    if (id === "loading") return { data: undefined, isLoading: true, error: null };
    return {
      data: {
        id,
        name: "crypto-tsmom",
        version: "1.4.2",
        summary: {
          status: "live",
          counts: { deployments: 3, backtests: 47, research_sessions: 2 },
        },
      },
      isLoading: false,
      error: null,
    };
  },
}));

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/algorithms/:id" element={<AlgorithmShell />}>
            <Route index element={<div>HUB CONTENT</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AlgorithmShell", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders the algorithm name and version in the header", () => {
    renderAt("/algorithms/abc");
    expect(screen.getByText("crypto-tsmom")).toBeInTheDocument();
    expect(screen.getByText(/v1\.4\.2/)).toBeInTheDocument();
  });

  it("renders the outlet content below the header", () => {
    renderAt("/algorithms/abc");
    expect(screen.getByText("HUB CONTENT")).toBeInTheDocument();
  });

  it("renders rollup counts in the header subline", () => {
    renderAt("/algorithms/abc");
    expect(screen.getByText(/3 deployments/)).toBeInTheDocument();
    expect(screen.getByText(/47 backtests/)).toBeInTheDocument();
    expect(screen.getByText(/2 research sessions/)).toBeInTheDocument();
  });

  it("shows a loading state", () => {
    renderAt("/algorithms/loading");
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows not-found state", () => {
    renderAt("/algorithms/missing");
    expect(screen.getByText(/Algorithm not found/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test, expect failure**

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm test -- --run AlgorithmShell
```

Expected: FAIL — file does not exist.

- [ ] **Step 3: Implement the shell**

Create `dashboard/src/components/AlgorithmShell.tsx`:

```typescript
import { Outlet, useParams, Link } from "react-router-dom";
import { useAlgorithm } from "../api/hooks";
import { AlgorithmStatusBadge } from "./AlgorithmStatusBadge";

export function AlgorithmShell() {
  const { id = "" } = useParams<{ id: string }>();
  const { data: algo, isLoading, error } = useAlgorithm(id);

  if (isLoading) {
    return (
      <div role="status" className="flex justify-center py-16">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
      </div>
    );
  }

  if (error || !algo) {
    return (
      <div className="px-6 py-12 text-center">
        <p className="text-lg text-gray-300">Algorithm not found</p>
        <Link to="/algorithms" className="mt-3 inline-block text-indigo-400 hover:text-indigo-300">
          ← Back to algorithms
        </Link>
      </div>
    );
  }

  const counts = algo.summary?.counts;

  return (
    <div className="px-6 py-4">
      <header className="mb-6 border-b border-gray-800 pb-4">
        <div className="flex items-baseline gap-3">
          <h1 className="text-2xl font-semibold text-gray-100">{algo.name}</h1>
          {algo.version && <span className="text-sm text-gray-500">v{algo.version}</span>}
          {algo.summary && <AlgorithmStatusBadge status={algo.summary.status} />}
        </div>
        {counts && (
          <p className="mt-1 text-sm text-gray-400">
            {counts.deployments} deployments · {counts.backtests} backtests · {counts.research_sessions} research sessions
          </p>
        )}
      </header>
      <Outlet />
    </div>
  );
}
```

(If `<AlgorithmStatusBadge>` doesn't exist yet, write a minimal stub for now — Task 7 implements the full component. For Task 5 the badge stub can be:)

```typescript
// dashboard/src/components/AlgorithmStatusBadge.tsx (stub)
export function AlgorithmStatusBadge({ status }: { status: string }) {
  return <span data-testid="status-badge">{status}</span>;
}
```

- [ ] **Step 4: Run tests, expect pass**

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm test -- --run AlgorithmShell
```

Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/AlgorithmShell.tsx dashboard/src/components/AlgorithmShell.test.tsx dashboard/src/components/AlgorithmStatusBadge.tsx
git commit -m "feat(dashboard): AlgorithmShell layout for nested algorithm routes"
```

---

### Task 6: Add nested routes (existing pages mounted inside the shell)

**Files:**
- Modify: `dashboard/src/App.tsx`
- Create: thin page wrappers in `dashboard/src/pages/` for each sub-route

**Goal:** the new URLs work today; they render the existing list/detail components wrapped in the shell. No visual change to the legacy URLs yet.

- [ ] **Step 1: Create thin wrapper pages**

Each wrapper renders the existing list/detail component pre-filtered by `algorithm_id` from `useParams`.

Create `dashboard/src/pages/AlgorithmBacktestsList.tsx`:

```typescript
import { useParams } from "react-router-dom";
import { Backtests } from "./Backtests";

export function AlgorithmBacktestsList() {
  const { id } = useParams<{ id: string }>();
  // Reuses the existing Backtests page; we'll teach it to accept algorithmId in Task 7.
  return <Backtests algorithmId={id} />;
}
```

Open `dashboard/src/pages/Backtests.tsx` and confirm it can accept an `algorithmId` prop. If it currently takes no props, add `interface Props { algorithmId?: string; }` and pass that down to its filter / hook call so it scopes to a single algorithm when provided.

Create `dashboard/src/pages/AlgorithmResearchList.tsx`:

```typescript
import { useParams } from "react-router-dom";
import { ResearchSessions } from "./Research";  // or whatever the current export is

export function AlgorithmResearchList() {
  const { id } = useParams<{ id: string }>();
  return <ResearchSessions algorithmId={id} />;
}
```

Same pattern — update the source `Research.tsx` page to accept `algorithmId?: string` and pass it to `useResearchSessions({ algorithm_id })`.

Create `dashboard/src/pages/AlgorithmDeploymentsList.tsx`:

```typescript
import { useParams } from "react-router-dom";
import { useDeployments } from "../api/hooks";
import { Link } from "react-router-dom";

export function AlgorithmDeploymentsList() {
  const { id = "" } = useParams<{ id: string }>();
  const { data, isLoading } = useDeployments({ algorithm_id: id });

  if (isLoading) return <p className="text-gray-400">Loading deployments…</p>;
  if (!data?.length) {
    return (
      <p className="text-gray-400">
        No deployments. <Link to={`/algorithms/${id}`} className="text-indigo-400">Deploy from the hub →</Link>
      </p>
    );
  }
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-gray-500">
        <tr>
          <th className="px-2 py-1">Account</th>
          <th className="px-2 py-1">Status</th>
          <th className="px-2 py-1">Today's PnL</th>
        </tr>
      </thead>
      <tbody>
        {data.map((d: any) => (
          <tr key={d.id} className="border-t border-gray-800">
            <td className="px-2 py-1">
              <Link to={`/algorithms/${id}/deployments/${d.id}`} className="text-indigo-400">
                {d.account_name ?? d.account_id}
              </Link>
            </td>
            <td className="px-2 py-1">{d.status}</td>
            <td className="px-2 py-1">{d.today_pnl?.toFixed(2) ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

Create `dashboard/src/pages/AlgorithmConfig.tsx`:

```typescript
import { useParams } from "react-router-dom";
import { useAlgorithm } from "../api/hooks";

export function AlgorithmConfig() {
  const { id = "" } = useParams<{ id: string }>();
  const { data: algo } = useAlgorithm(id);
  if (!algo) return null;
  return (
    <div className="space-y-6">
      <section>
        <h2 className="mb-2 text-lg font-medium text-gray-100">Repository</h2>
        <p className="text-sm text-gray-400">
          <a href={algo.repo_url} target="_blank" rel="noreferrer" className="text-indigo-400">
            {algo.repo_url}
          </a>{" "}
          · {algo.commit_hash?.slice(0, 8) ?? "—"}
        </p>
      </section>
      <section>
        <h2 className="mb-2 text-lg font-medium text-gray-100">Manifest</h2>
        <pre className="overflow-x-auto rounded bg-gray-950 p-3 text-xs text-gray-300">
{JSON.stringify(algo.config_schema, null, 2)}
        </pre>
      </section>
    </div>
  );
}
```

(Parameter Sets reuse stays in the existing `AlgorithmDetail.tsx` for now — moved in Task 11.)

- [ ] **Step 2: Add nested routes in `App.tsx`**

Find the existing `/algorithms/:id` route in `dashboard/src/App.tsx`. Replace the single `<Route path="/algorithms/:id" element={<AlgorithmDetail />} />` with:

```tsx
import { AlgorithmShell } from "./components/AlgorithmShell";
import { AlgorithmBacktestsList } from "./pages/AlgorithmBacktestsList";
import { AlgorithmResearchList } from "./pages/AlgorithmResearchList";
import { AlgorithmDeploymentsList } from "./pages/AlgorithmDeploymentsList";
import { AlgorithmConfig } from "./pages/AlgorithmConfig";
import { BacktestRunDetail } from "./pages/BacktestRunDetail";
import { ResearchSessionDetail } from "./pages/ResearchSessionDetail";
import { DeploymentDetail } from "./pages/DeploymentDetail";

// ...inside <Routes>:
<Route path="/algorithms/:id" element={<AlgorithmShell />}>
  <Route index element={<AlgorithmDetail />} />
  <Route path="backtests" element={<AlgorithmBacktestsList />} />
  <Route path="backtests/:runId" element={<BacktestRunDetail />} />
  <Route path="research" element={<AlgorithmResearchList />} />
  <Route path="research/:sessionId" element={<ResearchSessionDetail />} />
  <Route path="deployments" element={<AlgorithmDeploymentsList />} />
  <Route path="deployments/:instanceId" element={<DeploymentDetail />} />
  <Route path="config" element={<AlgorithmConfig />} />
</Route>
```

Adjust `BacktestRunDetail`, `ResearchSessionDetail`, `DeploymentDetail` if they read the id from `:id` — they need to read from `:runId`, `:sessionId`, `:instanceId` respectively, OR change the param names above to match what those components already use. Pick whichever requires the least surgery; the convention here matters only inside the route definitions.

- [ ] **Step 3: Manual smoke test**

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm run dev
```

In a browser: visit `/algorithms/<some-installed-algo-id>/backtests`, `/research`, `/deployments`, `/config`. Each should render with the algorithm header band and the relevant content. Confirm `/algorithms/:id` (the hub URL without sub-path) still renders the existing `AlgorithmDetail` as the index.

- [ ] **Step 4: Run dashboard tests**

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm test -- --run
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/App.tsx dashboard/src/pages/AlgorithmBacktestsList.tsx dashboard/src/pages/AlgorithmResearchList.tsx dashboard/src/pages/AlgorithmDeploymentsList.tsx dashboard/src/pages/AlgorithmConfig.tsx dashboard/src/pages/Backtests.tsx dashboard/src/pages/Research.tsx
git commit -m "feat(dashboard): nested routes under /algorithms/:id"
```

---

## Phase C — Legacy URL redirects

### Task 7: Build `<AlgorithmStatusBadge>` and `<EquitySparkline>` primitives

**Files:**
- Modify: `dashboard/src/components/AlgorithmStatusBadge.tsx` (replace the stub from Task 5)
- Create: `dashboard/src/components/AlgorithmStatusBadge.test.tsx`
- Create: `dashboard/src/components/EquitySparkline.tsx`
- Create: `dashboard/src/components/EquitySparkline.test.tsx`

- [ ] **Step 1: Test the badge**

`dashboard/src/components/AlgorithmStatusBadge.test.tsx`:

```typescript
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AlgorithmStatusBadge } from "./AlgorithmStatusBadge";

describe("AlgorithmStatusBadge", () => {
  it.each([
    ["live", "bg-emerald-500/15", "text-emerald-400"],
    ["paper", "bg-amber-500/15", "text-amber-400"],
    ["idle", "bg-gray-500/15", "text-gray-400"],
  ])("renders %s with correct color classes", (status, bg, text) => {
    render(<AlgorithmStatusBadge status={status as "live" | "paper" | "idle"} />);
    const el = screen.getByText((c) => c.includes(status));
    expect(el.className).toContain(bg);
    expect(el.className).toContain(text);
  });
});
```

- [ ] **Step 2: Implement the badge** (replace the stub)

```typescript
type Status = "live" | "paper" | "idle";

const STYLES: Record<Status, { bg: string; text: string; label: string }> = {
  live:  { bg: "bg-emerald-500/15", text: "text-emerald-400", label: "● live" },
  paper: { bg: "bg-amber-500/15",   text: "text-amber-400",   label: "● paper" },
  idle:  { bg: "bg-gray-500/15",    text: "text-gray-400",    label: "● idle" },
};

export function AlgorithmStatusBadge({ status }: { status: Status }) {
  const s = STYLES[status] ?? STYLES.idle;
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${s.bg} ${s.text}`}>
      {s.label}
    </span>
  );
}
```

- [ ] **Step 3: Test the sparkline**

`dashboard/src/components/EquitySparkline.test.tsx`:

```typescript
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { EquitySparkline } from "./EquitySparkline";

describe("EquitySparkline", () => {
  it("renders an SVG path when given points", () => {
    const { container } = render(<EquitySparkline points={[1, 2, 3, 2, 5]} />);
    const path = container.querySelector("path");
    expect(path).not.toBeNull();
    expect(path!.getAttribute("d")).toBeTruthy();
  });

  it("renders empty placeholder when points is empty", () => {
    const { container } = render(<EquitySparkline points={[]} />);
    expect(container.querySelector("path")).toBeNull();
    expect(container.textContent).toMatch(/no data/i);
  });

  it("colors green when last >= first, red otherwise", () => {
    const { container: up } = render(<EquitySparkline points={[1, 2, 3]} />);
    expect(up.querySelector("path")!.getAttribute("stroke")).toMatch(/emerald|#34d399|#10b981/);

    const { container: down } = render(<EquitySparkline points={[3, 2, 1]} />);
    expect(down.querySelector("path")!.getAttribute("stroke")).toMatch(/red|#ef4444|#f87171/);
  });
});
```

- [ ] **Step 4: Implement the sparkline**

```typescript
interface Props {
  points: number[];
  width?: number;
  height?: number;
}

export function EquitySparkline({ points, width = 140, height = 32 }: Props) {
  if (points.length === 0) {
    return <span className="text-xs text-gray-600">no data</span>;
  }
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const dx = points.length > 1 ? width / (points.length - 1) : 0;
  const d = points
    .map((p, i) => {
      const x = i * dx;
      const y = height - ((p - min) / range) * height;
      return `${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
  const stroke = points[points.length - 1] >= points[0] ? "#10b981" : "#ef4444";
  return (
    <svg width={width} height={height} className="overflow-visible">
      <path d={d} stroke={stroke} strokeWidth={1.5} fill="none" />
    </svg>
  );
}
```

- [ ] **Step 5: Run tests, expect pass**

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm test -- --run AlgorithmStatusBadge EquitySparkline
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/components/AlgorithmStatusBadge.tsx dashboard/src/components/AlgorithmStatusBadge.test.tsx dashboard/src/components/EquitySparkline.tsx dashboard/src/components/EquitySparkline.test.tsx
git commit -m "feat(dashboard): AlgorithmStatusBadge + EquitySparkline primitives"
```

---

### Task 8: Legacy URL redirect components + routes

**Files:**
- Create: `dashboard/src/pages/redirects/LegacyBacktestRunRedirect.tsx`
- Create: `dashboard/src/pages/redirects/LegacyResearchSessionRedirect.tsx`
- Create: `dashboard/src/pages/redirects/LegacyDeploymentRedirect.tsx`
- Create: `dashboard/src/pages/redirects/LegacyBacktestsCompareRedirect.tsx`
- Create: `dashboard/src/pages/redirects/LegacyBacktestRunRedirect.test.tsx`
- Modify: `dashboard/src/App.tsx`

Each redirect component fetches the entity, reads its `algorithm_id`, and navigates to the new nested URL with `replace: true`.

- [ ] **Step 1: Write the test (one representative)**

`dashboard/src/pages/redirects/LegacyBacktestRunRedirect.test.tsx`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { LegacyBacktestRunRedirect } from "./LegacyBacktestRunRedirect";

vi.mock("../../api/hooks", () => ({
  useBacktestRun: (id: string) => {
    if (id === "missing") return { data: undefined, isLoading: false, error: new Error("404") };
    return { data: { id, algorithm_id: "algo-xyz" }, isLoading: false, error: null };
  },
}));

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/backtest-runs/:id" element={<LegacyBacktestRunRedirect />} />
          <Route path="/algorithms/:algoId/backtests/:runId" element={<div>NEW URL</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("LegacyBacktestRunRedirect", () => {
  beforeEach(() => vi.clearAllMocks());

  it("redirects to the nested URL when the run resolves", async () => {
    renderAt("/backtest-runs/run-1");
    await waitFor(() => expect(screen.getByText("NEW URL")).toBeInTheDocument());
  });

  it("shows 'not found' when the run doesn't exist", () => {
    renderAt("/backtest-runs/missing");
    expect(screen.getByText(/not found/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test, expect failure**

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm test -- --run LegacyBacktestRunRedirect
```

Expected: FAIL — file does not exist.

- [ ] **Step 3: Implement the four redirect components**

`dashboard/src/pages/redirects/LegacyBacktestRunRedirect.tsx`:

```typescript
import { useEffect } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { useBacktestRun } from "../../api/hooks";

export function LegacyBacktestRunRedirect() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data, isLoading, error } = useBacktestRun(id);

  useEffect(() => {
    if (data?.algorithm_id) {
      navigate(`/algorithms/${data.algorithm_id}/backtests/${id}`, { replace: true });
    }
  }, [data, id, navigate]);

  if (isLoading) return <p className="p-6 text-gray-400">Redirecting…</p>;
  if (error || !data) {
    return (
      <div className="p-6">
        <p className="text-gray-300">Backtest run not found.</p>
        <Link to="/algorithms" className="text-indigo-400">← Back to algorithms</Link>
      </div>
    );
  }
  return <p className="p-6 text-gray-400">Redirecting…</p>;
}
```

`dashboard/src/pages/redirects/LegacyResearchSessionRedirect.tsx` — identical pattern with `useResearchSession(id)` → `/algorithms/:algorithm_id/research/:id`.

`dashboard/src/pages/redirects/LegacyDeploymentRedirect.tsx` — identical pattern with `useDeployment(id)` → `/algorithms/:algorithm_id/deployments/:id`. If `useDeployment` doesn't exist, add it next to `useDeployments` in `hooks.ts`:

```typescript
export function useDeployment(id: string) {
  return useQuery({
    queryKey: ["deployments", "byId", id],
    queryFn: () => api.getDeployment(id),
    enabled: !!id,
  });
}
```

`dashboard/src/pages/redirects/LegacyBacktestsCompareRedirect.tsx` — handles `/backtests/:id` (the multi-run comparison page). This is more complex because runs can span algorithms. Simplest acceptable handling: redirect to `/algorithms`, deferring the multi-algorithm comparison decision per the spec's Open questions. Implementation:

```typescript
import { Navigate } from "react-router-dom";

export function LegacyBacktestsCompareRedirect() {
  return <Navigate to="/algorithms" replace />;
}
```

- [ ] **Step 4: Wire redirect routes in `App.tsx`**

Add (or replace, if existing routes conflict):

```tsx
import { LegacyBacktestRunRedirect } from "./pages/redirects/LegacyBacktestRunRedirect";
import { LegacyResearchSessionRedirect } from "./pages/redirects/LegacyResearchSessionRedirect";
import { LegacyDeploymentRedirect } from "./pages/redirects/LegacyDeploymentRedirect";
import { LegacyBacktestsCompareRedirect } from "./pages/redirects/LegacyBacktestsCompareRedirect";

// inside <Routes>:
<Route path="/backtest-runs/:id" element={<LegacyBacktestRunRedirect />} />
<Route path="/backtests/:id" element={<LegacyBacktestsCompareRedirect />} />
<Route path="/backtests" element={<Navigate to="/algorithms" replace />} />
<Route path="/research/sessions/:id" element={<LegacyResearchSessionRedirect />} />
<Route path="/research" element={<Navigate to="/algorithms" replace />} />
<Route path="/deployments/:id" element={<LegacyDeploymentRedirect />} />
```

If any of these legacy routes are currently bound to the old detail components, REMOVE the old bindings — the redirect handler takes over.

Ensure `Navigate` is imported from `react-router-dom`.

- [ ] **Step 5: Run tests, expect pass**

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm test -- --run LegacyBacktestRunRedirect
```

Expected: PASS.

- [ ] **Step 6: Manual smoke test**

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm run dev
```

Visit `/backtest-runs/<known-run-id>` and confirm the URL becomes `/algorithms/<algo>/backtests/<run>` and the run report renders. Same for `/research/sessions/<id>` and `/deployments/<id>`. Visit `/backtests` and confirm redirect to `/algorithms`.

- [ ] **Step 7: Commit**

```bash
git add dashboard/src/pages/redirects dashboard/src/App.tsx dashboard/src/api/hooks.ts
git commit -m "feat(dashboard): legacy URL redirects to nested algorithm routes"
```

---

## Phase D — New algorithm list page (card grid)

### Task 9: `<AlgorithmCard>` component

**Files:**
- Create: `dashboard/src/components/AlgorithmCard.tsx`
- Create: `dashboard/src/components/AlgorithmCard.test.tsx`

- [ ] **Step 1: Write the test**

```typescript
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AlgorithmCard } from "./AlgorithmCard";

const baseAlgo = {
  id: "a-1",
  name: "crypto-tsmom",
  version: "1.4.2",
  summary: {
    status: "live" as const,
    status_source: "inst-1",
    headline_sharpe: 1.4,
    headline_sharpe_source: "live_30d" as const,
    equity_sparkline: [100, 101, 99, 103, 105],
    equity_sparkline_source: "live" as const,
    counts: { deployments: 3, backtests: 47, research_sessions: 2 },
    last_activity_at: "2026-06-10T11:00:00Z",
  },
};

function renderCard(algo: typeof baseAlgo) {
  return render(<MemoryRouter><AlgorithmCard algorithm={algo as any} /></MemoryRouter>);
}

describe("AlgorithmCard", () => {
  it("renders name, version, and counts", () => {
    renderCard(baseAlgo);
    expect(screen.getByText("crypto-tsmom")).toBeInTheDocument();
    expect(screen.getByText(/v1\.4\.2/)).toBeInTheDocument();
    expect(screen.getByText(/3 deployments/)).toBeInTheDocument();
    expect(screen.getByText(/47 backtests/)).toBeInTheDocument();
  });

  it("renders headline Sharpe with source label", () => {
    renderCard(baseAlgo);
    expect(screen.getByText(/1\.40/)).toBeInTheDocument();
    expect(screen.getByText(/live/i)).toBeInTheDocument();
  });

  it("renders an SVG sparkline when points are present", () => {
    const { container } = renderCard(baseAlgo);
    expect(container.querySelector("svg path")).not.toBeNull();
  });

  it("links to the algorithm hub", () => {
    renderCard(baseAlgo);
    const link = screen.getByRole("link", { name: /crypto-tsmom/i });
    expect(link.getAttribute("href")).toBe("/algorithms/a-1");
  });
});
```

- [ ] **Step 2: Run test, expect failure**

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm test -- --run AlgorithmCard
```

Expected: FAIL.

- [ ] **Step 3: Implement the card**

```typescript
import { Link } from "react-router-dom";
import type { Algorithm } from "../api/client";
import { AlgorithmStatusBadge } from "./AlgorithmStatusBadge";
import { EquitySparkline } from "./EquitySparkline";

function fmt(n: number) {
  return n.toFixed(2);
}
function ago(iso: string | null): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function AlgorithmCard({ algorithm }: { algorithm: Algorithm }) {
  const s = algorithm.summary;
  return (
    <Link
      to={`/algorithms/${algorithm.id}`}
      className="block rounded-lg border border-gray-800 bg-gray-900 p-4 hover:border-gray-700"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-medium text-gray-100">{algorithm.name}</span>
        {s && <AlgorithmStatusBadge status={s.status} />}
      </div>
      <div className="mt-0.5 flex items-baseline justify-between text-xs text-gray-500">
        <span>v{algorithm.version ?? "—"}</span>
        {s?.headline_sharpe != null && (
          <span>
            Sharpe {fmt(s.headline_sharpe)}
            <span className="ml-1 text-gray-600">· {s.headline_sharpe_source === "live_30d" ? "live" : "last backtest"}</span>
          </span>
        )}
      </div>
      <div className="mt-3">
        <EquitySparkline points={s?.equity_sparkline ?? []} width={240} height={36} />
      </div>
      {s && (
        <div className="mt-3 text-xs text-gray-400">
          {s.counts.deployments} deployments · {s.counts.backtests} backtests · {s.counts.research_sessions} research
        </div>
      )}
      <div className="mt-1 text-xs text-gray-600">Last run {ago(s?.last_activity_at ?? null)}</div>
    </Link>
  );
}
```

- [ ] **Step 4: Run tests, expect pass**

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm test -- --run AlgorithmCard
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/AlgorithmCard.tsx dashboard/src/components/AlgorithmCard.test.tsx
git commit -m "feat(dashboard): AlgorithmCard for grid page"
```

---

### Task 10: New `AlgorithmsGrid` page replaces the old list

**Files:**
- Create: `dashboard/src/pages/AlgorithmsGrid.tsx`
- Create: `dashboard/src/pages/AlgorithmsGrid.test.tsx`
- Modify: `dashboard/src/App.tsx` — replace `Algorithms` import + route

- [ ] **Step 1: Write tests**

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AlgorithmsGrid } from "./AlgorithmsGrid";

const algos = [
  {
    id: "a-1", name: "crypto-tsmom", version: "1.4.2",
    summary: {
      status: "live", status_source: null,
      headline_sharpe: 1.4, headline_sharpe_source: "live_30d",
      equity_sparkline: [1, 2, 3], equity_sparkline_source: "live",
      counts: { deployments: 3, backtests: 47, research_sessions: 2 },
      last_activity_at: "2026-06-10T11:00:00Z",
    },
  },
  {
    id: "a-2", name: "alpha-picks", version: "2.1.0",
    summary: {
      status: "idle", status_source: null,
      headline_sharpe: 0.8, headline_sharpe_source: "last_backtest",
      equity_sparkline: [], equity_sparkline_source: null,
      counts: { deployments: 0, backtests: 8, research_sessions: 0 },
      last_activity_at: "2026-06-05T00:00:00Z",
    },
  },
];

vi.mock("../api/hooks", () => ({
  useAlgorithms: () => ({ data: algos, isLoading: false }),
}));

function renderGrid() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AlgorithmsGrid />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AlgorithmsGrid", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders one card per algorithm", () => {
    renderGrid();
    expect(screen.getByText("crypto-tsmom")).toBeInTheDocument();
    expect(screen.getByText("alpha-picks")).toBeInTheDocument();
  });

  it("filters by name when the search input changes", () => {
    renderGrid();
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: "alpha" } });
    expect(screen.queryByText("crypto-tsmom")).not.toBeInTheDocument();
    expect(screen.getByText("alpha-picks")).toBeInTheDocument();
  });

  it("sorts by name when selected", () => {
    renderGrid();
    fireEvent.change(screen.getByLabelText(/sort/i), { target: { value: "name" } });
    const names = screen.getAllByRole("link").map((n) => n.textContent ?? "");
    expect(names[0]).toContain("alpha-picks");
    expect(names[1]).toContain("crypto-tsmom");
  });
});
```

- [ ] **Step 2: Run tests, expect failure**

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm test -- --run AlgorithmsGrid
```

Expected: FAIL.

- [ ] **Step 3: Implement the grid page**

```typescript
import { useMemo, useState } from "react";
import { useAlgorithms } from "../api/hooks";
import { AlgorithmCard } from "../components/AlgorithmCard";

type Sort = "activity" | "name" | "sharpe" | "backtests";

function sortFor(algos: any[], sort: Sort) {
  const copy = [...algos];
  switch (sort) {
    case "name":
      copy.sort((a, b) => a.name.localeCompare(b.name));
      break;
    case "sharpe":
      copy.sort((a, b) => (b.summary?.headline_sharpe ?? -Infinity) - (a.summary?.headline_sharpe ?? -Infinity));
      break;
    case "backtests":
      copy.sort((a, b) => (b.summary?.counts.backtests ?? 0) - (a.summary?.counts.backtests ?? 0));
      break;
    case "activity":
    default:
      copy.sort((a, b) => {
        const da = a.summary?.last_activity_at ? new Date(a.summary.last_activity_at).getTime() : 0;
        const db = b.summary?.last_activity_at ? new Date(b.summary.last_activity_at).getTime() : 0;
        return db - da;
      });
  }
  return copy;
}

export function AlgorithmsGrid() {
  const { data, isLoading } = useAlgorithms();
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<Sort>("activity");

  const view = useMemo(() => {
    if (!data) return [];
    const filtered = search
      ? data.filter((a: any) => a.name.toLowerCase().includes(search.toLowerCase()))
      : data;
    return sortFor(filtered, sort);
  }, [data, search, sort]);

  if (isLoading) return <p className="p-6 text-gray-400">Loading algorithms…</p>;

  if (!data || data.length === 0) {
    return (
      <div className="px-6 py-12 text-center">
        <p className="text-lg text-gray-300">No algorithms installed yet.</p>
        <p className="mt-2 text-sm text-gray-500">
          Install your first algorithm to start backtesting and deploying.
        </p>
        {/* TODO: surface the existing install button here */}
      </div>
    );
  }

  return (
    <div className="px-6 py-4">
      <div className="mb-4 flex items-center gap-3">
        <input
          placeholder="Search algorithms…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-64 rounded border border-gray-800 bg-gray-950 px-3 py-1.5 text-sm text-gray-100"
        />
        <label className="flex items-center gap-2 text-xs text-gray-400">
          Sort
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as Sort)}
            className="rounded border border-gray-800 bg-gray-950 px-2 py-1 text-xs text-gray-100"
          >
            <option value="activity">Last activity</option>
            <option value="name">Name</option>
            <option value="sharpe">Sharpe</option>
            <option value="backtests"># backtests</option>
          </select>
        </label>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {view.map((a: any) => (
          <AlgorithmCard key={a.id} algorithm={a} />
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Replace the route in `App.tsx`**

Find `import { Algorithms } from "./pages/Algorithms";` and change to:

```tsx
import { AlgorithmsGrid } from "./pages/AlgorithmsGrid";
```

Find `<Route path="/algorithms" element={<Algorithms />} />` and change to:

```tsx
<Route path="/algorithms" element={<AlgorithmsGrid />} />
```

Leave the install-from-URL CTA (if it lived inside the old `Algorithms.tsx` header) — copy it into `AlgorithmsGrid.tsx` if needed, replacing the `{/* TODO */}` marker above.

- [ ] **Step 5: Run tests, expect pass**

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm test -- --run AlgorithmsGrid
```

Expected: PASS.

- [ ] **Step 6: Manual smoke test**

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm run dev
```

Visit `/algorithms`. Confirm card grid with sparklines, search filters by name, sort dropdown reorders cards.

- [ ] **Step 7: Commit + delete old Algorithms page**

```bash
git rm dashboard/src/pages/Algorithms.tsx 2>/dev/null || true
git add dashboard/src/pages/AlgorithmsGrid.tsx dashboard/src/pages/AlgorithmsGrid.test.tsx dashboard/src/App.tsx
git commit -m "feat(dashboard): replace algorithms list with card grid"
```

If `Algorithms.tsx` still has unique logic (e.g., the install-from-URL modal), DON'T delete it yet — just leave it unrouted and remove in a later cleanup commit after porting the install flow.

---

## Phase E — New algorithm hub page

### Task 11: `<AlgorithmKpiRow>` + hub page skeleton

**Files:**
- Create: `dashboard/src/components/AlgorithmKpiRow.tsx`
- Create: `dashboard/src/pages/AlgorithmHub.tsx`
- Modify: `dashboard/src/App.tsx` — point the index route at `AlgorithmHub`

- [ ] **Step 1: Implement KpiRow** (no test — purely presentational; covered by hub test in next task)

```typescript
interface Props {
  livePnl?: number;
  sharpe?: number | null;
  sharpeSource?: "live_30d" | "last_backtest" | null;
  maxDrawdown?: number | null;
  openPositions?: number;
}

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded border border-gray-800 bg-gray-900 px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-gray-500">{label}</div>
      <div className="mt-1 text-xl font-semibold text-gray-100">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-gray-500">{sub}</div>}
    </div>
  );
}

export function AlgorithmKpiRow(props: Props) {
  const sharpeSub = props.sharpeSource === "live_30d" ? "live · 30d" :
                    props.sharpeSource === "last_backtest" ? "last backtest" : undefined;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <Kpi label="Live PnL" value={props.livePnl != null ? `$${props.livePnl.toFixed(2)}` : "—"} sub="today" />
      <Kpi label="Sharpe" value={props.sharpe != null ? props.sharpe.toFixed(2) : "—"} sub={sharpeSub} />
      <Kpi label="Max DD" value={props.maxDrawdown != null ? `${(props.maxDrawdown * 100).toFixed(1)}%` : "—"} sub={sharpeSub} />
      <Kpi label="Open positions" value={props.openPositions != null ? String(props.openPositions) : "—"} />
    </div>
  );
}
```

- [ ] **Step 2: Implement hub skeleton**

Create `dashboard/src/pages/AlgorithmHub.tsx`. For now it renders the KPI row and placeholder sections — preview tables come in Tasks 12-13.

```typescript
import { useParams } from "react-router-dom";
import { useAlgorithm } from "../api/hooks";
import { AlgorithmKpiRow } from "../components/AlgorithmKpiRow";

export function AlgorithmHub() {
  const { id = "" } = useParams<{ id: string }>();
  const { data: algo } = useAlgorithm(id);
  if (!algo) return null;
  const s = algo.summary;

  return (
    <div className="space-y-8">
      <AlgorithmKpiRow
        sharpe={s?.headline_sharpe ?? null}
        sharpeSource={s?.headline_sharpe_source ?? null}
      />
      <section>
        <h2 className="mb-2 text-lg font-medium text-gray-100">Recent backtests</h2>
        <div data-testid="recent-backtests-placeholder" className="text-sm text-gray-500">
          loading…
        </div>
      </section>
      <section>
        <h2 className="mb-2 text-lg font-medium text-gray-100">Active research</h2>
        <div data-testid="active-research-placeholder" className="text-sm text-gray-500">
          loading…
        </div>
      </section>
      <section>
        <h2 className="mb-2 text-lg font-medium text-gray-100">Live deployments</h2>
        <div data-testid="live-deployments-placeholder" className="text-sm text-gray-500">
          loading…
        </div>
      </section>
    </div>
  );
}
```

- [ ] **Step 3: Wire AlgorithmHub as the shell's index route**

In `App.tsx`, change the index route under `/algorithms/:id` from `<AlgorithmDetail />` to `<AlgorithmHub />`:

```tsx
<Route path="/algorithms/:id" element={<AlgorithmShell />}>
  <Route index element={<AlgorithmHub />} />
  {/* sub-routes from Task 6 */}
</Route>
```

The old `AlgorithmDetail` page is now unreferenced but still in the repo — remove in Phase F cleanup.

- [ ] **Step 4: Manual smoke test**

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm run dev
```

Visit `/algorithms/<installed-algo-id>`. Confirm the page renders the header band, KPI row, and three placeholder sections. No tests yet — Task 12 adds them along with the preview content.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/AlgorithmKpiRow.tsx dashboard/src/pages/AlgorithmHub.tsx dashboard/src/App.tsx
git commit -m "feat(dashboard): AlgorithmHub skeleton with KPI row"
```

---

### Task 12: Recent backtests preview section

**Files:**
- Modify: `dashboard/src/pages/AlgorithmHub.tsx`
- Create: `dashboard/src/pages/AlgorithmHub.test.tsx`

- [ ] **Step 1: Write tests**

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AlgorithmHub } from "./AlgorithmHub";

const algo = {
  id: "a-1", name: "crypto-tsmom", version: "1.4.2",
  summary: {
    status: "live", headline_sharpe: 1.4, headline_sharpe_source: "live_30d",
    counts: { deployments: 1, backtests: 3, research_sessions: 1 },
    equity_sparkline: [], last_activity_at: null,
  },
};
const recentRuns = [
  { id: "r-1", name: "sweep-1.1", status: "completed", sharpe_ratio: 1.2, cagr: 0.15, completed_at: "2026-06-09" },
  { id: "r-2", name: "sweep-1.2", status: "running", sharpe_ratio: null, cagr: null, completed_at: null },
];
const sessions = [
  { id: 7, name: "vol-target-sweep", kind: "sweep", status: "open", progress: 0.5 },
];

vi.mock("../api/hooks", () => ({
  useAlgorithm: () => ({ data: algo }),
  useBacktestRuns: (params: any) => ({ data: recentRuns, isLoading: false }),
  useResearchSessions: (params: any) => ({ data: sessions, isLoading: false }),
  useDeployments: (params: any) => ({ data: [], isLoading: false }),
}));

function renderAt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/algorithms/a-1"]}>
        <Routes>
          <Route path="/algorithms/:id" element={<AlgorithmHub />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AlgorithmHub – recent backtests", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders up to 5 recent backtests with link to view all", () => {
    renderAt();
    expect(screen.getByText("sweep-1.1")).toBeInTheDocument();
    expect(screen.getByText("sweep-1.2")).toBeInTheDocument();
    const viewAll = screen.getByRole("link", { name: /view all 3/i });
    expect(viewAll.getAttribute("href")).toBe("/algorithms/a-1/backtests");
  });

  it("renders active research session with progress", () => {
    renderAt();
    expect(screen.getByText("vol-target-sweep")).toBeInTheDocument();
    expect(screen.getByText(/50%|0\.5/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Add `useBacktestRuns` hook if it doesn't exist**

In `dashboard/src/api/hooks.ts`:

```typescript
export function useBacktestRuns(params?: { algorithm_id?: string; limit?: number; offset?: number }) {
  return useQuery({
    queryKey: ["backtest-runs", params ?? {}] as const,
    queryFn: () => api.listBacktestRuns(params),
    enabled: params?.algorithm_id ? !!params.algorithm_id : true,
  });
}
```

Add `listBacktestRuns(params)` to `client.ts` matching the URLSearchParams pattern from Task 4.

- [ ] **Step 3: Run tests, expect failure**

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm test -- --run AlgorithmHub
```

Expected: FAIL.

- [ ] **Step 4: Implement the preview sections**

Replace the placeholder sections in `AlgorithmHub.tsx`:

```typescript
import { useParams, Link } from "react-router-dom";
import { useAlgorithm, useBacktestRuns, useResearchSessions, useDeployments } from "../api/hooks";
import { AlgorithmKpiRow } from "../components/AlgorithmKpiRow";

export function AlgorithmHub() {
  const { id = "" } = useParams<{ id: string }>();
  const { data: algo } = useAlgorithm(id);
  const { data: runs = [] } = useBacktestRuns({ algorithm_id: id, limit: 5 });
  const { data: sessions = [] } = useResearchSessions({
    algorithm_id: id,
    status: "open,running,completed",
    limit: 5,
  });
  const { data: deployments = [] } = useDeployments({ algorithm_id: id });

  if (!algo) return null;
  const s = algo.summary;

  return (
    <div className="space-y-8">
      <AlgorithmKpiRow
        sharpe={s?.headline_sharpe ?? null}
        sharpeSource={s?.headline_sharpe_source ?? null}
        openPositions={deployments.reduce((acc: number, d: any) => acc + (d.open_positions ?? 0), 0)}
      />

      <section>
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-lg font-medium text-gray-100">Recent backtests</h2>
          <Link to={`/algorithms/${id}/backtests`} className="text-sm text-indigo-400">
            view all {s?.counts.backtests ?? 0} →
          </Link>
        </div>
        {runs.length === 0 ? (
          <p className="text-sm text-gray-500">No backtests yet.</p>
        ) : (
          <table className="w-full text-sm">
            <tbody>
              {runs.slice(0, 5).map((r: any) => (
                <tr key={r.id} className="border-t border-gray-800">
                  <td className="px-2 py-1">
                    <Link to={`/algorithms/${id}/backtests/${r.id}`} className="text-indigo-400">
                      {r.name}
                    </Link>
                  </td>
                  <td className="px-2 py-1 text-gray-400">{r.status}</td>
                  <td className="px-2 py-1 text-gray-400">{r.sharpe_ratio?.toFixed(2) ?? "—"}</td>
                  <td className="px-2 py-1 text-gray-400">{r.cagr != null ? `${(r.cagr * 100).toFixed(1)}%` : "—"}</td>
                  <td className="px-2 py-1 text-gray-500">{r.completed_at ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-lg font-medium text-gray-100">Active research</h2>
          <Link to={`/algorithms/${id}/research`} className="text-sm text-indigo-400">
            view all →
          </Link>
        </div>
        {sessions.length === 0 ? (
          <p className="text-sm text-gray-500">No active research sessions.</p>
        ) : (
          <ul className="space-y-2">
            {sessions.slice(0, 5).map((sess: any) => (
              <li key={sess.id} className="rounded border border-gray-800 bg-gray-900 p-3">
                <Link to={`/algorithms/${id}/research/${sess.id}`} className="font-medium text-indigo-400">
                  {sess.name}
                </Link>
                <span className="ml-2 text-xs text-gray-500">{sess.kind ?? "session"} · {sess.status}</span>
                {sess.progress != null && (
                  <div className="mt-1 h-1 w-full overflow-hidden rounded bg-gray-800">
                    <div className="h-full bg-indigo-500" style={{ width: `${Math.round(sess.progress * 100)}%` }} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-lg font-medium text-gray-100">Live deployments</h2>
          <Link to={`/algorithms/${id}/deployments`} className="text-sm text-indigo-400">
            view all →
          </Link>
        </div>
        {deployments.length === 0 ? (
          <p className="text-sm text-gray-500">
            No deployments. Deploy this algorithm to start trading.
          </p>
        ) : (
          <table className="w-full text-sm">
            <tbody>
              {deployments.slice(0, 5).map((d: any) => (
                <tr key={d.id} className="border-t border-gray-800">
                  <td className="px-2 py-1">
                    <Link to={`/algorithms/${id}/deployments/${d.id}`} className="text-indigo-400">
                      {d.account_name ?? d.account_id}
                    </Link>
                  </td>
                  <td className="px-2 py-1 text-gray-400">{d.status}</td>
                  <td className="px-2 py-1 text-gray-400">
                    {d.today_pnl != null ? `$${d.today_pnl.toFixed(2)}` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
```

- [ ] **Step 5: Run tests, expect pass**

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm test -- --run AlgorithmHub
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/pages/AlgorithmHub.tsx dashboard/src/pages/AlgorithmHub.test.tsx dashboard/src/api/hooks.ts dashboard/src/api/client.ts
git commit -m "feat(dashboard): hub previews for backtests, research, deployments"
```

---

### Task 13: Hub action buttons + manifest/parameter-sets expanders

**Files:**
- Modify: `dashboard/src/pages/AlgorithmHub.tsx`

Wire the existing `RunBacktestModal` and `NewSweepModal` into the hub header. Surface parameter sets and manifest as collapsed-by-default expanders below the hub previews.

- [ ] **Step 1: Add modal triggers and expanders**

At the top of `AlgorithmHub.tsx`:

```typescript
import { useState } from "react";
import { RunBacktestModal } from "../components/RunBacktestModal";
import { NewSweepModal } from "../components/NewSweepModal";  // if a session-scoped modal exists; otherwise omit Sweep button until research session exists
```

Add state hooks inside the component:

```typescript
const [runBacktestOpen, setRunBacktestOpen] = useState(false);
```

Add an action bar above the KPI row (replace the placeholder spot near the top of the JSX):

```tsx
<div className="flex items-center gap-2">
  <button
    onClick={() => setRunBacktestOpen(true)}
    className="rounded bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500"
  >
    Run Backtest
  </button>
</div>

<RunBacktestModal
  open={runBacktestOpen}
  onClose={() => setRunBacktestOpen(false)}
  algorithmId={id}
  manifestConfig={(algo.config_schema?.fields as any) ?? undefined}
  parameterSets={(algo as any).parameter_sets ?? undefined}
/>
```

Note: `NewSweepModal` requires a `sessionId`. The hub's "New Sweep" button needs to first create a session — defer that to the existing "Create session" flow on the research list page. So **omit a hub-level New Sweep button for now**; users go to the Research tab to create a session and submit a sweep there.

Add collapsed expanders at the bottom of the hub:

```tsx
<details className="rounded border border-gray-800 bg-gray-900">
  <summary className="cursor-pointer px-3 py-2 text-sm text-gray-300">Parameter sets</summary>
  <div className="px-3 py-2 text-sm text-gray-400">
    {/* TODO: keep existing ParameterSetsTable component reused from AlgorithmDetail */}
    Coming soon — see <Link className="text-indigo-400" to={`/algorithms/${id}/config`}>Config</Link>.
  </div>
</details>

<details className="rounded border border-gray-800 bg-gray-900">
  <summary className="cursor-pointer px-3 py-2 text-sm text-gray-300">Manifest</summary>
  <pre className="overflow-x-auto rounded bg-gray-950 p-3 text-xs text-gray-400">
{JSON.stringify(algo.config_schema, null, 2)}
  </pre>
</details>
```

If the existing `AlgorithmDetail.tsx` has a reusable `<ParameterSetsTable>` child component, EXTRACT it to `dashboard/src/components/ParameterSetsTable.tsx` and import it into the expander to replace the TODO. Doing so unblocks deletion of `AlgorithmDetail.tsx` in Phase F.

- [ ] **Step 2: Manual smoke test**

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm run dev
```

Visit `/algorithms/<algo>`. Click "Run Backtest" → modal opens prebound to this algorithm. Open the Parameter sets and Manifest expanders.

- [ ] **Step 3: Run tests, ensure nothing broke**

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm test -- --run
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/pages/AlgorithmHub.tsx dashboard/src/components/ParameterSetsTable.tsx 2>/dev/null
git commit -m "feat(dashboard): hub action bar and config expanders"
```

---

## Phase F — Sidebar shrink, cleanup, backlog reconciliation

### Task 14: Remove Backtests + Research from sidebar

**Files:**
- Modify: `dashboard/src/components/Layout.tsx`

- [ ] **Step 1: Edit NAV_ITEMS**

In `dashboard/src/components/Layout.tsx`, lines 17-26, remove the two entries:

```typescript
const NAV_ITEMS = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/accounts", label: "Accounts", icon: Wallet },
  { to: "/data", label: "Data", icon: Database },
  { to: "/workers", label: "Workers", icon: Server },
  { to: "/algorithms", label: "Algorithms", icon: Bot },
  { to: "/settings", label: "Settings", icon: Settings },
];
```

Also remove the unused `FlaskConical` and `Microscope` icon imports from the top of the file. Run `npm run typecheck` to confirm no other reference depends on them.

- [ ] **Step 2: Manual verification**

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm run dev
```

Confirm sidebar shows 6 entries; navigating to `/algorithms` and into individual algorithms works; legacy URLs from Phase C still redirect correctly.

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/components/Layout.tsx
git commit -m "feat(dashboard): collapse Backtests + Research into algorithms sidebar entry"
```

---

### Task 15: Delete obsolete pages

**Files:**
- Delete: `dashboard/src/pages/AlgorithmDetail.tsx` (replaced by `AlgorithmHub`)
- Delete: `dashboard/src/pages/Algorithms.tsx` (replaced by `AlgorithmsGrid`) — only if Task 10 left it for the install flow port

- [ ] **Step 1: Search for stale imports**

```bash
cd /home/jkern/dev/quilt-trader && grep -rn "AlgorithmDetail\|from \"\\./pages/Algorithms\"" dashboard/src --include="*.ts" --include="*.tsx" | grep -v "AlgorithmDetail.test.tsx"
```

Note any importer. If `App.tsx` is the only one and it now uses `AlgorithmHub` / `AlgorithmsGrid`, the file is safe to delete.

- [ ] **Step 2: Delete and run tests**

```bash
cd /home/jkern/dev/quilt-trader && git rm dashboard/src/pages/AlgorithmDetail.tsx dashboard/src/pages/Algorithms.tsx
cd /home/jkern/dev/quilt-trader/dashboard && npm test -- --run && npm run typecheck
```

If `npm run typecheck` doesn't exist as a script, run `npx tsc --noEmit` from the dashboard directory.

Expected: PASS. Type errors mean a stale import remained — fix and re-run.

- [ ] **Step 3: Commit**

```bash
git commit -m "chore(dashboard): remove legacy AlgorithmDetail and Algorithms pages"
```

---

### Task 16: Backlog reconciliation

**Files:**
- Modify: `docs/superpowers/backlog.md`

The Research Lab dashboard section currently positions Phase 3/4/5 items under a separate `/research/sessions/:id` page. They now land at `/algorithms/:id/research/:session_id`. Update the entries to reflect their new home.

- [ ] **Step 1: Edit backlog**

In `docs/superpowers/backlog.md`, find the `## Research Lab dashboard` section. Add this note at the top of the section (just under the heading), and update each Phase 3/4/5 entry's "What's needed" to point at the per-algo route:

```markdown
## Research Lab dashboard

> **Re-homed by [2026-06-10-algorithm-as-container-design.md](specs/2026-06-10-algorithm-as-container-design.md).** All sub-features below now live at `/algorithms/:id/research` (list) and `/algorithms/:id/research/:session_id` (detail) rather than `/research` and `/research/sessions/:id`. Implementation routes change; user-facing scope is unchanged.
```

For each of the 9 items in the section, change every reference to:

- `/research/sessions/:id` → `/algorithms/:id/research/:session_id`
- `ResearchSessionDetail.tsx` → `AlgorithmHub` sub-route (see new file paths in the spec)
- The "Session deletion / archive", "Session list filters / search", and "Bulk job operations" entries: update their "What's needed" to point at `/algorithms/:id/research` (the per-algo research list).

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/backlog.md
git commit -m "docs(backlog): re-home Research Lab dashboard items under algorithm shell"
```

---

## Verification — final smoke test

After all 16 tasks land:

- [ ] Run the full backend suite

```bash
cd /home/jkern/dev/quilt-trader && uv run pytest tests/coordinator/ -v
```

Expected: PASS.

- [ ] Run the full dashboard suite

```bash
cd /home/jkern/dev/quilt-trader/dashboard && npm test -- --run && npx tsc --noEmit
```

Expected: PASS, zero TS errors.

- [ ] Run the coordinator + dashboard locally and walk these paths

```bash
cd /home/jkern/dev/quilt-trader && uv run python -m coordinator.main &
cd /home/jkern/dev/quilt-trader/dashboard && npm run dev
```

Verify:
- Sidebar has 6 entries (no Backtests, no Research).
- `/algorithms` renders the card grid; search filters; sort reorders.
- Clicking a card opens `/algorithms/:id` (the hub) with header band, KPI row, three preview sections, expanders.
- "Run Backtest" button opens the modal pre-bound to this algorithm; submitting kicks off a real run.
- `/algorithms/:id/backtests` lists scoped runs; clicking a row opens `/algorithms/:id/backtests/:run_id` with breadcrumb back to the hub.
- `/algorithms/:id/research` lists scoped sessions; clicking opens detail.
- `/algorithms/:id/deployments` lists scoped instances.
- `/algorithms/:id/config` shows repo + manifest.
- Legacy URLs work: paste `/backtest-runs/<known-id>`, `/research/sessions/<known-id>`, `/deployments/<known-id>` into the URL bar — each redirects to the new nested URL with the same content visible.
- `/backtests`, `/research` redirect to `/algorithms`.

If anything is off, stop and fix before claiming completion.
