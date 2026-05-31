# Research Lab Dashboard (Phases 1 + 2) — Design

## Problem

The Research / Validation Lab is fully shipped on the backend (REST under `/api/research`, CLI as `quilt research`, `OptimizationSession` + `ResearchJob` + `BacktestRun` models, fire-and-poll job execution, markdown+HTML report generation), but has **zero presence in the dashboard**. The only way to use it today is the terminal.

That means:
- You cannot see what sweeps / walk-forwards are running without `quilt research session list && quilt research jobs ...`
- You cannot launch a sweep on an algorithm without composing a manifest path + JSON config blob in your shell
- You cannot watch progress without polling from a terminal
- New users have no entry point into the lab feature at all

This spec covers the first two phases of a five-phase build-out (Phase 3 walk-forward submission, Phase 4 sweep results matrix, Phase 4/5 walk-forward OOS chart, Phase 5 in-browser report viewer all go to the backlog).

## Goals

- **Sidebar nav entry "Research"** linking to a sessions list page
- **Sessions list** with empty state and a "New Session" button
- **Session creation form** (4-field modal) — name, hypothesis, parameter_space JSON, pre_registered_criteria JSON, notes
- **Session detail page** rendering session metadata, the list of associated jobs, and three action buttons (New Sweep, Generate Report, plus per-job Cancel)
- **Sweep submission form** (modal on the session detail page) — algorithm dropdown, base_config JSON, parameter_space JSON, search strategy, max_trials, parallelism, seed
- **Live job progress** via WebSocket push on the existing channel (new `research_job` event type)
- **Run links** from each completed job out to the existing `BacktestRunDetail.tsx` page
- **Generate Report** button surfaces file paths of the markdown+HTML the backend produces (in-browser rendering deferred to Phase 5)

## Non-goals (v1)

- Walk-forward submission UI (Phase 3) — backend exists, only the form is missing
- Sweep results matrix (Phase 4) — comparative grid across trials
- Walk-forward stitched OOS equity chart (Phase 4 or 5)
- In-browser markdown/HTML report viewer (Phase 5)
- Edit / delete sessions — sessions are immutable pre-registrations of an experiment, by design
- Manifest-derived structured form for the JSON fields — Q3-C from the brainstorm
- Session search / filter — defer until session count makes it useful
- Bulk job operations (cancel-all, retry-failed)

## Architecture

```
                          ┌────────────────────────────────┐
                          │   EXISTING BACKEND             │
                          │   (no functional changes)      │
                          ├────────────────────────────────┤
                          │ /api/research/sessions[/...]   │
                          │ ResearchJobManager             │
                          │ /api/algorithms                │
                          └─────────────┬──────────────────┘
                                        │ (small additive)
                                        ▼
                          ┌────────────────────────────────┐
                          │   NEW BACKEND additions         │
                          ├────────────────────────────────┤
                          │ ① `/api/algorithms` response   │
                          │    adds `manifest_path` field  │
                          │ ② WS broadcast `research_job`  │
                          │    event on status / progress  │
                          │    transitions                  │
                          │ ③ `POST /sessions/{id}/sweep`  │
                          │    and `…/walk-forward` accept │
                          │    `algorithm_id` as alt to    │
                          │    `manifest_path`             │
                          └────────────────────────────────┘
                                        ▲
                              REST + WS │
                                        │
       ┌────────────────────────────────┴────────────────────────────────┐
       │                       DASHBOARD (new)                            │
       ├──────────────────────────────────────────────────────────────────┤
       │  Sidebar:  Backtests  →  [ Research (Microscope) ]  →  Settings │
       │                                                                  │
       │  Routes:                                                         │
       │    /research                      → Sessions list page           │
       │    /research/sessions/{id}        → Session detail page          │
       │       (sweep submission is a modal, no separate route)           │
       │                                                                  │
       │  Pages, components, hooks per Section 3 below                    │
       └──────────────────────────────────────────────────────────────────┘
```

## Backend changes (three small additions)

### 1. `/api/algorithms` adds `manifest_path`

`coordinator/api/routes/algorithms.py:147` `_algo_to_response()` gains:

```python
"manifest_path": (
    str(Path(algo.source_path) / "quilt.yaml")
    if algo.source_path else None
),
```

Used by the dashboard to show the resolved manifest path in the sweep form once an algorithm is selected. Null-safe for orphaned algorithms.

### 2. WebSocket broadcast `research_job` event

The existing coord → dashboard WS channel already carries `download` events (for `MarketDataDownload` and `DatasetDownload` progress). It gains a `research_job` event type fired on every `ResearchJob` status / progress transition.

**`coordinator/services/research_job_manager.py`** — `ResearchJobManager` gains an optional constructor arg:

```python
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
        ...
        self._on_job_update = on_job_update

    async def _set_job(self, job_id: str, **fields):
        async with self._sf() as s:
            row = await s.get(ResearchJob, job_id)
            for k, v in fields.items():
                setattr(row, k, v)
            await s.commit()
        if self._on_job_update is not None:
            try:
                await self._on_job_update(_row_to_dict(row))
            except Exception:
                logger.exception("research job update broadcaster raised")
```

**`coordinator/main.py`** lifespan wires the broadcaster:

```python
async def _broadcast_research_update(payload: dict):
    await ws_broadcaster.publish({"type": "research_job", **payload})

research_manager = ResearchJobManager(
    ...,
    on_job_update=_broadcast_research_update,
)
```

(Match the actual broadcaster API in `coordinator/services/event_bus.py` or wherever live updates flow today.)

**Event envelope:**

```json
{
  "type": "research_job",
  "session_id": 7,
  "job_id": "abc-123-uuid",
  "kind": "sweep",
  "status": "running",
  "progress_pct": 0.42,
  "progress_message": "trial 12/30",
  "run_ids": ["...", "..."],
  "error_message": null,
  "completed_at": null
}
```

`kind` is included so the dashboard can render sweep vs walk-forward rows differently in Phase 3. `session_id` lets the dashboard route the event to the right session's job list without a separate lookup.

### 3. `POST /sessions/{id}/sweep` accepts `algorithm_id`

`coordinator/api/routes/research.py`:

```python
class SweepRequest(BaseModel):
    # Exactly one of manifest_path or algorithm_id must be provided.
    manifest_path: str | None = None
    algorithm_id: str | None = None
    base_config: dict
    parameter_space: dict | None = None
    search: Literal["grid", "random", "latin", "tpe"] = "grid"
    max_trials: int = 50
    parallelism: int = 1
    seed: int | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.manifest_path is None) == (self.algorithm_id is None):
            raise ValueError("provide exactly one of manifest_path or algorithm_id")
        return self
```

In the route handler, if `algorithm_id` is set, load the `Algorithm` row and use `<source_path>/quilt.yaml` as the resolved manifest path before forwarding to `ResearchJobManager.create_sweep_job`. Same change applied to `POST /sessions/{id}/walk-forward` for symmetry — Phase 3 inherits the friendlier contract without a second backend pass.

**Backward compatibility:** the existing CLI continues to use `manifest_path` and is unchanged.

### Things explicitly NOT changing on the backend

- No new tables, no migrations
- No changes to the 18 lab/engine invariants documented in `2026-05-28-backtest-and-validation-lab-integration.md`
- No changes to `OptimizationSession`, `ResearchJob`, `BacktestRun` models
- No changes to the existing CLI commands

## Frontend

### File layout

```
dashboard/src/
├── pages/
│   ├── Research.tsx                       # sessions list
│   └── ResearchSessionDetail.tsx          # one session + its jobs
│
├── components/
│   ├── NewSessionModal.tsx                # create session form
│   ├── NewSweepModal.tsx                  # create sweep form
│   ├── ResearchJobRow.tsx                 # status / progress / cancel / run links
│   ├── ResearchSessionSummary.tsx         # header card on detail page
│   └── JsonTextField.tsx                  # reusable JSON textarea + parse validation
│
├── hooks/
│   ├── useResearchSessions.ts             # list + create
│   ├── useResearchSession.ts              # single session + its jobs
│   ├── useResearchMutations.ts            # createSweep, cancelJob, generateReport
│   └── useWebSocketSync.ts                # EXTEND existing — add research_job
│                                          # subscription block
│
├── api/
│   ├── client.ts                          # add research endpoints + types
│   └── hooks.ts                           # alternative location for hooks if convention
│
└── components/Layout.tsx                  # modify — add Research nav item
```

`components/Layout.tsx` (existing): a new entry between `backtests` and `settings`:

```typescript
{ to: "/research", label: "Research", icon: Microscope },
```

### Pages

**`Research.tsx`** — list of sessions. Columns: name, status badge, hypothesis (truncated to ~80 chars with hover tooltip for full text), job count, created_at. Click a row → `/research/sessions/{id}`. Top-right "New Session" button opens `NewSessionModal`. Empty state with "Create your first session" CTA when API returns `[]`.

**`ResearchSessionDetail.tsx`** — three stacked regions:

1. **Header (`ResearchSessionSummary`):** name, status badge, created_at, action buttons (`New Sweep`, `Generate Report`). Hypothesis text in a collapsible section since it can be long. Parameter space and criteria rendered as read-only JSON (via `JsonTextField disabled`).

2. **Jobs list:** a vertical stack of `ResearchJobRow`s, newest first. Empty state: "No jobs yet. Click New Sweep to start one."

3. *(Future Phase 4 adds a comparative results matrix region here. Not built in this spec.)*

### `ResearchJobRow`

Per-row contents:
- **Kind** — `sweep` or `walk-forward` badge (color-coded)
- **Status** — pill (`queued` / `running` / `completed` / `failed` / `cancelled`)
- **Progress** — bar + percentage + `progress_message` text below
- **Runs** — count badge; expanding chevron reveals `run_ids` as small clickable links → `/backtests/runs/{id}`. "—" if 0 runs and status is terminal.
- **Timing** — relative time strings ("3m ago", "started 2m ago", "ran for 5m")
- **Actions** — `Cancel` button when status ∈ {queued, running}; nothing otherwise

Click anywhere on the row body (not the action / link cells) expands to reveal:
- `error_message` (when status=failed)
- Full `request_payload` JSON (for "what was this sweep again?" debugging)

### Modals

**`NewSessionModal`** — 4 required fields + 1 optional:

| Field | Type | Notes |
|---|---|---|
| `name` | text | server validates uniqueness; show 422 inline |
| `hypothesis` | textarea | free-form pre-registration text |
| `parameter_space` | JSON via `JsonTextField` | required |
| `pre_registered_criteria` | JSON via `JsonTextField` | required |
| `notes` | textarea | optional |

Submit disabled until all required fields are populated AND both JSON fields parse. On success: modal closes, page navigates to the new session detail.

**`NewSweepModal`** — 6 fields (modal opens on a session detail page, so `session_id` is implicit):

| Field | Type | Notes |
|---|---|---|
| `algorithm` | dropdown | populated from `useAlgorithms()` — selection sends `algorithm_id` to backend |
| `base_config` | JSON via `JsonTextField` | defaults to `{}` |
| `parameter_space` | JSON via `JsonTextField` | optional — blank sends `null`, server falls back to session's pre-registered space |
| `search` | select | grid / random / latin / tpe; default grid |
| `max_trials` | number | default 50, min 1 |
| `parallelism` | number | default 1, min 1 |
| `seed` | number | optional |

Submit disabled until algorithm picked AND all JSON fields parse. On success: modal closes; optimistic insertion of a "queued" job row at the top of the jobs list (WS push will hydrate as it transitions).

### `JsonTextField` — small reusable

Live JSON parse on every keystroke (debounced 200ms). Renders:
- `<textarea>` with monospaced font
- Small status line below: green checkmark + "valid" / red X + parse error message with line:col
- Red border when invalid
- `disabled` prop renders read-only (used on session summary for `parameter_space` + `criteria` display — same component, no editing)

One component. Used in both modals AND on the read-only summary card. Avoids reinventing JSON validation in three places.

### Live job updates — extend `useWebSocketSync`

The project already has a single WS subscription hub: `dashboard/src/hooks/useWebSocketSync.ts` calls `wsManager.subscribe(eventType, handler)` from `api/websocket` for every server-pushed event (e.g. `instance_started`, `trade_executed`, `heartbeat`). The pattern is "one subscription block per event type, each invalidates / patches the relevant query cache."

We follow the same pattern. Inside the existing `useWebSocketSync` hook, add:

```typescript
const unsubscribeResearchJob = wsManager.subscribe(
  "research_job",
  (data) => {
    const msg = data as {
      session_id: number;
      job_id: string;
      [k: string]: unknown;
    };
    // Patch any cached jobs-list query for this session
    queryClient.setQueriesData<JobResponse[]>(
      { queryKey: keys.researchJobs(msg.session_id) },
      (old) => old?.map(j => j.job_id === msg.job_id ? { ...j, ...msg } : j),
    );
    // Patch the single-job query if anyone's watching it
    queryClient.setQueryData(
      keys.researchJob(msg.session_id, msg.job_id),
      (old: any) => old ? { ...old, ...msg } : msg,
    );
  },
);
```

And add to the unsubscribe array in the `useEffect` cleanup.

`keys.researchJobs(sessionId)` and `keys.researchJob(sessionId, jobId)` are new entries in the `keys` object exported by `api/hooks.ts` (matches the existing convention for query key construction).

**Server side:** the WS event type string published by the coord is `"research_job"`, matching the `wsManager.subscribe` filter.

### Loading / empty / error states

- **List page:** spinner during initial fetch; empty state with CTA if 0 sessions; error toast on fetch fail with retry button.
- **Detail page:** spinner during initial fetch; 404 page if session id is unknown; error toast on fetch fail.
- **Modals:** disabled submit during pending mutation; success toast on resolve; inline form error on validation / 422 from server.

## Testing strategy

### Backend (pytest)

`tests/coordinator/api/test_algorithms_routes.py` (extend):
- `manifest_path` present in `/api/algorithms` response, equals `<source_path>/quilt.yaml`
- `manifest_path` is `null` when `source_path` is `null`

`tests/coordinator/api/test_research_routes.py` (extend):
- `POST /sessions/{id}/sweep` with `algorithm_id=<known>` succeeds; resolved `manifest_path` lands in the queued `ResearchJob.request_payload`
- `POST /sessions/{id}/sweep` with both `algorithm_id` and `manifest_path` returns 422
- `POST /sessions/{id}/sweep` with neither returns 422
- `POST /sessions/{id}/sweep` with `algorithm_id=<unknown>` returns 404
- Symmetry: same three cases for `POST /sessions/{id}/walk-forward`

`tests/coordinator/services/test_research_job_manager.py` (extend):
- When `on_job_update` callback is provided, `_set_job` invokes it after commit with a payload containing the post-update field values
- When `on_job_update` raises, the DB commit still succeeds and the exception is logged (not swallowed silently)
- WS-integration smoke: a sweep that transitions queued → running → completed yields three published messages with monotonic progress

### Frontend (`@testing-library/react`, vitest)

`pages/Research.test.tsx`:
- Renders empty state with CTA when API returns `[]`
- Renders rows for each session in the response
- Clicking "New Session" opens `NewSessionModal`
- Clicking a row navigates to `/research/sessions/{id}`

`pages/ResearchSessionDetail.test.tsx`:
- Renders header summary fields (name, status badge, hypothesis)
- Renders parameter_space and criteria as read-only JSON
- "New Sweep" button opens `NewSweepModal`
- "Generate Report" disabled when 0 completed runs; enabled when ≥1; on click calls API and shows file-paths toast
- Jobs list renders one `ResearchJobRow` per job
- Empty jobs state with the right copy

`components/NewSessionModal.test.tsx`:
- Submit disabled until name + hypothesis + valid parameter_space + valid criteria
- Invalid JSON in either field disables submit and shows the parse error
- Successful submit calls the create-session API with the correct body shape and closes the modal

`components/NewSweepModal.test.tsx`:
- Algorithm dropdown populates from `useAlgorithms()` mock
- Submit body includes `algorithm_id` (NOT `manifest_path`)
- Invalid JSON in base_config or parameter_space disables submit
- Empty parameter_space submits as `null`
- Search-strategy select reflects selection

`components/ResearchJobRow.test.tsx`:
- Status pill colors per status
- Progress bar width matches `progress_pct`
- Cancel button present for `queued`/`running`, absent otherwise
- Run links render `run_ids.length` chips and each links to `/backtests/runs/{id}`
- Click row expands and shows `request_payload` + `error_message` (when failed)

`components/JsonTextField.test.tsx`:
- Valid JSON → green status, no error border
- Invalid JSON → red status, error message includes line/col
- `disabled` prop renders as read-only (no editing, no error UI)
- Debounced parse (fake timers — type 5 chars, advance 100ms → no parse; advance 200ms → parsed)

`hooks/useWebSocketSync.test.ts` (extend if exists, create otherwise):
- Mock `wsManager`, dispatch a `research_job` message → matching job in the query cache is updated
- The `unsubscribeResearchJob` cleanup is called on unmount (no leak)
- Patching a non-existent session's cache is a no-op (no error thrown)

### Out of scope

- Real WS end-to-end test from coord to dashboard (verified separately via the existing WS smoke pattern; the new event type is shape-tested at both endpoints which is what catches actual regressions)
- Screenshot / visual regression tests (dashboard doesn't use them today)
- Load tests for the live update path (single user, few jobs at a time)
- Tests for `quilt research *` CLI (unchanged)

## Configuration sequence

1. User installs/upgrades. No migration required (no schema changes).
2. User refreshes the dashboard. New "Research" entry appears in the sidebar.
3. Clicking it lands on `/research` — empty state with "Create your first session" CTA.
4. User clicks → modal → fills name + hypothesis + parameter_space + criteria → submit.
5. Page navigates to `/research/sessions/{id}` showing the new session.
6. User clicks "New Sweep" → modal → picks algorithm from dropdown → fills base_config + (optional) parameter_space → submit.
7. New `queued` job row appears optimistically; transitions to `running` and updates progress live via WS push.
8. On `completed`, run links activate; user can click through to `/backtests/runs/{id}` for any individual trial.
9. After ≥1 completed run, "Generate Report" enables; click produces markdown + HTML files and shows their paths in a toast for the user to open out-of-band.

## Deferred to v1.1+ (tracked in `docs/superpowers/backlog.md` under "Research Lab dashboard")

- **Phase 3 — Walk-forward submission UI.** Sister form to NewSweepModal with train/test/step/objective fields. Backend already exists.
- **Phase 4 — Sweep results matrix.** Comparative grid across all trials with sortable metric columns and per-config-parameter columns; click-through to single-run detail. The substantial UX of "compare 50 runs at once."
- **Phase 4 or 5 — Walk-forward stitched OOS equity chart.** Concatenated OOS curve with per-fold boundary markers, lightweight-charts (already a project dep).
- **Phase 5 — In-browser report viewer.** Renders the markdown + HTML output of `POST /sessions/{id}/report` inline. v1 surfaces the file paths.
- **Manifest-derived structured form for JSON config fields.** Reads the algorithm's `config_schema` and renders typed inputs (sliders for numeric ranges, dropdowns for enums, multi-select for arrays) instead of raw JSON textareas. Replaces the `JsonTextField` for `base_config` (and partially for `parameter_space` where the keys correspond to typed config fields).
- **Session deletion / archive.** Sessions are immutable pre-registrations and there's no tidy-up affordance today. A safe "archive" (hide from default list, retain row) is the right pattern when needed; hard delete should probably never exist.
- **Session list filters / search.** Useful when session count grows past ~50.
- **Bulk job operations.** Cancel-all-running, retry-failed — build when someone hits the friction.
- **Compare-runs view.** Pick N runs and render their metrics + equity curves side-by-side. Natural Phase 6 once the matrix exists.
