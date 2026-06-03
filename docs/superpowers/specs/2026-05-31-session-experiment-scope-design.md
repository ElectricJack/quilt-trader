# Session Experiment Scope — Design

## Problem

The previous spec ([`2026-05-30-session-experiment-binding-design.md`](2026-05-30-session-experiment-binding-design.md)) pinned each `OptimizationSession` to an algorithm + `base_config`. That closed the "algorithm-per-sweep" pre-registration hole, but on first real-world try the sweep failed:

```
sqlite3.IntegrityError: NOT NULL constraint failed: backtest_runs.date_range_start
```

`validation/sweep.py:_run_one_backtest` reads four "experiment scope" keys from `merged = {**base_config, **trial_config}` and writes them onto every `BacktestRun` row:

- `algorithm_id` → handled by the prior spec (server-side merge as a quick fix in commit `df006f8`)
- `start` / `end` → become `BacktestRun.date_range_start` / `_end` (NOT NULL)
- `initial_cash` → with a hard-coded default of `1000.0`

These are all **experiment-scope** values (which algo, what time period, how much capital). They should live on the session row for the same pre-registration reason that drove the prior spec. Burying them in `base_config` is the antipattern we already noticed and fixed for `algorithm_id`; doing it again for the date range and cash would be a knowing repeat.

Additionally, three related fields with corresponding NOT-NULL-ish columns on `BacktestRun` belong in the same pre-registration story:

- `cost_profile` — which transaction cost model to use
- `benchmark_symbol` / `benchmark_source` — what benchmark the reports compare against (optional pair)

This spec adds all six fields to the session and **completes the cleanup**: `_run_one_backtest()` is refactored to take session-scoped fields as explicit kwargs instead of mining them from `merged`, removing the leak pattern entirely.

## Goals

- **Session pins the full experiment scope.** `OptimizationSession` gains six new columns: `date_range_start`, `date_range_end`, `initial_cash`, `cost_profile`, `benchmark_symbol`, `benchmark_source`. Combined with the existing `algorithm_id` + `base_config` + `parameter_space` + `pre_registered_criteria`, a session is now a complete pre-registered experiment definition.
- **No more "scope leaks into algorithm config"** — `validation/sweep.py:_run_one_backtest` and walk-forward equivalent take session-scoped fields as named kwargs. `base_config` carries only algorithm hyperparameters.
- **Sweep dispatch (`sweep_endpoint`, `walk_forward_endpoint`) ships scope as top-level keys** in `request_payload`. The hack in commit `df006f8` (`base_config_with_algo = {...}`) is removed.
- **CLI, API, dashboard all align.**

## Non-goals

- Adding `symbols` or `data_source` to the session. The explorer found `_pick_best_train_config` strips these from base_config (suggesting they could be session-scoped), but they're not currently read by `_run_one_backtest`. Defer until something actually needs them.
- A schema-derived structured form for `cost_profile` (dropdown from a registry of cost profiles). Plain string input now; revisit if that registry exists.
- Editing sessions post-create. Sessions remain immutable pre-registrations.

## Decisions locked during brainstorm

| Question | Choice |
|---|---|
| Scope of session-scoped fields | **B (comprehensive)** — six fields: dates + cash + cost_profile + benchmark pair. |
| Date column type | `Date` (calendar date, ISO `YYYY-MM-DD`). Session UI uses date pickers; sweep wraps in datetime at BacktestRun insert. |
| `initial_cash` required-ness + default | Required with server-side default `10_000.0`. Caller may omit to accept default. |
| `cost_profile` required-ness + default | Required with server-side default `"default"`. |
| Benchmark fields | Both optional. Validator enforces both-set or both-null. |
| Legacy session | 1 row (the smoke session from yesterday). Wipe in migration; 102+ BacktestRuns survive orphaned. |
| Cleanup `_run_one_backtest` scope-mining | **Yes** — included in this spec (Section 2b). Removes the leak pattern entirely. |

## Architecture

```
Before this spec:
  OptimizationSession
    + algorithm_id + base_config + parameter_space + criteria

  sweep_endpoint:
    base_config_with_algo = {**sess.base_config, "algorithm_id": sess.algorithm_id}  ← hack
    request_payload["base_config"] = base_config_with_algo
                                     ^^^^^^^^^^^^^^^^^^^^^
                                     algorithm_id leaks here

  sweep.py:_run_one_backtest:
    merged = {**base_config, **config}     # ← experiment scope tangled with hyperparams
    BacktestRun(
      algorithm_id=merged.get("algorithm_id", ""),       # silent default
      date_range_start=_as_date(merged.get("start")),    # None → NOT NULL violation
      date_range_end=_as_date(merged.get("end")),
      initial_cash=float(merged.get("initial_cash", 1000.0)),
      cost_profile=??? (not set)                          # falls to BacktestRun column default
      ...
    )

After this spec:
  OptimizationSession
    + algorithm_id + base_config + parameter_space + criteria
    + date_range_start + date_range_end                  ← NEW required
    + initial_cash (default 10_000.0)                    ← NEW required-with-default
    + cost_profile (default "default")                   ← NEW required-with-default
    + benchmark_symbol + benchmark_source (optional pair) ← NEW

  sweep_endpoint:
    request_payload = {
      "manifest_path": manifest_path,
      "algorithm_id": sess.algorithm_id,                 # explicit, no leak
      "date_range_start": sess.date_range_start.isoformat(),
      "date_range_end": sess.date_range_end.isoformat(),
      "initial_cash": sess.initial_cash,
      "cost_profile": sess.cost_profile,
      "benchmark_symbol": sess.benchmark_symbol,
      "benchmark_source": sess.benchmark_source,
      "base_config": sess.base_config,                    # algo config ONLY
      "parameter_space": json.loads(sess.parameter_space),
      "search": ..., "max_trials": ..., ...
    }

  sweep.py:_run_one_backtest:
    # Session-scoped fields are explicit kwargs — no more merged.get(...)
    BacktestRun(
      algorithm_id=algorithm_id,           # from kwarg
      date_range_start=date_range_start,   # from kwarg
      ...
      config_overrides={**base_config, **config},   # still merged for the runner
    )
```

## Data model + migration

### Schema additions to `optimization_sessions`

```python
class OptimizationSession(Base):
    __tablename__ = "optimization_sessions"

    # ... existing columns ...
    algorithm_id: Mapped[str] = mapped_column(...)
    base_config: Mapped[dict] = mapped_column(...)
    parameter_space: Mapped[str] = mapped_column(...)
    pre_registered_criteria: Mapped[str] = mapped_column(...)

    # NEW (this spec)
    date_range_start: Mapped[date] = mapped_column(Date, nullable=False)
    date_range_end:   Mapped[date] = mapped_column(Date, nullable=False)
    initial_cash:     Mapped[float] = mapped_column(
        Float, nullable=False, server_default="10000.0",
    )
    cost_profile:     Mapped[str]  = mapped_column(
        String(32), nullable=False, server_default="default",
    )
    benchmark_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    benchmark_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
```

**`Date` not `DateTime`** — users pick calendar dates from HTML date inputs. The sweep wraps in datetime when constructing the BacktestRun row.

### Alembic migration

```python
def upgrade() -> None:
    # 1. NULL out optimization_session_id on BacktestRuns to preserve historical
    #    run data after we wipe the session.
    op.execute(
        "UPDATE backtest_runs SET optimization_session_id = NULL "
        "WHERE optimization_session_id IS NOT NULL"
    )
    # 2. Drop ResearchJobs that reference the 1 legacy session.
    op.execute(
        "DELETE FROM research_jobs WHERE session_id IN "
        "(SELECT id FROM optimization_sessions)"
    )
    # 3. Wipe the 1 legacy session (smoke-session-binding-2026-05-30).
    op.execute("DELETE FROM optimization_sessions")
    # 4. Add columns NOT NULL (safe — table is empty). Batch mode for SQLite.
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

## Backend API + service changes

### `CreateSessionRequest` + `SessionResponse` grow 6 fields

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
    benchmark_symbol: str | None = None                    # optional
    benchmark_source: str | None = None                    # optional

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

`SessionResponse` gains the same 6 fields.

### `create_session()` service signature

```python
def create_session(
    db, *,
    name, hypothesis,
    algorithm_id, base_config,
    parameter_space, pre_registered_criteria,
    notes="",
    # NEW
    date_range_start: date,
    date_range_end: date,
    initial_cash: float = 10_000.0,
    cost_profile: str = "default",
    benchmark_symbol: str | None = None,
    benchmark_source: str | None = None,
) -> OptimizationSession:
```

### Sweep + walk-forward endpoints — clean payload

Replace the algorithm_id hack from commit `df006f8` with explicit top-level keys:

```python
# inside sweep_endpoint, after fetching sess and resolving manifest_path:
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

Symmetric for walk-forward.

## Section 2b — `validation/sweep.py` + `walk_forward.py` signature refactor

### `_run_one_backtest()` takes session-scoped fields as explicit kwargs

```python
async def _run_one_backtest(
    db, runner_factory, *,
    session_id: int,
    # NEW — session-scoped, no merged-dict mining
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
    db.add(run_row); db.flush(); db.commit()
    run_id = run_row.id
    await runner_factory(run_id)
    db.refresh(run_row)
    return {"run_id": run_id, "config_hash": config_hash_str, "config": config}
```

Same change to walk_forward's `_run_one_oos` (or equivalent). The `merged.get("...", default)` pattern for these fields goes away. No more silent defaults to `1000.0`, empty `algorithm_id`, or `None` dates.

### `walk_forward.py:_pick_best_train_config` simplifies

Currently strips keys from base_config (`symbols`, `data_source`, `cost_profile`, `_fold_index`, `_oos`) before merging the best train trial into the OOS run. With session scope explicit, base_config is algorithm config only — the strip-list shrinks to just `{_fold_index, _oos}` (the internal markers used by the OOS dispatch).

### `run_sweep()` and `run_walk_forward()` grow signatures

The orchestrators each gain the 6 new kwargs and forward to `_run_one_backtest` / `_run_one_oos`.

### `ResearchJobManager._dispatch_sweep` + `_dispatch_walk_forward`

Map the new payload keys to kwargs:

```python
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
```

Symmetric for walk-forward.

### What's NOT changing

- `BacktestRun` model — no new columns; the existing `algorithm_id`, `date_range_start`, `date_range_end`, `initial_cash`, `cost_profile`, `benchmark_symbol`, `benchmark_source` columns all receive their values from the session via the new sweep kwargs
- `BacktestRunner` — it reads `cost_profile` / `benchmark_*` from BacktestRun columns (not from `config_overrides`), so no change there
- The 18 integration invariants
- `ResearchJob` model
- `JsonTextField`, `<ExperimentConfigEditor>`, `<ResearchJobRow>`, the WS subscription, the sidebar, the routes

## CLI changes

### `quilt research session create` grows 6 flags

```
quilt research session create
    --name SMOKE
    --hypothesis "..."
    --algorithm-id <id>
    --base-config '{"vol_target": 0.10}'
    --parameter-space '{"lookback": [20, 50]}'
    --criteria '{"min_sharpe": 0.5}'
    --start 2023-01-01                       # NEW — required
    --end 2024-12-31                         # NEW — required
    --initial-cash 10000                     # NEW — optional, default 10000
    --cost-profile default                   # NEW — optional, default "default"
    --benchmark-symbol SPY                   # NEW — optional (pair with --benchmark-source)
    --benchmark-source polygon               # NEW — optional (pair with --benchmark-symbol)
    --notes "..."
```

- `--start` / `--end` use `type=click.DateTime(formats=["%Y-%m-%d"])`; serialized back to ISO when the CLI client sends the API request.
- Benchmark pair: Click handler checks both-or-neither and exits with `error: --benchmark-symbol and --benchmark-source must both be set or both be omitted` on mismatch (mirrors the API validator with a cleaner CLI error message).

### `quilt research session show <id>` extends text output

Add new lines (benchmark conditional on being set):

```
Session #1: smoke-session-2026-06-01
Status:       open
Algorithm:    34b3eeec-9c7f-41bb-81ee-c348789571ec
Base config:  {"vol_target": 0.10}
Date range:   2023-01-01 → 2024-12-31              # NEW
Initial cash: $10,000.00                           # NEW
Cost profile: default                              # NEW
Benchmark:    SPY (polygon)                        # NEW — omitted if not set
Hypothesis:   ...
```

### Breaking change

`quilt research session create` now requires `--start` and `--end`. No automation depends on the old shape.

### What's NOT changing

- `quilt research sweep` and `walk-forward` CLI flags (still just execution params)
- Global `--json` / `--quiet`

## Dashboard changes

### `NewSessionModal` — add `<ExperimentScopeFields>` row

Layout:

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Name                                  Algorithm                          │
├──────────────────────────────────────────────────────────────────────────┤
│  Hypothesis                                                               │
├──────────────────────────────────────────────────────────────────────────┤
│  Start date   End date   Initial cash   Cost profile                     │  ← NEW
│  Benchmark symbol (optional)   Benchmark source (optional)               │  ← NEW (2nd row)
├──────────────────────────────────────────────────────────────────────────┤
│  Notes (optional)                                                         │
├──────────────────────────────────────────────────────────────────────────┤
│  ExperimentConfigEditor                                                   │
│  ┌────────────────┐ ┌──────────────────┐ ┌───────────────────────────┐  │
│  │  Base config   │ │  Parameter space │ │  Pre-registered criteria  │  │
│  └────────────────┘ └──────────────────┘ └───────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

### `<ExperimentScopeFields>` — new focused component

```typescript
interface Props {
  startDate: string;            // ISO YYYY-MM-DD
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
  onValidityChange: (valid: boolean) => void;
  disabled?: boolean;
}
```

Local validity rules: `startDate` non-empty, `endDate > startDate`, `initialCash > 0`, `costProfile` non-empty, benchmark pair both-empty or both-set.

Input types: `<input type="date">` for dates (HTML date pickers), `<input type="number" min={1}>` for cash, text inputs for cost_profile + benchmarks.

### `NewSessionModal` wires it in

```typescript
const [scope, setScope] = useState({
  date_range_start: "", date_range_end: "",
  initial_cash: 10000, cost_profile: "default",
  benchmark_symbol: null as string | null,
  benchmark_source: null as string | null,
});
const [scopeValid, setScopeValid] = useState(false);

// canSubmit = ... && scopeValid && configValid && ...;
// Submit body spreads `...scope`.
```

### `ResearchSessionSummary` — inline scope display

A read-only monospace line below the algorithm chip, before the collapsible Hypothesis:

```
algo: <chip>  ·  2023-01-01 → 2024-12-31  ·  $10,000  ·  cost: default  ·  bench: SPY (polygon)
```

Benchmark segment only renders when `benchmark_symbol` is set.

### `Research` (sessions list) — Date range column

Insert between Algorithm and Status columns:

```
Name | Algorithm | Date range | Status | Hypothesis | Runs | Created
```

Format: `YYYY-MM-DD → YYYY-MM-DD` in small monospace.

### Frontend type updates

```typescript
export interface ResearchSession {
  // ... existing ...
  date_range_start: string;          // ISO YYYY-MM-DD
  date_range_end: string;
  initial_cash: number;
  cost_profile: string;
  benchmark_symbol: string | null;
  benchmark_source: string | null;
}

export interface CreateSessionRequest {
  // ... existing ...
  date_range_start: string;
  date_range_end: string;
  initial_cash?: number;          // server default 10000
  cost_profile?: string;           // server default "default"
  benchmark_symbol?: string | null;
  benchmark_source?: string | null;
}
```

`CreateSweepRequest` unchanged.

### What's NOT changing in the UI

- `<ExperimentConfigEditor>` — still the 3 JSON fields
- `NewSweepModal` — still 4 execution fields
- `ResearchJobRow`, live WS updates, sidebar, routes

## Testing strategy

### Backend

**`tests/coordinator/services/validation/test_optimization_session.py`** — extend the 2 existing `create_session` tests + 2 new:
- `test_create_session_persists_date_range_and_cash`
- `test_create_session_persists_benchmark_pair` (both-set and both-null)

**`tests/coordinator/api/test_research_routes.py`** — new tests:
- `test_create_session_requires_date_range_start` → 422
- `test_create_session_requires_date_range_end` → 422
- `test_create_session_rejects_end_before_start` → 422
- `test_create_session_rejects_unpaired_benchmark` → 422
- `test_create_session_accepts_default_initial_cash_and_cost_profile` → response shows 10000 / "default"
- `test_session_response_includes_all_six_new_fields` → round-trip

Update existing sweep + walk-forward `request_payload` assertions: the new top-level keys (`algorithm_id`, `date_range_start`, etc.) instead of nested in base_config.

**`tests/coordinator/services/test_research_job_manager.py`** — `_seed_session_sf` fixture grows 6 kwargs (defaults sensible so unrelated tests don't touch). `_dispatch_sweep` / `_dispatch_walk_forward` call-arg tests assert the new kwargs forwarded to mocked `sweep_fn` / `walk_forward_fn`.

**`tests/coordinator/services/validation/test_sweep.py` + `test_walk_forward.py`** — bulk of Section 2b's test cost:
- Tests calling `_run_one_backtest` directly pass the 7 new kwargs explicitly. Tests that stuffed `start`/`end`/`algorithm_id` into base_config move those into named args.
- `run_sweep` orchestrator tests pass the new kwargs.
- `_pick_best_train_config` tests update — strip-list shrinks to `{_fold_index, _oos}`.
- ~12-15 tests touched.

**Fixture fan-out** — any test file constructing `OptimizationSession(...)` directly gains the 6 new fields (mostly with defaults: dates pinned to a small range like `2023-01-01 → 2023-12-31`, cash `10000`, cost_profile `"default"`, benchmarks null).

### Frontend

**`ExperimentScopeFields.test.tsx`** (new — 5 tests):
- Renders 6 inputs with correct labels
- `onChange` emits combined object on any field change
- `onValidityChange(false)` when start empty / end ≤ start / cash 0 / cost_profile empty / unpaired benchmark
- `onValidityChange(true)` when all required pass + benchmark pair invariant holds
- `disabled` propagates to all 6 inputs

**`NewSessionModal.test.tsx`** — update existing 3 tests:
- Populate the 6 new fields in "successful submit"
- Assert submit body includes the 6 keys
- Existing form validation tests unchanged

**`ResearchSessionDetail.test.tsx`** — extend SESSION fixture; add 1 assertion the scope line renders.

**`Research.test.tsx`** — extend fixture; add 1 assertion the Date range column renders.

### Migration smoke

Manual: `alembic upgrade head` against the live DB → verify 1 smoke session gone, 102+ BacktestRuns survive orphaned, 6 new columns with correct NOT NULL / nullability / defaults.

### Tests NOT being added

- Real production-data migration test (manual smoke covers it)
- `_pick_best_train_config` strip-list explicit test (covered indirectly by walk-forward E2E)
- E2E browser test (manual walkthrough)

### What's not changing

- 18 backtest/lab integration invariants — `BacktestRun.config_overrides` still gets the merged shape
- Hypothesis property test for datasets framework

## Configuration sequence

1. `alembic upgrade head` — drops the 1 smoke session, adds the 6 columns NOT NULL.
2. Restart the coord. The temporary algorithm_id-merge hack from `df006f8` is removed in this spec's implementation; the cleaner explicit-kwarg path takes over.
3. Refresh the dashboard. "New Session" modal now has the date pickers + cash + cost_profile + benchmark fields above the config editor.
4. Create a session: pick algorithm, set start/end/cash, leave cost_profile as "default", optionally set a benchmark.
5. New Sweep modal is unchanged (still 4 fields).
6. Sweep runs successfully — every BacktestRun row is populated with the session's algorithm_id / dates / cash / cost_profile.

## Deferred (tracked in backlog)

- **`symbols` + `data_source` on session** — `_pick_best_train_config` strips them suggesting they could be session-scoped, but `_run_one_backtest` doesn't currently read them. Add when needed.
- **Schema-derived `cost_profile` dropdown** — plain string input now; a registry of cost profiles would let the UI render a typed select. Lives with the "manifest-derived structured form" backlog entry.
- **`initial_cash` formatting helpers** — currency-formatted display in the summary; the modal input is a plain `<input type="number">`. Polish-tier.
