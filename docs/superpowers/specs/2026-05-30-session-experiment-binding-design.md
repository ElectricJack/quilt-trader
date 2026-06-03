# Session-Experiment Binding — Design

## Problem

`OptimizationSession` was designed as the pre-registration of a research experiment ("I hypothesize that [strategy X], parameterized over [these ranges], will hit [these criteria]"). But the current schema only owns `hypothesis`, `parameter_space`, `pre_registered_criteria`, `notes` — **not** the algorithm being tested or the non-swept settings (`base_config`).

Today, every sweep / walk-forward submission re-specifies algorithm + base_config per job. Nothing stops sweep #1 of a session from running on algorithm A and sweep #2 from running on algorithm B, which defeats the pre-registration framing. The user noticed this when the dashboard's "New Sweep" modal asked them to pick an algorithm from a dropdown — they expected the algorithm to be pinned by the session they had just opened.

This is a backend design hole the CLI inherited and the dashboard faithfully exposed. This spec closes it.

## Goals

- **Session pins the experiment.** `OptimizationSession` gains required `algorithm_id` and `base_config` columns. Combined with the existing `parameter_space` and `pre_registered_criteria`, a session is now a complete pre-registered experiment definition.
- **Sweep / walk-forward submissions become execution-only.** The request payload drops `manifest_path`, `algorithm_id`, `base_config`, and `parameter_space` — all read from the session row server-side. Sweep keeps `search`/`max_trials`/`parallelism`/`seed`; walk-forward keeps `train_years`/`test_years`/`step_months`/`objective`/`parallelism`.
- **CLI, API, dashboard all align with the new model.** No splits where one surface forces algorithm-per-sweep and another enforces algorithm-per-session.
- **Clean upgrade path** to a future manifest-schema-derived structured form by extracting `<ExperimentConfigEditor>` as the swap-in target.

## Non-goals

- The manifest-schema-derived structured form itself (still backlog; see "Deferred" at the end).
- A `session edit` capability — sessions remain immutable pre-registrations.
- Migrating existing CLI scripts / external automation (only one user; no automation depends on the old shape).

## Decisions locked during brainstorm

| Question | Choice |
|---|---|
| Override policy on sweep | **A — strict.** Sweep request cannot override algorithm / base_config / parameter_space. |
| Legacy sessions in the live DB | **B — delete the 4 rows.** Schema is NOT NULL. The 66 historical `BacktestRun` rows linked to them keep their data but their `optimization_session_id` is set to NULL. |
| `base_config` requirement | **A — required at session create, defaults to `{}`.** Pre-registration is complete up-front. |
| `BacktestRun.config_overrides` shape | Unchanged. Still `{**base_config, **trial}` merged at sweep time. The session row is the source of truth; the run row is a denormalization for engine/finalizer/report. |

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│  Before (broken pre-registration)                              │
├────────────────────────────────────────────────────────────────┤
│  OptimizationSession                                            │
│    name, hypothesis, parameter_space, criteria, notes           │
│                                                                  │
│  Per-sweep payload                                              │
│    manifest_path / algorithm_id, base_config, parameter_space,  │
│    search, max_trials, parallelism, seed                        │
│                                                                  │
│  → Nothing prevents Sweep #1 (algo A) and Sweep #2 (algo B)    │
│    in the same session.                                         │
└────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│  After (pinned experiment)                                     │
├────────────────────────────────────────────────────────────────┤
│  OptimizationSession                                            │
│    name, hypothesis, algorithm_id (NEW), base_config (NEW),     │
│    parameter_space, criteria, notes                             │
│      ↓                                                           │
│  Per-sweep payload (execution-only)                            │
│    search, max_trials, parallelism, seed                        │
│                                                                  │
│  → Sweep dispatch reads algorithm + base_config + parameter_   │
│    space from session.                                          │
│  → Same algorithm/config for every sweep in a session, by      │
│    construction.                                                │
└────────────────────────────────────────────────────────────────┘
```

## Data model + migration

### Schema additions to `optimization_sessions`

```python
class OptimizationSession(Base):
    __tablename__ = "optimization_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    hypothesis: Mapped[str] = mapped_column(Text)
    parameter_space: Mapped[dict] = mapped_column(JSON)
    pre_registered_criteria: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="open")
    notes: Mapped[str] = mapped_column(Text, default="")

    # NEW
    algorithm_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("algorithms.id", ondelete="RESTRICT"),
        nullable=False,
    )
    base_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    completed_at: Mapped[datetime | None]

    # existing relationship
    runs: Mapped[list["BacktestRun"]] = relationship(back_populates="optimization_session")
```

**Why `ondelete="RESTRICT"`:** an experiment session pins the strategy under test. If you uninstall the algorithm, the session loses the artifact it's testing. RESTRICT forces the user to either reinstall or wipe the session first, rather than silently breaking the experiment.

### Alembic migration

```python
def upgrade() -> None:
    # 1. Set optimization_session_id=NULL on all BacktestRun rows so
    #    cascade rules don't take historical run data with the sessions.
    op.execute(
        "UPDATE backtest_runs SET optimization_session_id = NULL "
        "WHERE optimization_session_id IS NOT NULL"
    )
    # 2. Drop any ResearchJob rows that reference the legacy sessions.
    #    The live DB exploration found zero such rows (legacy sessions
    #    predate the async-job model), but doing this explicitly avoids
    #    a FK constraint failure on step 3 if any rows do exist.
    op.execute(
        "DELETE FROM research_jobs WHERE session_id IN "
        "(SELECT id FROM optimization_sessions)"
    )
    # 3. Wipe the 4 legacy sessions.
    op.execute("DELETE FROM optimization_sessions")
    # 4. Add columns NOT NULL (safe — table is now empty).
    op.add_column(
        "optimization_sessions",
        sa.Column("algorithm_id", sa.String(64), nullable=False),
    )
    op.create_foreign_key(
        "fk_session_algorithm",
        "optimization_sessions", "algorithms",
        ["algorithm_id"], ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "optimization_sessions",
        sa.Column("base_config", sa.JSON(), nullable=False, server_default="{}"),
    )

def downgrade() -> None:
    op.drop_constraint("fk_session_algorithm", "optimization_sessions")
    op.drop_column("optimization_sessions", "algorithm_id")
    op.drop_column("optimization_sessions", "base_config")
```

The 66 `BacktestRun` rows previously linked to legacy sessions become orphaned (`optimization_session_id=NULL`) but remain fully browseable at `/backtests/runs/{id}` with all metrics intact. Only the Research sessions list loses those 4 rows.

## Backend API + service changes

### `CreateSessionRequest` grows two fields

```python
# coordinator/api/routes/research.py
class CreateSessionRequest(BaseModel):
    name: str
    hypothesis: str
    algorithm_id: str                                  # NEW — required
    base_config: dict = Field(default_factory=dict)    # NEW — required, defaults to {}
    parameter_space: dict
    pre_registered_criteria: dict
    notes: str = ""
```

`SessionResponse` gains the same two fields so the dashboard renders them on the session summary.

The create handler validates `algorithm_id`:
- Resolves to a real `Algorithm` row → 404 if unknown
- The row has non-null `source_path` → 400 if orphaned / not fully installed

### `create_session()` service signature

```python
# coordinator/services/validation/optimization_session.py
def create_session(
    db: Session,
    *,
    name: str,
    hypothesis: str,
    algorithm_id: str,                # NEW
    base_config: dict[str, Any],      # NEW
    parameter_space: dict[str, Any],
    pre_registered_criteria: dict[str, Any],
    notes: str = "",
) -> OptimizationSession:
```

No defaults on the new args at the service layer; the API/CLI/test fixtures are responsible for providing them. The default-`{}` for `base_config` lives only at the Pydantic layer.

### `SweepRequest` and `WalkForwardRequest` shrink

```python
class SweepRequest(BaseModel):
    # algorithm + base_config + parameter_space come from the session — removed.
    search: Literal["grid", "random", "latin", "tpe"] = "grid"
    max_trials: int = 50
    parallelism: int = 1
    seed: int = 0
```

```python
class WalkForwardRequest(BaseModel):
    train_years: float = 4.0
    test_years: float = 1.0
    step_months: float = 6.0
    objective: Literal["sharpe", "calmar", "sortino"] = "sharpe"
    parallelism: int = 1
```

The XOR validator on `(manifest_path, algorithm_id)` added in commit `1704d4a` is deleted along with those fields.

### Sweep + walk-forward handlers read from session

```python
# inside sweep_endpoint
session = await db.get(OptimizationSession, session_id)
if session is None:
    raise HTTPException(404, f"unknown session: {session_id}")
manifest_path = await _resolve_manifest_path_from_algorithm_id(db, session.algorithm_id)
request_payload = {
    "manifest_path": manifest_path,
    "base_config": session.base_config,
    "parameter_space": session.parameter_space,
    "search": req.search,
    "max_trials": req.max_trials,
    "parallelism": req.parallelism,
    "seed": req.seed,
}
job_id = await container.research_job_manager.create_sweep_job(
    session_id=session_id, request_payload=request_payload,
)
```

The previous `_resolve_manifest_path(db, manifest_path=..., algorithm_id=...)` helper from commit `1704d4a` simplifies to take just `algorithm_id` (single resolution path). Symmetric change for walk-forward.

### What's NOT changing on the backend

- `ResearchJob` model — same columns; `request_payload` shape stays compatible (`manifest_path`, `base_config`, `parameter_space` still present, just sourced server-side from session).
- `BacktestRun.config_overrides` — full merged shape preserved (`{**base_config, **trial}`).
- `validation/sweep.py` and `validation/walk_forward.py` — callable signatures stable. The manager builds the same `request_payload` shape that `_dispatch_sweep` and `_dispatch_walk_forward` already expect; only the source of `base_config`/`parameter_space` changed (session row vs request body).
- The 18 integration invariants in `2026-05-28-backtest-and-validation-lab-integration.md`.
- The `on_job_update` WS broadcast.

## CLI changes

### `quilt research session create` grows two flags

```
quilt research session create
    --name SMOKE-2026-06-01
    --hypothesis "Vol-targeted SMA crossover outperforms buy-and-hold ..."
    --algorithm-id <algorithm-id>             # NEW (required)
    --base-config '{"vol_target": 0.10}'      # NEW (required; JSON, .json, .yaml)
    --parameter-space '{"lookback_days": [20, 50, 100]}'
    --criteria '{"min_sharpe": 1.0}'
    --notes "..."
```

`--base-config` accepts inline JSON, a `.json`/`.yaml` file path, or just `'{}'`. Reuses the existing `_parse_json_or_yaml_or_file()` helper.

### `quilt research sweep` shrinks

```
# Before
quilt research sweep --session-id 7
    --manifest /path/to/quilt.yaml          # ← removed
    --base-config '{"vol_target": 0.10}'    # ← removed
    --parameter-space '{...}'               # ← removed
    --search grid --max-trials 50 --parallelism 1 --seed 0

# After
quilt research sweep --session-id 7
    --search grid --max-trials 50 --parallelism 1 --seed 0
```

### `quilt research walk-forward` shrinks similarly

```
quilt research walk-forward --session-id 7
    --train-years 4.0 --test-years 1.0 --step-months 6.0
    --objective sharpe --parallelism 1
```

Drops `--manifest`, `--base-config`, `--parameter-space`.

### `quilt research session show <id>` displays bound algorithm

JSON output already includes `algorithm_id` and `base_config` via the API change. Text-format output gains:

```
Algorithm:    my-crypto-tsmom-algo (algo-abc123def)
Base config:  {"vol_target": 0.10, "rebalance_frequency": "1day"}
```

### Backward compatibility — explicit

Hard removal of `--manifest`, `--base-config`, `--parameter-space` from sweep / walk-forward. Old invocations fail at Click's "unknown flag." The user is the only operator; there are no automation pipelines on the old shape.

## Dashboard changes

### `NewSessionModal` grows

| Field | Type | Order |
|---|---|---|
| Name | text | 1 |
| **Algorithm** | dropdown from `useAlgorithms()` | **2** (new) |
| Hypothesis | textarea | 3 |
| Notes (optional) | textarea | 4 |
| **`<ExperimentConfigEditor>`** | wraps 3 `<JsonTextField>`s in 3-column grid | **5** (new) |

`<ExperimentConfigEditor>` is the new wrapper described below. The 3-column grid (base_config | parameter_space | criteria) makes the "for each config field, fix it or sweep it" mental model visible from day one — even though the editor today is three JSON blobs.

Submit disabled until name + algorithm + hypothesis + all three JSON fields parse. `base_config` defaults to `{}` which is a valid parse, so the field starts valid.

Algorithm is **immutable after session creation** — the dropdown only appears in this modal, never in any edit context (which doesn't exist by design).

### `<ExperimentConfigEditor>` — new wrapper component

```typescript
interface Props {
  baseConfig: Record<string, unknown> | null;
  parameterSpace: Record<string, unknown> | null;
  criteria: Record<string, unknown> | null;
  onChange: (next: {
    base_config: Record<string, unknown> | null;
    parameter_space: Record<string, unknown> | null;
    pre_registered_criteria: Record<string, unknown> | null;
  }) => void;
  onValidityChange: (allValid: boolean) => void;
  disabled?: boolean;
}
```

Today renders three `<JsonTextField>`s side-by-side. Future structured-form spec replaces ONLY this component's internals with per-field rows derived from the selected algorithm's manifest `config_schema`. The session modal, the hooks, the API payload, the backend — all unchanged.

### `NewSweepModal` shrinks to 4 fields

| Field | Type |
|---|---|
| Search | dropdown: grid / random / latin / tpe |
| Max trials | number, default 50 |
| Parallelism | number, default 1 |
| Seed (optional) | number |

Removed: algorithm dropdown, base_config, parameter_space.

### `ResearchSessionSummary` grows

- **Algorithm chip + link** below the name header, showing `algorithm_name (algorithm_id)`. Click → `/algorithms/{id}`.
- **`<ExperimentConfigEditor disabled>`** replaces the existing two read-only JsonTextFields (parameter_space + criteria). All three configs displayed as read-only JSON side-by-side.

### `Research` (sessions list) gains an algorithm column

Insert **Algorithm** column between Name and Status, showing the algorithm row's `name` field (looked up client-side from the existing `useAlgorithms()` cache; fallback to `algorithm_id` if not loaded yet).

### What's NOT changing in the UI

- `JsonTextField` itself (just reused inside the editor wrapper).
- `ResearchJobRow` (job rows still show status / progress / runs / cancel).
- Live WS updates.
- Sidebar nav, routes, page structure.
- Empty states.

## Testing strategy

### Backend tests

**Fixture updates.** `_seed_session` / `_seed_session_sf` helpers across the test suite gain `algorithm_id` + `base_config` args, defaulting to a `_seed_algorithm()` fresh fixture that satisfies the FK:

```python
async def _seed_session_sf(db_session_factory, *, algorithm_id="test-algo-1", base_config=None):
    from coordinator.database.models import OptimizationSession
    async with db_session_factory() as s:
        row = OptimizationSession(
            name=f"smoke-{uuid.uuid4().hex[:6]}",
            hypothesis="seed",
            algorithm_id=algorithm_id,
            base_config=base_config or {},
            parameter_space={"x": [1]},
            pre_registered_criteria={"min_sharpe": 0.0},
        )
        s.add(row); await s.commit(); await s.refresh(row)
        return row.id
```

Tests that exercise sweep dispatch also call `_seed_algorithm()` so the FK resolves.

**New API tests** in `tests/coordinator/api/test_research_routes.py`:

- `test_create_session_requires_algorithm_id` — 422 when omitted
- `test_create_session_rejects_unknown_algorithm_id` — 404
- `test_create_session_rejects_algorithm_with_null_source_path` — 400
- `test_create_session_accepts_empty_base_config` — `{}` is valid
- `test_session_response_includes_algorithm_id_and_base_config` — round-trip
- `test_sweep_request_rejects_legacy_fields` — request with `manifest_path` / `algorithm_id` / `base_config` / `parameter_space` returns 422
- `test_sweep_uses_session_algorithm_and_base_config` — queue a sweep; verify `ResearchJob.request_payload` has `manifest_path` resolved from session and `base_config` copied from session
- Symmetric pair for walk-forward

**Migration smoke** — manual: snapshot `data/quilt_trader.db` → `alembic upgrade head` → verify the legacy 4 sessions are gone, BacktestRuns survive with `optimization_session_id=NULL`, new columns are NOT NULL with the FK in place. The codebase doesn't have a migration-testing harness today; the manual smoke is the right shape.

### Frontend tests

`NewSessionModal.test.tsx`: update 3 existing + add 1 for "submit body includes algorithm_id and base_config."

`NewSweepModal.test.tsx`: update 4 existing tests (algorithm dropdown gone, JSON fields gone, submit body shape changes); the algorithm-id and parameter_space assertions deleted.

`ResearchSessionDetail.test.tsx`: add "summary card renders algorithm name + base_config" and "sweep modal opens with only the 4 execution fields."

`ExperimentConfigEditor.test.tsx` (new — 5 tests):
- Renders three `<JsonTextField>` children with correct labels
- Passes through values to each field
- Emits combined `{base_config, parameter_space, pre_registered_criteria}` on any child change
- `disabled` prop propagates to all three children
- All three must parse for the parent's `onValidityChange(true)` to fire

`tests/sdk/cli/test_research_cli.py`: update CLI invocations to pass the new `--algorithm-id` and `--base-config` flags.

### Tests not being added

- Real production-data migration (manual smoke covers it; we don't have a snapshot harness).
- E2E browser test (existing pattern is manual smoke after deploy).
- The `ondelete="RESTRICT"` runtime behavior — relying on SQLAlchemy/SQLite FK semantics. If it breaks at runtime, it surfaces as a 400 on algorithm-uninstall with attached sessions.

## Configuration sequence at first install / upgrade

1. User pulls the branch.
2. `alembic upgrade head` runs (manually or as part of `quilt coord restart` if migrations auto-apply). The 4 legacy sessions are deleted; the 66 BacktestRuns lose their session linkage.
3. User restarts the coord.
4. User refreshes the dashboard. The Research sessions list is empty.
5. User clicks "New Session." Form now has an Algorithm dropdown and a Base config field (3-col grid).
6. User picks an installed algorithm, fills in hypothesis / base_config / parameter_space / criteria, submits.
7. Session detail page shows the algorithm chip + read-only base_config alongside the existing JSON fields.
8. User clicks "New Sweep." Form now has only 4 fields (search / max_trials / parallelism / seed). User submits; sweep runs against the session-bound algorithm + base_config + parameter_space.

## Deferred to v1.1+ (tracked in `docs/superpowers/backlog.md`)

The existing backlog entry "Manifest-derived structured form for JSON config fields" is updated to point at this spec's `<ExperimentConfigEditor>` extraction as the swap-in target. The follow-up work replaces the editor's three `<JsonTextField>` children with per-field rows derived from `algorithm.config_schema`, with a fix-vs-sweep toggle per field. Same API, same backend, much better UX. The fallback for algorithms without a populated `config_schema` is the JSON textarea version this spec ships.
