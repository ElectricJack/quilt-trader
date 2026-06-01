# Session Experiment Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 6 fields to `OptimizationSession` (date range, initial cash, cost profile, optional benchmark pair) so a session is a complete pre-registered experiment definition; refactor `validation/sweep.py` + `walk_forward.py` to take session-scoped fields as explicit kwargs instead of mining them from `merged = {**base_config, **trial_config}`.

**Architecture:** Alembic migration drops the 1 legacy session and adds 6 new columns. `_run_one_backtest()` (and `_run_oos_backtest()`) take `algorithm_id`/`date_range_start`/`date_range_end`/`initial_cash`/`cost_profile`/`benchmark_symbol`/`benchmark_source` as named kwargs; `base_config` becomes algorithm config only. `sweep_endpoint` / `walk_forward_endpoint` ship session scope as top-level keys in `request_payload`. The hack in commit `df006f8` (`base_config_with_algo = {...}`) is removed. CLI, API, and dashboard all align.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.x async + sync, Alembic, pytest + pytest-asyncio, React 18 + Vite + TypeScript, TanStack Query, vitest + @testing-library/react.

**Spec:** [`docs/superpowers/specs/2026-05-31-session-experiment-scope-design.md`](../specs/2026-05-31-session-experiment-scope-design.md)

---

## File map

### Created
- `coordinator/database/migrations/versions/<hash>_session_experiment_scope.py`
- `dashboard/src/components/ExperimentScopeFields.tsx`
- `dashboard/src/components/ExperimentScopeFields.test.tsx`

### Modified
- `coordinator/database/models.py` — `OptimizationSession` gains 6 columns
- `coordinator/services/validation/optimization_session.py` — `create_session()` signature grows 6 kwargs
- `coordinator/services/validation/sweep.py` — `_run_one_backtest` + `run_sweep` signatures take session-scoped fields as kwargs
- `coordinator/services/validation/walk_forward.py` — `_run_oos_backtest` + `run_walk_forward` signatures; `_pick_best_train_config` strip-list shrinks
- `coordinator/services/research_job_manager.py` — `_dispatch_sweep` + `_dispatch_walk_forward` plumb new kwargs
- `coordinator/api/routes/research.py` — `CreateSessionRequest`+`SessionResponse` grow; `sweep_endpoint`+`walk_forward_endpoint` send clean payload (removes `df006f8` hack)
- `sdk/cli/commands/research.py` — `session create` grows 6 flags; `session show` text output extended
- `dashboard/src/api/client.ts` — `ResearchSession` + `CreateSessionRequest` grow 6 fields
- `dashboard/src/components/NewSessionModal.tsx` — adds `<ExperimentScopeFields>` row
- `dashboard/src/components/NewSessionModal.test.tsx` — 3 tests updated
- `dashboard/src/components/ResearchSessionSummary.tsx` — inline scope line
- `dashboard/src/pages/Research.tsx` — Date range column added
- `dashboard/src/pages/Research.test.tsx` + `ResearchSessionDetail.test.tsx` — fixtures grow 6 fields
- Test fixture fan-out (Task 5):
  - `tests/coordinator/services/test_research_job_manager.py:26,154`
  - `tests/coordinator/services/validation/test_report.py:34,94`
  - `tests/coordinator/api/test_research_jobs_endpoints.py:27,210`
  - `tests/coordinator/api/test_research_routes.py:108`
  - `tests/coordinator/database/test_session.py:28`
  - `tests/coordinator/database/test_research_job_model.py:22`
- Sweep/WF tests (Task 4):
  - `tests/coordinator/services/validation/test_sweep.py`
  - `tests/coordinator/services/validation/test_walk_forward.py`

---

## Task 1: Migration + model

**Files:**
- Modify: `coordinator/database/models.py` (`OptimizationSession` class)
- Create: `coordinator/database/migrations/versions/<hash>_session_experiment_scope.py`

### Step 1: Update model

Edit `coordinator/database/models.py`. Find the `OptimizationSession` class. Append 6 new columns after the existing `algorithm_id` / `base_config` block:

```python
from datetime import date  # ensure imported
from sqlalchemy import Date, Float  # ensure imported

class OptimizationSession(Base):
    __tablename__ = "optimization_sessions"

    # ... existing columns unchanged ...
    algorithm_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("algorithms.id", ondelete="RESTRICT"), nullable=False,
    )
    base_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # NEW (this spec)
    date_range_start: Mapped[date] = mapped_column(Date, nullable=False)
    date_range_end: Mapped[date] = mapped_column(Date, nullable=False)
    initial_cash: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="10000.0",
    )
    cost_profile: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="default",
    )
    benchmark_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    benchmark_source: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # ... rest unchanged (parameter_space, pre_registered_criteria, status, etc.) ...
```

If `Date` / `Float` / `date` aren't already imported, add them.

### Step 2: Generate migration

```
cd /home/jkern/dev/quilt-trader
alembic revision --autogenerate -m "session experiment scope"
```

Inspect the generated file. Replace its `upgrade()` and `downgrade()` bodies with this exact code:

```python
def upgrade() -> None:
    # 1. NULL out optimization_session_id on backtest_runs to preserve them.
    op.execute(
        "UPDATE backtest_runs SET optimization_session_id = NULL "
        "WHERE optimization_session_id IS NOT NULL"
    )
    # 2. Drop research_jobs that reference the 1 legacy session.
    op.execute(
        "DELETE FROM research_jobs WHERE session_id IN "
        "(SELECT id FROM optimization_sessions)"
    )
    # 3. Wipe the 1 legacy session.
    op.execute("DELETE FROM optimization_sessions")
    # 4. Add the 6 new columns NOT NULL (safe — table is now empty).
    with op.batch_alter_table("optimization_sessions") as batch:
        batch.add_column(sa.Column("date_range_start", sa.Date(), nullable=False))
        batch.add_column(sa.Column("date_range_end", sa.Date(), nullable=False))
        batch.add_column(sa.Column(
            "initial_cash", sa.Float(),
            nullable=False, server_default="10000.0",
        ))
        batch.add_column(sa.Column(
            "cost_profile", sa.String(32),
            nullable=False, server_default="default",
        ))
        batch.add_column(sa.Column("benchmark_symbol", sa.String(32), nullable=True))
        batch.add_column(sa.Column("benchmark_source", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("optimization_sessions") as batch:
        batch.drop_column("benchmark_source")
        batch.drop_column("benchmark_symbol")
        batch.drop_column("cost_profile")
        batch.drop_column("initial_cash")
        batch.drop_column("date_range_end")
        batch.drop_column("date_range_start")
```

Preserve the auto-filled `revision` / `down_revision` / `branch_labels` / `depends_on` at the top of the file.

### Step 3: Apply

```
alembic upgrade head
```

Expected: clean, no errors.

### Step 4: Verify

```
python3 -c "
import sqlite3
con = sqlite3.connect('data/quilt_trader.db')
print('sessions:', con.execute('SELECT COUNT(*) FROM optimization_sessions').fetchone()[0])
print('backtest_runs:', con.execute('SELECT COUNT(*) FROM backtest_runs').fetchone()[0])
print('orphaned runs:', con.execute('SELECT COUNT(*) FROM backtest_runs WHERE optimization_session_id IS NULL').fetchone()[0])
print()
print(con.execute(\"SELECT sql FROM sqlite_master WHERE name='optimization_sessions'\").fetchone()[0])
"
```

Expected: 0 sessions, backtest_runs preserved with all NULL, schema includes the 6 new columns with correct NOT NULL / nullability / defaults.

### Step 5: Commit

```
git add coordinator/database/models.py \
        coordinator/database/migrations/versions/*session_experiment_scope*.py
git commit -m "feat(db): OptimizationSession gains date range, cash, cost, benchmark fields

Drop 1 legacy session. NULL out optimization_session_id on historical
BacktestRun rows. Add date_range_start (Date), date_range_end (Date),
initial_cash (Float, default 10000), cost_profile (String, default
'default'), benchmark_symbol + benchmark_source (nullable pair).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `create_session()` service signature

**Files:**
- Modify: `coordinator/services/validation/optimization_session.py`
- Modify: `tests/coordinator/services/validation/test_optimization_session.py`

### Step 1: Add failing tests

Append to `tests/coordinator/services/validation/test_optimization_session.py`:

```python
import pytest
from datetime import date
from coordinator.database.models import Algorithm, OptimizationSession
from coordinator.services.validation.optimization_session import create_session


def test_create_session_persists_date_range_and_cash(db_session):
    _seed_algorithm(db_session)  # helper from existing tests (Task 2 of prior plan)
    sess = create_session(
        db_session,
        name="t-scope-1",
        hypothesis="h",
        algorithm_id="test-algo-fixture",
        base_config={},
        parameter_space={"x": [1]},
        pre_registered_criteria={"min_sharpe": 0.0},
        date_range_start=date(2023, 1, 1),
        date_range_end=date(2024, 12, 31),
        initial_cash=25000.0,
        cost_profile="paid_tier",
    )
    db_session.flush()
    assert sess.date_range_start == date(2023, 1, 1)
    assert sess.date_range_end == date(2024, 12, 31)
    assert sess.initial_cash == 25000.0
    assert sess.cost_profile == "paid_tier"
    assert sess.benchmark_symbol is None
    assert sess.benchmark_source is None


def test_create_session_persists_benchmark_pair_set(db_session):
    _seed_algorithm(db_session, id="bench-set-fixture")
    sess = create_session(
        db_session,
        name="t-scope-2",
        hypothesis="h",
        algorithm_id="bench-set-fixture",
        base_config={},
        parameter_space={"x": [1]},
        pre_registered_criteria={"min_sharpe": 0.0},
        date_range_start=date(2023, 1, 1),
        date_range_end=date(2024, 12, 31),
        benchmark_symbol="SPY",
        benchmark_source="polygon",
    )
    db_session.flush()
    assert sess.benchmark_symbol == "SPY"
    assert sess.benchmark_source == "polygon"


def test_create_session_persists_benchmark_pair_null(db_session):
    _seed_algorithm(db_session, id="bench-null-fixture")
    sess = create_session(
        db_session,
        name="t-scope-3",
        hypothesis="h",
        algorithm_id="bench-null-fixture",
        base_config={},
        parameter_space={"x": [1]},
        pre_registered_criteria={"min_sharpe": 0.0},
        date_range_start=date(2023, 1, 1),
        date_range_end=date(2024, 12, 31),
    )
    db_session.flush()
    assert sess.benchmark_symbol is None
    assert sess.benchmark_source is None
    # Defaults applied via service-layer defaults:
    assert sess.initial_cash == 10000.0
    assert sess.cost_profile == "default"
```

Also update the 2 existing tests (`test_create_session_persists_algorithm_id_and_base_config` and `test_create_session_accepts_empty_base_config`) — add `date_range_start=date(2023,1,1), date_range_end=date(2024,12,31)` to each `create_session(...)` call.

### Step 2: Run, verify fail

```
python3 -m pytest tests/coordinator/services/validation/test_optimization_session.py -v
```

Expected: TypeError on missing required `date_range_start` / `date_range_end`.

### Step 3: Update `create_session()` signature + body

Edit `coordinator/services/validation/optimization_session.py`:

```python
from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from coordinator.database.models import OptimizationSession


def create_session(
    db: Session,
    *,
    name: str,
    hypothesis: str,
    algorithm_id: str,
    base_config: dict[str, Any],
    parameter_space: dict[str, Any],
    pre_registered_criteria: dict[str, Any],
    notes: str = "",
    # NEW (this spec) — required
    date_range_start: date,
    date_range_end: date,
    # NEW — required-with-default
    initial_cash: float = 10_000.0,
    cost_profile: str = "default",
    # NEW — optional pair
    benchmark_symbol: str | None = None,
    benchmark_source: str | None = None,
) -> OptimizationSession:
    """Create a new OptimizationSession.

    The session is the complete pre-registered experiment definition:
    algorithm, base_config, parameter_space, criteria, date range, capital,
    cost model, and optional benchmark are all immutable post-create.
    """
    sess = OptimizationSession(
        name=name,
        hypothesis=hypothesis,
        algorithm_id=algorithm_id,
        base_config=base_config,
        parameter_space=json.dumps(parameter_space),
        pre_registered_criteria=json.dumps(pre_registered_criteria),
        notes=notes,
        status="open",
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        initial_cash=initial_cash,
        cost_profile=cost_profile,
        benchmark_symbol=benchmark_symbol,
        benchmark_source=benchmark_source,
    )
    db.add(sess)
    db.flush()
    return sess
```

### Step 4: Tests pass

```
python3 -m pytest tests/coordinator/services/validation/test_optimization_session.py -v
```

Expected: all pass.

### Step 5: Commit

```
git add coordinator/services/validation/optimization_session.py \
        tests/coordinator/services/validation/test_optimization_session.py
git commit -m "feat(research): create_session takes date range, cash, cost, benchmark args

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `CreateSessionRequest` + `SessionResponse` + endpoint validation

**Files:**
- Modify: `coordinator/api/routes/research.py`
- Modify: `tests/coordinator/api/test_research_routes.py`

### Step 1: Add failing tests

Append to `tests/coordinator/api/test_research_routes.py`:

```python
@pytest.mark.asyncio
async def test_create_session_requires_date_range_start(test_client, seeded_algorithm):
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-no-start",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_end": "2024-12-31",
        # date_range_start omitted
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_session_requires_date_range_end(test_client, seeded_algorithm):
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-no-end",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2023-01-01",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_session_rejects_end_before_start(test_client, seeded_algorithm):
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-bad-range",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2024-12-31",
        "date_range_end": "2023-01-01",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_session_rejects_unpaired_benchmark(test_client, seeded_algorithm):
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-unpaired",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
        "benchmark_symbol": "SPY",
        # benchmark_source omitted
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_session_accepts_default_initial_cash_and_cost_profile(
    test_client, seeded_algorithm,
):
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-defaults",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
        # initial_cash + cost_profile omitted — server applies defaults
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["initial_cash"] == 10000.0
    assert body["cost_profile"] == "default"


@pytest.mark.asyncio
async def test_session_response_includes_all_six_new_fields(
    test_client, seeded_algorithm,
):
    create_resp = await test_client.post("/api/research/sessions", json={
        "name": "t-roundtrip-scope",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
        "initial_cash": 25000.0,
        "cost_profile": "paid_tier",
        "benchmark_symbol": "QQQ",
        "benchmark_source": "yfinance",
    })
    assert create_resp.status_code == 200
    sid = create_resp.json()["id"]
    get_resp = await test_client.get(f"/api/research/sessions/{sid}")
    body = get_resp.json()
    assert body["date_range_start"] == "2023-01-01"
    assert body["date_range_end"] == "2024-12-31"
    assert body["initial_cash"] == 25000.0
    assert body["cost_profile"] == "paid_tier"
    assert body["benchmark_symbol"] == "QQQ"
    assert body["benchmark_source"] == "yfinance"
```

Also update the existing `test_create_session_*` and `test_session_response_*` tests to include `date_range_start: "2023-01-01"` and `date_range_end: "2024-12-31"` in their POST bodies — they'll now fail without those keys.

### Step 2: Run, verify fail

```
python3 -m pytest tests/coordinator/api/test_research_routes.py -v -k "create_session or session_response"
```

Expected: 422/missing-field failures.

### Step 3: Update models in `research.py`

Find `CreateSessionRequest` (around line 43). Replace:

```python
from datetime import date as _date
from pydantic import BaseModel, Field, model_validator

class CreateSessionRequest(BaseModel):
    name: str
    hypothesis: str
    algorithm_id: str
    base_config: dict = Field(default_factory=dict)
    parameter_space: dict
    pre_registered_criteria: dict
    notes: str = ""

    # NEW (this spec)
    date_range_start: _date                                # required
    date_range_end: _date                                  # required
    initial_cash: float = 10_000.0                         # required, with default
    cost_profile: str = "default"                          # required, with default
    benchmark_symbol: str | None = None                    # optional pair
    benchmark_source: str | None = None

    @model_validator(mode="after")
    def _benchmark_pair(self):
        if (self.benchmark_symbol is None) != (self.benchmark_source is None):
            raise ValueError(
                "benchmark_symbol and benchmark_source must both be set or both be null"
            )
        return self

    @model_validator(mode="after")
    def _date_range_valid(self):
        if self.date_range_end <= self.date_range_start:
            raise ValueError("date_range_end must be after date_range_start")
        return self
```

Update `SessionResponse` to include the 6 new fields. Use string serialization for the dates (`str` in TypeScript-compatible ISO format):

```python
class SessionResponse(BaseModel):
    id: int
    name: str
    hypothesis: str
    status: str
    notes: str
    created_at: str
    completed_at: str | None = None
    algorithm_id: str
    base_config: dict
    parameter_space: dict
    pre_registered_criteria: dict
    n_runs: int
    # NEW
    date_range_start: str           # ISO date YYYY-MM-DD
    date_range_end: str
    initial_cash: float
    cost_profile: str
    benchmark_symbol: str | None = None
    benchmark_source: str | None = None
```

Update `_session_to_response` helper to include the new fields (note `.isoformat()` for the dates):

```python
def _session_to_response(sess: OptimizationSession, n_runs: int) -> SessionResponse:
    return SessionResponse(
        id=sess.id,
        name=sess.name,
        hypothesis=sess.hypothesis,
        algorithm_id=sess.algorithm_id,
        base_config=sess.base_config,
        status=sess.status,
        notes=sess.notes,
        created_at=sess.created_at.isoformat(),
        completed_at=sess.completed_at.isoformat() if sess.completed_at else None,
        parameter_space=json.loads(sess.parameter_space),
        pre_registered_criteria=json.loads(sess.pre_registered_criteria),
        n_runs=n_runs,
        # NEW
        date_range_start=sess.date_range_start.isoformat(),
        date_range_end=sess.date_range_end.isoformat(),
        initial_cash=sess.initial_cash,
        cost_profile=sess.cost_profile,
        benchmark_symbol=sess.benchmark_symbol,
        benchmark_source=sess.benchmark_source,
    )
```

Update `create_session_endpoint` body to pass the new fields to `create_session()`:

```python
sess = create_session(
    db,
    name=payload.name,
    hypothesis=payload.hypothesis,
    algorithm_id=payload.algorithm_id,
    base_config=payload.base_config,
    parameter_space=payload.parameter_space,
    pre_registered_criteria=payload.pre_registered_criteria,
    notes=payload.notes,
    date_range_start=payload.date_range_start,
    date_range_end=payload.date_range_end,
    initial_cash=payload.initial_cash,
    cost_profile=payload.cost_profile,
    benchmark_symbol=payload.benchmark_symbol,
    benchmark_source=payload.benchmark_source,
)
```

### Step 4: Tests pass

```
python3 -m pytest tests/coordinator/api/test_research_routes.py -v
```

All session-create/response tests pass. Sweep/walk-forward tests may break here because `seeded_session` is now missing the new required fields — that's handled in Task 5 (fixture fan-out).

### Step 5: Commit

```
git add coordinator/api/routes/research.py tests/coordinator/api/test_research_routes.py
git commit -m "feat(research-api): CreateSessionRequest grows 6 scope fields + validators

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Sweep + WF refactor — explicit kwargs end-to-end

**This is a coordinated atomic change.** It touches `sweep.py`, `walk_forward.py`, `research_job_manager.py`, both endpoints in `routes/research.py`, and their tests. Single commit at the end.

**Files:**
- Modify: `coordinator/services/validation/sweep.py`
- Modify: `coordinator/services/validation/walk_forward.py`
- Modify: `coordinator/services/research_job_manager.py`
- Modify: `coordinator/api/routes/research.py`
- Modify: `tests/coordinator/services/validation/test_sweep.py`
- Modify: `tests/coordinator/services/validation/test_walk_forward.py`
- Modify: `tests/coordinator/services/test_research_job_manager.py`

### Step 1: Read current state

```
sed -n '110,175p' coordinator/services/validation/sweep.py
sed -n '94,200p' coordinator/services/validation/walk_forward.py
sed -n '155,200p' coordinator/services/research_job_manager.py
```

You'll see:
- `_run_one_backtest` reads from `merged.get("algorithm_id", "")` etc.
- `_run_oos_backtest` does the same
- `_pick_best_train_config` strips `{"algorithm_id", "start", "end", "initial_cash", "symbols", "data_source", "cost_profile", "_fold_index", "_oos"}` from the winner config
- `run_sweep` and `run_walk_forward` orchestrators take `base_config` as a single dict; the scope fields leak in via base_config
- `_dispatch_sweep` and `_dispatch_walk_forward` pull from `payload["base_config"]` and pass through

### Step 2: Refactor `sweep.py`

Replace `_run_one_backtest` body:

```python
async def _run_one_backtest(
    db: Session,
    runner_factory: RunnerFactory,
    *,
    session_id: int,
    # NEW — session-scoped, explicit kwargs
    algorithm_id: str,
    date_range_start: date,
    date_range_end: date,
    initial_cash: float,
    cost_profile: str,
    benchmark_symbol: str | None,
    benchmark_source: str | None,
    # base_config is algorithm config only now
    base_config: dict[str, Any],
    config: dict[str, Any],          # trial hyperparameters
    config_hash_str: str,
) -> dict[str, Any]:
    """Spawn a single backtest. Session-scoped fields are passed explicitly;
    base_config holds only algorithm hyperparameters; config holds the trial's
    overrides. config_overrides on the BacktestRun row is the merge of the
    two (for the runner / finalizer / report)."""
    from datetime import date as _date, datetime
    from coordinator.database.models import BacktestRun

    merged = {**base_config, **config}   # algo config + trial → config_overrides

    run_row = BacktestRun(
        algorithm_id=algorithm_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        initial_cash=initial_cash,
        cost_profile=cost_profile,
        benchmark_symbol=benchmark_symbol,
        benchmark_source=benchmark_source,
        config_overrides=merged,
        config_hash=config_hash_str,
        optimization_session_id=session_id,
        status="pending",
    )
    db.add(run_row)
    db.flush()
    db.commit()  # make row visible to async runner via its own connection

    run_id = run_row.id
    await runner_factory(run_id)

    db.refresh(run_row)
    return {
        "run_id": run_row.id,
        "config_hash": config_hash_str,
        "config": config,
    }
```

Drop the `_as_date` helper inside `_run_one_backtest` (no longer needed since dates come in already-typed).

Now find `run_sweep` (around line 169 in sweep.py). Its signature grows to accept the 7 new kwargs and forwards them to `_run_one_backtest`:

```python
async def run_sweep(
    db: Session,
    runner_factory: RunnerFactory,
    *,
    session_id: int,
    manifest_path: str,
    # NEW — session-scoped
    algorithm_id: str,
    date_range_start: date,
    date_range_end: date,
    initial_cash: float,
    cost_profile: str,
    benchmark_symbol: str | None,
    benchmark_source: str | None,
    # algorithm config only
    base_config: dict[str, Any],
    parameter_space: dict[str, Any] | None,
    search: Literal["grid", "random", "latin", "tpe"] = "grid",
    max_trials: int = 50,
    parallelism: int = 1,
    seed: int = 0,
    progress_callback: Optional[ProgressCallback] = None,
) -> SweepResult:
    """..."""
    # Existing body — but where it builds the trial dict and calls
    # _run_one_backtest, pass through the new kwargs.
    ...

    # Inside the trial loop, replace the existing _run_one_backtest call:
    result = await _run_one_backtest(
        db, runner_factory,
        session_id=session_id,
        algorithm_id=algorithm_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        initial_cash=initial_cash,
        cost_profile=cost_profile,
        benchmark_symbol=benchmark_symbol,
        benchmark_source=benchmark_source,
        base_config=base_config,
        config=trial_config,
        config_hash_str=config_hash(trial_config),
    )
```

(Read the existing `run_sweep` body — the trial loop is the only place that calls `_run_one_backtest`. Adapt the surrounding code unchanged.)

### Step 3: Refactor `walk_forward.py`

Apply the symmetric change to `_run_oos_backtest`:

```python
async def _run_oos_backtest(
    db: Session,
    runner_factory: RunnerFactory,
    *,
    session_id: int,
    # NEW — session-scoped
    algorithm_id: str,
    initial_cash: float,
    cost_profile: str,
    benchmark_symbol: str | None,
    benchmark_source: str | None,
    # OOS-specific (walk-forward uses test-window dates, not session range)
    oos_start: date,
    oos_end: date,
    # algorithm config only
    base_config: dict[str, Any],
    config: dict[str, Any],
    fold_index: int,
) -> int:
    """Run a single OOS backtest with the winning config on the test window."""
    from coordinator.database.models import BacktestRun
    from coordinator.services.validation.sweep import config_hash

    merged = {**base_config, **config, "_fold_index": fold_index, "_oos": True}

    run_row = BacktestRun(
        algorithm_id=algorithm_id,
        date_range_start=oos_start,
        date_range_end=oos_end,
        initial_cash=initial_cash,
        cost_profile=cost_profile,
        benchmark_symbol=benchmark_symbol,
        benchmark_source=benchmark_source,
        config_overrides=merged,
        config_hash=config_hash(config),
        optimization_session_id=session_id,
        status="pending",
    )
    db.add(run_row)
    db.flush()
    db.commit()
    run_id = run_row.id
    await runner_factory(run_id)
    db.refresh(run_row)
    return run_row.id
```

**Note:** `_run_oos_backtest` takes `oos_start` / `oos_end` (the per-fold OOS window), NOT the session's overall `date_range_start` / `_end`. The session's overall range bounds the WF universe; each fold has its own narrower OOS window the walk-forward code computes.

Shrink `_pick_best_train_config`'s strip-list:

```python
async def _pick_best_train_config(
    db: Session, run_ids: list[str], objective: str
) -> dict[str, Any]:
    """Pick the in-sample winner by objective. Returns ONLY the sweep parameters
    (the algorithm hyperparameters varied per trial), not internal markers."""
    from coordinator.database.models import BacktestRun

    rows = db.query(BacktestRun).filter(BacktestRun.id.in_(run_ids)).all()
    metric_col = {
        "sharpe": "sharpe_ratio",
        "calmar": "calmar_ratio",
        "sortino": "sortino_ratio",
    }[objective]
    rows.sort(key=lambda r: getattr(r, metric_col, 0.0) or 0.0, reverse=True)
    best = rows[0]
    full = best.config_overrides or {}
    # config_overrides is now {**base_config, **trial} where base_config is
    # algorithm config only. Strip only the internal markers added by
    # _run_oos_backtest for fold tracking.
    _INTERNAL_KEYS = {"_fold_index", "_oos"}
    return {k: v for k, v in full.items() if k not in _INTERNAL_KEYS}
```

Update `run_walk_forward` signature and the call site to `_run_oos_backtest`. The orchestrator gains the 7 new kwargs (same as `run_sweep`) but ALSO recursively calls `run_sweep` per fold for the training window — that call must pass the new kwargs through:

```python
async def run_walk_forward(
    db: Session,
    runner_factory: RunnerFactory,
    *,
    session_id: int,
    manifest_path: str,
    # NEW — session-scoped
    algorithm_id: str,
    date_range_start: date,           # bounds the WF universe
    date_range_end: date,             # bounds the WF universe
    initial_cash: float,
    cost_profile: str,
    benchmark_symbol: str | None,
    benchmark_source: str | None,
    # algorithm config only
    base_config: dict[str, Any],
    parameter_space: dict[str, Any],
    train_years: float,
    test_years: float,
    step_months: float,
    objective: Literal["sharpe", "calmar", "sortino"],
    parallelism: int = 1,
    progress_callback: Optional[ProgressCallback] = None,
) -> WalkForwardResult:
    """..."""
    # ... existing fold-computation body using date_range_start/end ...

    for fold_idx, fold in enumerate(folds):
        # Sweep on the train window — pass the session-scoped fields through.
        sweep_result = await run_sweep(
            db, runner_factory,
            session_id=session_id,
            manifest_path=manifest_path,
            algorithm_id=algorithm_id,
            date_range_start=fold.train_start,        # fold's train window
            date_range_end=fold.train_end,
            initial_cash=initial_cash,
            cost_profile=cost_profile,
            benchmark_symbol=benchmark_symbol,
            benchmark_source=benchmark_source,
            base_config=base_config,
            parameter_space=parameter_space,
            search="grid",
            max_trials=...,
            parallelism=parallelism,
        )
        winner = await _pick_best_train_config(db, sweep_result.run_ids, objective)
        # OOS run for this fold
        oos_id = await _run_oos_backtest(
            db, runner_factory,
            session_id=session_id,
            algorithm_id=algorithm_id,
            initial_cash=initial_cash,
            cost_profile=cost_profile,
            benchmark_symbol=benchmark_symbol,
            benchmark_source=benchmark_source,
            oos_start=fold.test_start,
            oos_end=fold.test_end,
            base_config=base_config,
            config=winner,
            fold_index=fold_idx,
        )
```

Read the existing `run_walk_forward` body to preserve all the fold-iteration logic; only the call signatures to `run_sweep` and `_run_oos_backtest` change.

### Step 4: Refactor `research_job_manager.py` dispatchers

`_dispatch_sweep` (around line 157):

```python
async def _dispatch_sweep(self, session_id: int, payload: dict, progress_cb) -> None:
    if self._sync_sf is None:
        raise RuntimeError("sync_session_factory required for sweep dispatch")
    with self._sync_sf() as db:
        await self._sweep_fn(
            db, self._runner_factory,
            session_id=session_id,
            manifest_path=payload["manifest_path"],
            algorithm_id=payload["algorithm_id"],
            date_range_start=date.fromisoformat(payload["date_range_start"]),
            date_range_end=date.fromisoformat(payload["date_range_end"]),
            initial_cash=payload["initial_cash"],
            cost_profile=payload["cost_profile"],
            benchmark_symbol=payload.get("benchmark_symbol"),
            benchmark_source=payload.get("benchmark_source"),
            base_config=payload["base_config"],
            parameter_space=payload.get("parameter_space"),
            search=payload.get("search", "grid"),
            max_trials=payload.get("max_trials", 50),
            parallelism=payload.get("parallelism", 1),
            seed=payload.get("seed", 0),
            progress_callback=progress_cb,
        )
        db.commit()
```

(Add `from datetime import date` at the top of `research_job_manager.py` if missing.)

`_dispatch_walk_forward` (around line 175) — symmetric:

```python
async def _dispatch_walk_forward(self, session_id: int, payload: dict, progress_cb) -> None:
    if self._sync_sf is None:
        raise RuntimeError("sync_session_factory required for walk-forward dispatch")
    with self._sync_sf() as db:
        await self._wf_fn(
            db, self._runner_factory,
            session_id=session_id,
            manifest_path=payload["manifest_path"],
            algorithm_id=payload["algorithm_id"],
            date_range_start=date.fromisoformat(payload["date_range_start"]),
            date_range_end=date.fromisoformat(payload["date_range_end"]),
            initial_cash=payload["initial_cash"],
            cost_profile=payload["cost_profile"],
            benchmark_symbol=payload.get("benchmark_symbol"),
            benchmark_source=payload.get("benchmark_source"),
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
```

### Step 5: Refactor `routes/research.py` endpoints — clean payload

Find `sweep_endpoint` (around line 248). Replace the body section that builds `request_payload`. **Removes** the `base_config_with_algo` hack from commit `df006f8`:

```python
# Inside sweep_endpoint, after fetching sess and resolving manifest_path:
request_payload = {
    "manifest_path": manifest_path,
    "algorithm_id": sess.algorithm_id,
    "date_range_start": sess.date_range_start.isoformat(),
    "date_range_end": sess.date_range_end.isoformat(),
    "initial_cash": sess.initial_cash,
    "cost_profile": sess.cost_profile,
    "benchmark_symbol": sess.benchmark_symbol,
    "benchmark_source": sess.benchmark_source,
    "base_config": sess.base_config,    # algorithm config only
    "parameter_space": json.loads(sess.parameter_space),
    "search": payload.search,
    "max_trials": payload.max_trials,
    "parallelism": payload.parallelism,
    "seed": payload.seed,
}
```

Apply symmetric change to `walk_forward_endpoint`.

### Step 6: Update tests for sweep/wf

`tests/coordinator/services/validation/test_sweep.py` — find every call to `_run_one_backtest(...)` or `run_sweep(...)`. They currently pass `start`/`end`/`algorithm_id`/`initial_cash` via `base_config`. Move those to named kwargs:

Before:
```python
result = await _run_one_backtest(
    db, mock_runner,
    session_id=sid,
    base_config={
        "algorithm_id": "test-algo",
        "start": "2023-01-01",
        "end": "2024-12-31",
        "initial_cash": 10000,
        "vol_target": 0.10,
    },
    config={"lookback": 20},
    config_hash_str="abc",
)
```

After:
```python
result = await _run_one_backtest(
    db, mock_runner,
    session_id=sid,
    algorithm_id="test-algo",
    date_range_start=date(2023, 1, 1),
    date_range_end=date(2024, 12, 31),
    initial_cash=10000,
    cost_profile="default",
    benchmark_symbol=None,
    benchmark_source=None,
    base_config={"vol_target": 0.10},     # algorithm config only
    config={"lookback": 20},
    config_hash_str="abc",
)
```

Same pattern for `run_sweep` calls.

`tests/coordinator/services/validation/test_walk_forward.py` — analogous changes for `_run_oos_backtest` and `run_walk_forward`. The `_pick_best_train_config` tests assert what's stripped from the returned dict — update them to expect only `{_fold_index, _oos}` are stripped (not the old larger list).

`tests/coordinator/services/test_research_job_manager.py` — tests that mock `sweep_fn` / `walk_forward_fn` and assert the kwargs passed to them. Update assertions:

```python
# Inside whatever test mocks sweep_fn:
sweep_fn_mock = AsyncMock()
mgr = ResearchJobManager(..., sweep_fn=sweep_fn_mock, ...)
# Queue a job with a request_payload that includes the new keys
job_id = await mgr.create_sweep_job(session_id=sid, request_payload={
    "manifest_path": "/x/quilt.yaml",
    "algorithm_id": "algo-a",
    "date_range_start": "2023-01-01",
    "date_range_end": "2024-12-31",
    "initial_cash": 10000,
    "cost_profile": "default",
    "benchmark_symbol": None,
    "benchmark_source": None,
    "base_config": {"vol_target": 0.10},
    "parameter_space": {"lookback": [20, 50]},
    "search": "grid", "max_trials": 5, "parallelism": 1, "seed": 0,
})
# ... wait for dispatch ...
sweep_fn_mock.assert_awaited_once()
kwargs = sweep_fn_mock.call_args.kwargs
assert kwargs["algorithm_id"] == "algo-a"
assert kwargs["date_range_start"] == date(2023, 1, 1)
assert kwargs["initial_cash"] == 10000
assert kwargs["base_config"] == {"vol_target": 0.10}  # CLEAN — no scope leakage
```

### Step 7: Run all affected tests

```
python3 -m pytest tests/coordinator/services/validation/test_sweep.py -v
python3 -m pytest tests/coordinator/services/validation/test_walk_forward.py -v
python3 -m pytest tests/coordinator/services/test_research_job_manager.py -v
python3 -m pytest tests/coordinator/api/test_research_routes.py -v -k "sweep or walk_forward"
```

All green.

### Step 8: Commit

```
git add coordinator/services/validation/sweep.py \
        coordinator/services/validation/walk_forward.py \
        coordinator/services/research_job_manager.py \
        coordinator/api/routes/research.py \
        tests/coordinator/services/validation/test_sweep.py \
        tests/coordinator/services/validation/test_walk_forward.py \
        tests/coordinator/services/test_research_job_manager.py
git commit -m "refactor(research): sweep/wf take session-scoped fields as explicit kwargs

_run_one_backtest and _run_oos_backtest no longer mine merged config for
algorithm_id, date_range, initial_cash, cost_profile, benchmark. The
orchestrators (run_sweep, run_walk_forward) take them as explicit kwargs;
ResearchJobManager dispatchers plumb them from request_payload top-level
keys; sweep_endpoint and walk_forward_endpoint emit clean payload from
session row.

Removes the algorithm_id-into-base_config hack from df006f8.
_pick_best_train_config strip-list shrinks to {_fold_index, _oos}.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Test fixture fan-out for `OptimizationSession(...)` constructor sites

The model now requires 2 more fields (date_range_start, date_range_end). Every test that constructs `OptimizationSession(...)` directly needs those.

**Files:**
- Modify: `tests/coordinator/services/test_research_job_manager.py:26, 154`
- Modify: `tests/coordinator/services/validation/test_report.py:34, 94`
- Modify: `tests/coordinator/api/test_research_jobs_endpoints.py:27, 210`
- Modify: `tests/coordinator/database/test_session.py:28`
- Modify: `tests/coordinator/database/test_research_job_model.py:22`

### Step 1: For each file, add the 2 required fields

For each `OptimizationSession(...)` constructor call, add:

```python
date_range_start=date(2023, 1, 1),
date_range_end=date(2023, 12, 31),
```

Add `from datetime import date` import if missing at the top of the file.

The other 4 new fields (`initial_cash`, `cost_profile`, `benchmark_*`) have sensible DB-level defaults and don't need to be specified at the constructor unless the test cares.

### Step 2: For shared seed helpers (`_seed_session_sf`, `_seed_session`)

If the file has a `_seed_session_sf` or similar helper, update it once instead of each call site. Pattern:

```python
async def _seed_session_sf(db_session_factory, *, algorithm_id=None) -> int:
    from coordinator.database.models import OptimizationSession
    import json, uuid
    aid = algorithm_id or await _seed_algorithm_sf(db_session_factory)
    async with db_session_factory() as s:
        sess = OptimizationSession(
            name=f"sess-{uuid.uuid4().hex[:6]}",
            hypothesis="h",
            algorithm_id=aid,
            base_config={},
            parameter_space=json.dumps({"x": [1]}),
            pre_registered_criteria=json.dumps({"min_sharpe": 0.0}),
            status="open",
            # NEW (this spec)
            date_range_start=date(2023, 1, 1),
            date_range_end=date(2023, 12, 31),
        )
        s.add(sess); await s.commit(); await s.refresh(sess)
        return sess.id
```

### Step 3: Run each affected test file

```
python3 -m pytest tests/coordinator/services/test_research_job_manager.py -v
python3 -m pytest tests/coordinator/services/validation/test_report.py -v
python3 -m pytest tests/coordinator/api/test_research_jobs_endpoints.py -v
python3 -m pytest tests/coordinator/database/test_session.py -v
python3 -m pytest tests/coordinator/database/test_research_job_model.py -v
```

All green.

### Step 4: Full suite spot-check

```
python3 -m pytest tests/coordinator/ -x -q 2>&1 | tail -10
```

The only failures should be unrelated pre-existing ones (e.g. datasets isolation issues). No new failures.

### Step 5: Commit

```
git add tests/coordinator/
git commit -m "test(research): seed date_range_start/end on every OptimizationSession fixture

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: CLI `session create` flags

**Files:**
- Modify: `sdk/cli/commands/research.py` (session_create command)
- Modify: `tests/sdk/cli/test_research_cli.py`

### Step 1: Write failing test

Append to `tests/sdk/cli/test_research_cli.py`:

```python
def test_session_create_passes_date_range_and_cash():
    from unittest.mock import patch
    from click.testing import CliRunner
    from sdk.cli.main import quilt

    runner = CliRunner()
    with patch("sdk.cli.commands.research._post") as mock_post:
        mock_post.return_value = {
            "id": 42, "name": "t", "hypothesis": "h",
            "algorithm_id": "algo-x", "base_config": {},
            "parameter_space": {"x": [1]},
            "pre_registered_criteria": {"min_sharpe": 1.0},
            "status": "open", "notes": "",
            "created_at": "2026-05-31", "completed_at": None, "n_runs": 0,
            "date_range_start": "2023-01-01",
            "date_range_end": "2024-12-31",
            "initial_cash": 25000.0,
            "cost_profile": "default",
            "benchmark_symbol": None,
            "benchmark_source": None,
        }
        result = runner.invoke(quilt, [
            "research", "session", "create",
            "--name", "t",
            "--hypothesis", "h",
            "--algorithm-id", "algo-x",
            "--base-config", "{}",
            "--parameter-space", '{"x":[1]}',
            "--criteria", '{"min_sharpe":1.0}',
            "--start", "2023-01-01",
            "--end", "2024-12-31",
            "--initial-cash", "25000",
        ])
        assert result.exit_code == 0, result.output
        body = mock_post.call_args[0][1]
        assert body["date_range_start"] == "2023-01-01"
        assert body["date_range_end"] == "2024-12-31"
        assert body["initial_cash"] == 25000.0


def test_session_create_rejects_unpaired_benchmark():
    from click.testing import CliRunner
    from sdk.cli.main import quilt

    runner = CliRunner()
    result = runner.invoke(quilt, [
        "research", "session", "create",
        "--name", "t",
        "--hypothesis", "h",
        "--algorithm-id", "algo-x",
        "--base-config", "{}",
        "--parameter-space", '{"x":[1]}',
        "--criteria", '{"min_sharpe":1.0}',
        "--start", "2023-01-01",
        "--end", "2024-12-31",
        "--benchmark-symbol", "SPY",
        # --benchmark-source omitted
    ])
    assert result.exit_code != 0
    assert "benchmark" in result.output.lower()
```

### Step 2: Run, verify fail

```
python3 -m pytest tests/sdk/cli/test_research_cli.py -v -k "date_range or benchmark"
```

### Step 3: Update `session_create` command

Add 6 click options + update the body. Find the existing command and add:

```python
@session_group.command("create")
@click.option("--name", required=True, ...)
@click.option("--hypothesis", required=True, ...)
@click.option("--algorithm-id", required=True, ...)
@click.option("--base-config", required=True, ...)
@click.option("--parameter-space", required=True, ...)
@click.option("--criteria", required=True, ...)
@click.option("--notes", default="", ...)
# NEW
@click.option("--start", "date_range_start",
              type=click.DateTime(formats=["%Y-%m-%d"]),
              required=True, help="Start date (YYYY-MM-DD)")
@click.option("--end", "date_range_end",
              type=click.DateTime(formats=["%Y-%m-%d"]),
              required=True, help="End date (YYYY-MM-DD)")
@click.option("--initial-cash", type=float, default=10000.0,
              help="Initial cash (default 10000)")
@click.option("--cost-profile", default="default",
              help="Cost model (default 'default')")
@click.option("--benchmark-symbol", default=None,
              help="Benchmark symbol (paired with --benchmark-source)")
@click.option("--benchmark-source", default=None,
              help="Benchmark data source (paired with --benchmark-symbol)")
@click.pass_context
def session_create(ctx, name, hypothesis, algorithm_id, base_config,
                   parameter_space, criteria, notes,
                   date_range_start, date_range_end, initial_cash,
                   cost_profile, benchmark_symbol, benchmark_source):
    """Create a new OptimizationSession (pre-registered experiment)."""
    # Validate benchmark pair before hitting the API
    if (benchmark_symbol is None) != (benchmark_source is None):
        click.echo(
            "error: --benchmark-symbol and --benchmark-source must both be set "
            "or both be omitted",
            err=True,
        )
        ctx.exit(2)

    body = {
        "name": name,
        "hypothesis": hypothesis,
        "algorithm_id": algorithm_id,
        "base_config": _parse_json_or_yaml_or_file(base_config),
        "parameter_space": _parse_json_or_yaml_or_file(parameter_space),
        "pre_registered_criteria": _parse_json_or_yaml_or_file(criteria),
        "notes": notes,
        "date_range_start": date_range_start.strftime("%Y-%m-%d"),
        "date_range_end": date_range_end.strftime("%Y-%m-%d"),
        "initial_cash": initial_cash,
        "cost_profile": cost_profile,
        "benchmark_symbol": benchmark_symbol,
        "benchmark_source": benchmark_source,
    }
    resp = _post(ctx, "/api/research/sessions", body)
    click.echo(f"session created: id={resp['id']}, name={resp['name']}")
```

Update any existing test that calls `session create` without `--start` / `--end` — those now need them.

### Step 4: Tests pass

```
python3 -m pytest tests/sdk/cli/test_research_cli.py -v
```

### Step 5: Commit

```
git add sdk/cli/commands/research.py tests/sdk/cli/test_research_cli.py
git commit -m "feat(cli): research session create grows --start/--end/--initial-cash/--cost-profile/--benchmark-*

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: CLI `session show` text output extension

**Files:**
- Modify: `sdk/cli/commands/research.py` (session_show command)

### Step 1: Update implementation

Find `session_show`. After the existing `Base config:` line, add:

```python
click.echo(f"Date range:   {body['date_range_start']} → {body['date_range_end']}")
click.echo(f"Initial cash: ${body['initial_cash']:,.2f}")
click.echo(f"Cost profile: {body['cost_profile']}")
if body.get("benchmark_symbol"):
    click.echo(f"Benchmark:    {body['benchmark_symbol']} ({body['benchmark_source']})")
```

The `if body.get("benchmark_symbol")` keeps the line out of the output when the benchmark pair is null.

### Step 2: Smoke verify

No automated test (display-only). Just confirm the module compiles:

```
python3 -c "from sdk.cli.commands.research import session_show; print('ok')"
```

### Step 3: Commit

```
git add sdk/cli/commands/research.py
git commit -m "feat(cli): research session show displays date range + cash + cost + benchmark

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Frontend API client types

**Files:**
- Modify: `dashboard/src/api/client.ts`

### Step 1: Update `ResearchSession`

Find the existing interface. Append 6 new fields:

```typescript
export interface ResearchSession {
  id: number;
  name: string;
  hypothesis: string;
  status: "open" | "running" | "completed" | "failed";
  notes: string;
  created_at: string;
  completed_at: string | null;
  algorithm_id: string;
  base_config: Record<string, unknown>;
  parameter_space: Record<string, unknown>;
  pre_registered_criteria: Record<string, unknown>;
  n_runs: number;
  // NEW (this spec)
  date_range_start: string;          // ISO YYYY-MM-DD
  date_range_end: string;
  initial_cash: number;
  cost_profile: string;
  benchmark_symbol: string | null;
  benchmark_source: string | null;
}
```

### Step 2: Update `CreateSessionRequest`

```typescript
export interface CreateSessionRequest {
  name: string;
  hypothesis: string;
  algorithm_id: string;
  base_config: Record<string, unknown>;
  parameter_space: Record<string, unknown>;
  pre_registered_criteria: Record<string, unknown>;
  notes?: string;
  // NEW
  date_range_start: string;
  date_range_end: string;
  initial_cash?: number;              // server default 10000
  cost_profile?: string;               // server default "default"
  benchmark_symbol?: string | null;
  benchmark_source?: string | null;
}
```

`CreateSweepRequest` is unchanged.

### Step 3: Typecheck

```
cd dashboard && npx tsc --noEmit 2>&1 | head -20
```

Expected: TypeScript flags errors in `NewSessionModal.tsx`, `ResearchSessionSummary.tsx`, `Research.tsx`, and the test files that build session fixtures. These get fixed in Tasks 9-12.

### Step 4: Commit

```
git add dashboard/src/api/client.ts
git commit -m "feat(dashboard): ResearchSession + CreateSessionRequest grow 6 scope fields

Note: leaves TypeScript errors in modal/summary/list — addressed in next tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: `<ExperimentScopeFields>` component

**Files:**
- Create: `dashboard/src/components/ExperimentScopeFields.tsx`
- Create: `dashboard/src/components/ExperimentScopeFields.test.tsx`

### Step 1: Write failing tests

```typescript
// dashboard/src/components/ExperimentScopeFields.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ExperimentScopeFields } from "./ExperimentScopeFields";

const baseProps = {
  startDate: "",
  endDate: "",
  initialCash: 10000,
  costProfile: "default",
  benchmarkSymbol: "",
  benchmarkSource: "",
  onChange: () => {},
  onValidityChange: () => {},
};

describe("ExperimentScopeFields", () => {
  it("renders 6 inputs with correct labels", () => {
    render(<ExperimentScopeFields {...baseProps} />);
    expect(screen.getByLabelText(/start date/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/end date/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/initial cash/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/cost profile/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/benchmark symbol/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/benchmark source/i)).toBeInTheDocument();
  });

  it("onChange emits combined object on field change", () => {
    const onChange = vi.fn();
    render(<ExperimentScopeFields {...baseProps} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText(/start date/i),
                     { target: { value: "2023-01-01" } });
    const last = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    expect(last.date_range_start).toBe("2023-01-01");
    // unchanged fields preserved
    expect(last.initial_cash).toBe(10000);
    expect(last.cost_profile).toBe("default");
    // null benchmarks when both empty
    expect(last.benchmark_symbol).toBeNull();
    expect(last.benchmark_source).toBeNull();
  });

  it("onValidityChange false when end ≤ start", () => {
    const onValidityChange = vi.fn();
    render(
      <ExperimentScopeFields
        {...baseProps}
        startDate="2024-12-31"
        endDate="2023-01-01"
        onValidityChange={onValidityChange}
      />,
    );
    // After initial render the validator runs at least once
    expect(onValidityChange).toHaveBeenLastCalledWith(false);
  });

  it("onValidityChange true when all required fields valid + benchmark pair empty", () => {
    const onValidityChange = vi.fn();
    render(
      <ExperimentScopeFields
        {...baseProps}
        startDate="2023-01-01"
        endDate="2024-12-31"
        onValidityChange={onValidityChange}
      />,
    );
    expect(onValidityChange).toHaveBeenLastCalledWith(true);
  });

  it("onValidityChange false when only one benchmark field is set", () => {
    const onValidityChange = vi.fn();
    render(
      <ExperimentScopeFields
        {...baseProps}
        startDate="2023-01-01"
        endDate="2024-12-31"
        benchmarkSymbol="SPY"
        benchmarkSource=""
        onValidityChange={onValidityChange}
      />,
    );
    expect(onValidityChange).toHaveBeenLastCalledWith(false);
  });

  it("disabled propagates to all 6 inputs", () => {
    render(<ExperimentScopeFields {...baseProps} disabled />);
    expect(screen.getByLabelText(/start date/i)).toBeDisabled();
    expect(screen.getByLabelText(/end date/i)).toBeDisabled();
    expect(screen.getByLabelText(/initial cash/i)).toBeDisabled();
    expect(screen.getByLabelText(/cost profile/i)).toBeDisabled();
    expect(screen.getByLabelText(/benchmark symbol/i)).toBeDisabled();
    expect(screen.getByLabelText(/benchmark source/i)).toBeDisabled();
  });
});
```

### Step 2: Run, verify fail

```
cd dashboard && npx vitest run src/components/ExperimentScopeFields.test.tsx
```

### Step 3: Implement

```typescript
// dashboard/src/components/ExperimentScopeFields.tsx
import { useEffect } from "react";

interface Props {
  startDate: string;            // ISO YYYY-MM-DD or ""
  endDate: string;
  initialCash: number;
  costProfile: string;
  benchmarkSymbol: string;      // "" when unset
  benchmarkSource: string;      // "" when unset
  onChange: (next: {
    date_range_start: string;
    date_range_end: string;
    initial_cash: number;
    cost_profile: string;
    benchmark_symbol: string | null;
    benchmark_source: string | null;
  }) => void;
  onValidityChange?: (valid: boolean) => void;
  disabled?: boolean;
}

function isValid(p: Props): boolean {
  if (!p.startDate || !p.endDate) return false;
  if (p.endDate <= p.startDate) return false;
  if (!(p.initialCash > 0)) return false;
  if (!p.costProfile.trim()) return false;
  const bsEmpty = !p.benchmarkSymbol.trim();
  const bSrcEmpty = !p.benchmarkSource.trim();
  if (bsEmpty !== bSrcEmpty) return false;     // pair violation
  return true;
}

export function ExperimentScopeFields(props: Props) {
  const {
    startDate, endDate, initialCash, costProfile,
    benchmarkSymbol, benchmarkSource,
    onChange, onValidityChange, disabled,
  } = props;

  // Notify parent of validity whenever inputs change.
  useEffect(() => {
    onValidityChange?.(isValid(props));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startDate, endDate, initialCash, costProfile, benchmarkSymbol, benchmarkSource]);

  function emit(overrides: Partial<Props>) {
    const merged = { ...props, ...overrides };
    onChange({
      date_range_start: merged.startDate,
      date_range_end: merged.endDate,
      initial_cash: merged.initialCash,
      cost_profile: merged.costProfile,
      benchmark_symbol: merged.benchmarkSymbol.trim() || null,
      benchmark_source: merged.benchmarkSource.trim() || null,
    });
  }

  const input =
    "bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full";

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-4 gap-2">
        <div className="space-y-1">
          <label htmlFor="sf-start" className="text-sm text-gray-300">
            Start date <span className="text-red-400">*</span>
          </label>
          <input
            id="sf-start" type="date" value={startDate} disabled={disabled}
            onChange={(e) => emit({ startDate: e.target.value })}
            className={input}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="sf-end" className="text-sm text-gray-300">
            End date <span className="text-red-400">*</span>
          </label>
          <input
            id="sf-end" type="date" value={endDate} disabled={disabled}
            onChange={(e) => emit({ endDate: e.target.value })}
            className={input}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="sf-cash" className="text-sm text-gray-300">
            Initial cash <span className="text-red-400">*</span>
          </label>
          <input
            id="sf-cash" type="number" min={1} value={initialCash} disabled={disabled}
            onChange={(e) => emit({ initialCash: parseFloat(e.target.value || "0") })}
            className={input}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="sf-cost" className="text-sm text-gray-300">
            Cost profile <span className="text-red-400">*</span>
          </label>
          <input
            id="sf-cost" type="text" value={costProfile} disabled={disabled}
            onChange={(e) => emit({ costProfile: e.target.value })}
            className={input}
          />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <label htmlFor="sf-bsym" className="text-sm text-gray-300">
            Benchmark symbol (optional, paired)
          </label>
          <input
            id="sf-bsym" type="text" value={benchmarkSymbol} disabled={disabled}
            placeholder="e.g. SPY"
            onChange={(e) => emit({ benchmarkSymbol: e.target.value })}
            className={input}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="sf-bsrc" className="text-sm text-gray-300">
            Benchmark source (optional, paired)
          </label>
          <input
            id="sf-bsrc" type="text" value={benchmarkSource} disabled={disabled}
            placeholder="e.g. polygon"
            onChange={(e) => emit({ benchmarkSource: e.target.value })}
            className={input}
          />
        </div>
      </div>
    </div>
  );
}
```

### Step 4: Tests pass

```
cd dashboard && npx vitest run src/components/ExperimentScopeFields.test.tsx
```

Expected: 6 passed.

### Step 5: Commit

```
git add dashboard/src/components/ExperimentScopeFields.tsx \
        dashboard/src/components/ExperimentScopeFields.test.tsx
git commit -m "feat(dashboard): ExperimentScopeFields — date range, cash, cost, benchmark inputs

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: `NewSessionModal` integration

**Files:**
- Modify: `dashboard/src/components/NewSessionModal.tsx`
- Modify: `dashboard/src/components/NewSessionModal.test.tsx`

### Step 1: Update tests

Update the existing 3 tests to use the new fields. The "successful submit" test gets the most change:

```typescript
// dashboard/src/components/NewSessionModal.test.tsx (update existing test)
it("successful submit calls API with all session-scope fields", async () => {
  vi.useFakeTimers();
  const onCreated = vi.fn();
  render(wrap(<NewSessionModal open={true} onClose={() => {}} onCreated={onCreated} />));
  fireEvent.change(screen.getByLabelText(/^name/i), { target: { value: "Smoke" } });
  fireEvent.change(screen.getByLabelText(/hypothesis/i), { target: { value: "test" } });
  fireEvent.change(screen.getByLabelText(/algorithm/i), { target: { value: "algo-a" } });
  // NEW — fill scope fields
  fireEvent.change(screen.getByLabelText(/start date/i), { target: { value: "2023-01-01" } });
  fireEvent.change(screen.getByLabelText(/end date/i), { target: { value: "2024-12-31" } });
  // initial_cash + cost_profile default
  // benchmark left empty (null pair)
  fireEvent.change(screen.getByLabelText(/^base config/i), {
    target: { value: '{"vol":0.1}' },
  });
  fireEvent.change(screen.getByLabelText(/^parameter space/i), {
    target: { value: '{"x":[1]}' },
  });
  fireEvent.change(screen.getByLabelText(/^criteria/i), {
    target: { value: '{"min_sharpe":1}' },
  });
  act(() => vi.advanceTimersByTime(250));
  vi.useRealTimers();
  fireEvent.click(screen.getByRole("button", { name: /create session/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith(42));
  const { api } = await import("../api/client");
  const body = (api.createResearchSession as any).mock.calls[0][0];
  expect(body.algorithm_id).toBe("algo-a");
  expect(body.date_range_start).toBe("2023-01-01");
  expect(body.date_range_end).toBe("2024-12-31");
  expect(body.initial_cash).toBe(10000);     // default
  expect(body.cost_profile).toBe("default");  // default
  expect(body.benchmark_symbol).toBeNull();
  expect(body.benchmark_source).toBeNull();
});
```

Update the mock'd `createResearchSession` return value at the top of the file to include the 6 new fields (otherwise type-checks fail).

### Step 2: Implement modal changes

Edit `NewSessionModal.tsx`. Add scope state + render `<ExperimentScopeFields>` between Notes and `<ExperimentConfigEditor>`:

```typescript
import { ExperimentScopeFields } from "./ExperimentScopeFields";

export function NewSessionModal({ open, onClose, onCreated }: Props) {
  // ... existing state ...

  // NEW — scope state
  const [scope, setScope] = useState({
    date_range_start: "",
    date_range_end: "",
    initial_cash: 10000,
    cost_profile: "default",
    benchmark_symbol: null as string | null,
    benchmark_source: null as string | null,
  });
  const [scopeValid, setScopeValid] = useState(false);

  // ... existing setup ...

  const canSubmit =
    name.trim().length > 0 &&
    algorithmId !== "" &&
    hypothesis.trim().length > 0 &&
    config.base_config !== null &&
    config.parameter_space !== null &&
    config.pre_registered_criteria !== null &&
    configValid &&
    scopeValid &&                    // NEW
    !mut.isPending;

  const handleSubmit = async () => {
    setSubmitError(null);
    try {
      const session = await mut.mutateAsync({
        name: name.trim(),
        hypothesis: hypothesis.trim(),
        algorithm_id: algorithmId,
        base_config: config.base_config ?? {},
        parameter_space: config.parameter_space ?? {},
        pre_registered_criteria: config.pre_registered_criteria ?? {},
        notes: notes.trim(),
        ...scope,                     // NEW — spreads the 6 fields
      });
      onCreated(session.id);
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : "Failed to create session");
    }
  };

  // ... in JSX, add a new section between Notes and <ExperimentConfigEditor>:
  return (
    // ...
    <div className="overflow-auto px-6 py-4 space-y-4">
      {/* Name + Algorithm grid (existing) */}
      {/* Hypothesis (existing) */}
      {/* Notes (existing) */}
      <ExperimentScopeFields
        startDate={scope.date_range_start}
        endDate={scope.date_range_end}
        initialCash={scope.initial_cash}
        costProfile={scope.cost_profile}
        benchmarkSymbol={scope.benchmark_symbol ?? ""}
        benchmarkSource={scope.benchmark_source ?? ""}
        onChange={setScope}
        onValidityChange={setScopeValid}
      />
      {/* ExperimentConfigEditor (existing) */}
    </div>
    // ...
  );
}
```

### Step 3: Tests pass

```
cd dashboard && npx vitest run src/components/NewSessionModal.test.tsx
```

### Step 4: Commit

```
git add dashboard/src/components/NewSessionModal.tsx \
        dashboard/src/components/NewSessionModal.test.tsx
git commit -m "feat(dashboard): NewSessionModal adds ExperimentScopeFields row

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: `ResearchSessionSummary` inline scope line

**Files:**
- Modify: `dashboard/src/components/ResearchSessionSummary.tsx`
- Modify: `dashboard/src/pages/ResearchSessionDetail.test.tsx`

### Step 1: Update test fixture + add assertion

In `ResearchSessionDetail.test.tsx`, find the `SESSION` fixture and add the 6 new fields:

```typescript
const SESSION = {
  id: 7, name: "Smoke", hypothesis: "ws works", status: "open" as const,
  notes: "", created_at: "2026-05-31", completed_at: null,
  algorithm_id: "test-algo-a",
  base_config: { vol: 0.10 },
  parameter_space: { x: [1, 2] },
  pre_registered_criteria: { min_sharpe: 1 },
  n_runs: 0,
  // NEW
  date_range_start: "2023-01-01",
  date_range_end: "2024-12-31",
  initial_cash: 25000,
  cost_profile: "default",
  benchmark_symbol: "SPY",
  benchmark_source: "polygon",
};
```

Add a new test:

```typescript
it("summary card renders inline scope line with dates, cash, cost, benchmark", async () => {
  const { api } = await import("../api/client");
  (api.getResearchSession as any).mockResolvedValue(SESSION);
  (api.listResearchJobs as any).mockResolvedValue([]);
  render(wrap(<ResearchSessionDetail />));
  await waitFor(() => {
    expect(screen.getByText(/2023-01-01.*2024-12-31/)).toBeInTheDocument();
    expect(screen.getByText(/\$25,000/)).toBeInTheDocument();
    expect(screen.getByText(/cost: default/i)).toBeInTheDocument();
    expect(screen.getByText(/bench: SPY \(polygon\)/i)).toBeInTheDocument();
  });
});
```

### Step 2: Update `ResearchSessionSummary.tsx`

Find the section just below the algorithm chip + status badges (before the collapsible Hypothesis). Add:

```tsx
{/* Inline scope line — read-only, monospace */}
<div className="text-xs text-gray-400 font-mono flex items-center gap-2 flex-wrap mt-1">
  <span>{session.date_range_start} → {session.date_range_end}</span>
  <span className="text-gray-600">·</span>
  <span>${session.initial_cash.toLocaleString()}</span>
  <span className="text-gray-600">·</span>
  <span>cost: {session.cost_profile}</span>
  {session.benchmark_symbol && (
    <>
      <span className="text-gray-600">·</span>
      <span>bench: {session.benchmark_symbol} ({session.benchmark_source})</span>
    </>
  )}
</div>
```

### Step 3: Tests pass

```
cd dashboard && npx vitest run src/pages/ResearchSessionDetail.test.tsx
```

### Step 4: Commit

```
git add dashboard/src/components/ResearchSessionSummary.tsx \
        dashboard/src/pages/ResearchSessionDetail.test.tsx
git commit -m "feat(dashboard): SessionSummary shows inline scope line

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: `Research` list Date range column

**Files:**
- Modify: `dashboard/src/pages/Research.tsx`
- Modify: `dashboard/src/pages/Research.test.tsx`

### Step 1: Update tests

Add the 6 new fields to each existing session fixture. Add a new test:

```typescript
it("renders Date range column", async () => {
  const { api } = await import("../api/client");
  (api.listResearchSessions as any).mockResolvedValue([
    { id: 1, name: "S1", hypothesis: "H1", status: "open", notes: "",
      created_at: "2026-05-31", completed_at: null,
      algorithm_id: "algo-abc", base_config: {},
      parameter_space: {}, pre_registered_criteria: {}, n_runs: 0,
      date_range_start: "2023-01-01",
      date_range_end: "2024-12-31",
      initial_cash: 10000,
      cost_profile: "default",
      benchmark_symbol: null,
      benchmark_source: null,
    },
  ]);
  render(wrap(<Research />));
  await waitFor(() => {
    expect(screen.getByText(/2023-01-01.*2024-12-31/)).toBeInTheDocument();
  });
});
```

### Step 2: Add the Date range column

In `Research.tsx`, find the `<thead>` row. Insert between Algorithm and Status:

```tsx
<thead className="bg-gray-950 text-gray-400 text-xs">
  <tr>
    <th className="px-4 py-2 text-left">Name</th>
    <th className="px-4 py-2 text-left">Algorithm</th>
    <th className="px-4 py-2 text-left">Date range</th>      {/* NEW */}
    <th className="px-4 py-2 text-left">Status</th>
    <th className="px-4 py-2 text-left">Hypothesis</th>
    <th className="px-4 py-2 text-right">Runs</th>
    <th className="px-4 py-2 text-left">Created</th>
  </tr>
</thead>
```

And the row template:

```tsx
<td className="px-4 py-2 font-medium">{s.name}</td>
<td className="px-4 py-2 text-gray-400 font-mono text-xs">{s.algorithm_id}</td>
<td className="px-4 py-2 text-gray-400 font-mono text-xs whitespace-nowrap">
  {s.date_range_start} → {s.date_range_end}                  {/* NEW */}
</td>
<td className="px-4 py-2">{s.status}</td>
```

### Step 3: Tests pass

```
cd dashboard && npx vitest run src/pages/Research.test.tsx
```

### Step 4: Commit

```
git add dashboard/src/pages/Research.tsx dashboard/src/pages/Research.test.tsx
git commit -m "feat(dashboard): Research list adds Date range column

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Typecheck + build + coord restart smoke

**Files:** (no edits)

### Step 1: Full typecheck

```
cd /home/jkern/dev/quilt-trader/dashboard && npx tsc --noEmit 2>&1 | head -20
```

Expected: clean.

### Step 2: Full vitest

```
cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run 2>&1 | tail -10
```

The new + updated session/sweep/summary/list tests pass. The 6 pre-existing failures on `main` (in Accounts/AccountDetail/DeploymentDetail tests, confirmed in prior plans) remain.

### Step 3: Build dashboard

```
cd /home/jkern/dev/quilt-trader && quilt dashboard build 2>&1 | tail -4
```

Expected: clean build.

### Step 4: Restart coord

```
quilt coord restart
```

Expected: `coord started (pid=..., port=...)`. If startup fails, check `~/.quilt/log/coord.log`.

### Step 5: API smoke

```
curl -sf http://127.0.0.1:8000/api/research/sessions | python3 -m json.tool | head -5
```

Expected: `[]` (sessions wiped by the migration).

### Step 6: No commit.

---

## Task 14: End-to-end manual smoke

Manual hand-walk. No automated checks.

- [ ] **Step 1:** Hard-refresh dashboard. Sidebar still shows Research.

- [ ] **Step 2:** Research → empty state with "Create your first session" CTA.

- [ ] **Step 3:** Click. Modal shows Name + Algorithm (top row), Hypothesis, Notes, then a NEW ExperimentScopeFields row (Start / End / Initial cash / Cost profile + Benchmark pair below), then ExperimentConfigEditor.

- [ ] **Step 4:** Fill in: name, algorithm, hypothesis, dates `2023-01-01` → `2024-12-31`, leave cash at 10000 and cost_profile "default", leave benchmark empty. base_config `{}`, parameter_space `{"lookback":[20,50]}`, criteria `{"min_sharpe":0.5}`. Submit.

- [ ] **Step 5:** Lands on session detail. Algorithm chip, then a monospace line: `2023-01-01 → 2024-12-31 · $10,000 · cost: default`. No benchmark segment (empty pair).

- [ ] **Step 6:** Click New Sweep → modal is still just 4 fields. `grid` / `5` / `1` / `0`. Start.

- [ ] **Step 7:** Job appears, transitions through states via WS push. After completion, run links activate and link to `/backtests/runs/{id}`.

- [ ] **Step 8:** Open the run detail — `algorithm_id`, `date_range_start`, `date_range_end`, `initial_cash` are all populated correctly. **No NOT NULL violations.**

- [ ] **Step 9:** CLI sanity: `quilt research session show <id>` displays the Date range / Initial cash / Cost profile lines.

- [ ] **Step 10:** CLI negative: `quilt research session create --name X --hypothesis Y --algorithm-id Z --base-config '{}' --parameter-space '{}' --criteria '{}'` (no `--start` / `--end`) → exits with "Missing option '--start'".

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| 6 new columns + migration + legacy session wipe | Task 1 |
| create_session() signature + 3 new tests | Task 2 |
| CreateSessionRequest validators + SessionResponse + endpoint validation | Task 3 |
| _run_one_backtest / _run_oos_backtest explicit-kwarg refactor | Task 4 |
| run_sweep / run_walk_forward signature growth | Task 4 |
| _pick_best_train_config strip-list shrink to {_fold_index, _oos} | Task 4 |
| ResearchJobManager._dispatch_* plumbing | Task 4 |
| sweep_endpoint / walk_forward_endpoint clean payload (removes df006f8 hack) | Task 4 |
| Test fixture fan-out for OptimizationSession constructor sites | Task 5 |
| CLI session create grows 6 flags + benchmark pair validator | Task 6 |
| CLI session show display extension | Task 7 |
| Frontend API types | Task 8 |
| ExperimentScopeFields component + 6 tests | Task 9 |
| NewSessionModal integration | Task 10 |
| ResearchSessionSummary inline scope line | Task 11 |
| Research list Date range column | Task 12 |
| Typecheck + build + restart | Task 13 |
| E2E manual smoke | Task 14 |

**Placeholder scan:** none.

**Type consistency:**
- `date_range_start: date` model, `date_range_start: _date` Pydantic, `date_range_start: string` (ISO) TypeScript — consistent boundaries (server holds dates, wire is ISO strings)
- `initial_cash` defaults: `10_000.0` everywhere (DB server_default, Pydantic, service-layer, CLI flag default, frontend state initial value) — consistent
- `cost_profile` defaults: `"default"` everywhere — consistent
- `benchmark_symbol` / `benchmark_source` — paired-or-both-null enforced at API validator, CLI handler, frontend `isValid` — three layers of the same rule
- `_run_one_backtest` and `_run_oos_backtest` kwargs match between sweep.py, walk_forward.py, dispatcher, tests

**One scope note:** `_run_oos_backtest` takes `oos_start`/`oos_end` as its date kwargs (the per-fold OOS window), distinct from `date_range_start`/`_end` on the session (which bounds the WF universe). Task 4 Step 3 explicitly calls this out — both names are intentional, not a typo.
