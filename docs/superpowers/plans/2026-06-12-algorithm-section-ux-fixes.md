# Algorithm Section UX Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 15 UX/UI issues found in the 2026-06-12 review of the dashboard's algorithm section — navigation that escapes algorithm scope, silent error swallowing, missing feedback toasts, dead buttons, raw/unformatted data, and missing job context.

**Architecture:** Frontend-only changes for 11 of 13 tasks (React 18 + TypeScript + TanStack Query + Zustand in `dashboard/`). Two tasks touch the FastAPI backend: Task 11 serializes `request_payload`/`result` on research job responses, Task 12 adds a cost-profiles listing endpoint. No DB migrations.

**Tech Stack:** React 18.3, React Router 6.28, TanStack Query 5.62, Zustand 5 (`useUIStore.addAlert` for toasts), Tailwind, Vitest + React Testing Library. Backend: FastAPI + pydantic v2 + SQLAlchemy async, pytest.

**Branch:** Create branch `ux-fixes` off `main` before Task 1 (`git checkout -b ux-fixes`). Do not work on `main`.

**Commands (run from these directories):**
- Frontend tests: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run <path>`
- Frontend typecheck: `cd /home/jkern/dev/quilt-trader/dashboard && npm run typecheck`
- Backend tests: `cd /home/jkern/dev/quilt-trader && .venv/bin/pytest <path> -v`

**Known pre-existing failures:** The backend full suite has ~31 known pre-existing failures (tick-context, options-mtm, sweep TPE, equity-curve, accounts/portfolio API, download-manager, alpaca adapter). Do not try to fix them; only the test files you touch must be green.

---

## File Structure

| File | Change |
|---|---|
| `dashboard/src/pages/BacktestRunDetail.tsx` | Scope-aware back nav (T1); trades load-more (T6) |
| `dashboard/src/pages/ResearchSessionDetail.tsx` | Scope-aware back nav, pass algorithmId to job rows (T1) |
| `dashboard/src/pages/Research.tsx` | Scope-aware row nav (T1); status badge + error detail (T9); fmtDate (T8) |
| `dashboard/src/components/ResearchJobRow.tsx` | Scoped run links (T1); fmtDateTime (T8); request params display (T11) |
| `dashboard/src/components/NewCpcvModal.tsx` | JSON validation via JsonTextField + submit toasts (T2) |
| `dashboard/src/components/NewSweepModal.tsx` | Success toast (T3) |
| `dashboard/src/components/ParameterSetsSection.tsx` | Remove dead Deploy button; fix isBest (T4) |
| `dashboard/src/pages/AlgorithmConfig.tsx` | Drop onDeploy prop (T4); loading state (T7) |
| `dashboard/src/pages/DeploymentDetail.tsx` | Visible run-filter scope note (T5) |
| `dashboard/src/pages/AlgorithmHub.tsx` | Loading state (T7); formatting (T8); empty-state CTAs (T10) |
| `dashboard/src/lib/formatNumbers.ts` | Add fmtDate/fmtDateTime (T8) |
| `dashboard/src/components/ExperimentScopeFields.tsx` | Cost-profile dropdown (T12) |
| `dashboard/src/components/NewSessionModal.tsx` | Dirty-guard backdrop close (T13) |
| `dashboard/src/api/client.ts` | ResearchJob.request_payload type (T11); listCostProfiles (T12) |
| `dashboard/src/api/hooks.ts` | useCostProfiles (T12) |
| `coordinator/services/research_job_manager.py` | request_payload in `_row_to_dict` (T11) |
| `coordinator/api/routes/research.py` | JobResponse request_payload + result fields (T11); GET /cost-profiles (T12) |
| `coordinator/services/validation/cost_model.py` | `list_profile_names()` (T12) |
| `docs/superpowers/backlog.md` | Deferred-work entries (T4, T5) |

---

### Task 1: Scope-aware navigation (P0)

Pages under `/algorithms/:id/...` contain links/navigations that jump back to legacy global routes (`/backtests`, `/research`, `/backtest-runs/:id`), kicking the user out of the algorithm context. Routes (from `dashboard/src/App.tsx:66-81`): scoped pages live under `/algorithms/:id` (children `backtests/:runId`, `research/:sessionId`); legacy `/backtests` and `/research` now just `Navigate to="/algorithms"`, so these links silently dump users at the grid.

**Files:**
- Modify: `dashboard/src/pages/BacktestRunDetail.tsx:51,71`
- Modify: `dashboard/src/pages/ResearchSessionDetail.tsx:34,44,77-82`
- Modify: `dashboard/src/components/ResearchJobRow.tsx:6-9,102-110`
- Modify: `dashboard/src/pages/Research.tsx:64,88`
- Test (create): `dashboard/src/pages/BacktestRunDetail.nav.test.tsx`
- Test (create): `dashboard/src/pages/ResearchSessionDetail.nav.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `dashboard/src/pages/BacktestRunDetail.nav.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BacktestRunDetail } from "./BacktestRunDetail";

vi.mock("../api/hooks", async () => {
  const actual = await vi.importActual<object>("../api/hooks");
  return {
    ...actual,
    useBacktestReport: () => ({
      data: {
        status: "completed",
        key_metrics: null,
        config_overrides: {},
        eoy_returns: [],
        drawdown_periods: [],
      },
    }),
    useBacktestTrades: () => ({ data: { items: [], total: 0 } }),
    useDeleteBacktestRun: () => ({ mutateAsync: vi.fn(), isPending: false }),
  };
});
vi.mock("../components/report/EquitySlot", () => ({ EquitySlot: () => null }));
vi.mock("../components/report/DrawdownSlot", () => ({ DrawdownSlot: () => null }));
vi.mock("../components/report/ReturnsDistributionSlot", () => ({ ReturnsDistributionSlot: () => null }));
vi.mock("../components/report/RollingMetricsSlot", () => ({ RollingMetricsSlot: () => null }));

function renderAt(path: string, routePath: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={routePath} element={<BacktestRunDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("BacktestRunDetail scope-aware navigation", () => {
  it("back link targets the algorithm's backtests list when algo-scoped", () => {
    renderAt("/algorithms/algo-1/backtests/run-9", "/algorithms/:id/backtests/:runId");
    const back = screen.getAllByRole("link")[0];
    expect(back).toHaveAttribute("href", "/algorithms/algo-1/backtests");
  });

  it("back link falls back to /algorithms on the legacy route", () => {
    renderAt("/backtest-runs/run-9", "/backtest-runs/:id");
    const back = screen.getAllByRole("link")[0];
    expect(back).toHaveAttribute("href", "/algorithms");
  });
});
```

Create `dashboard/src/pages/ResearchSessionDetail.nav.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ResearchSessionDetail } from "./ResearchSessionDetail";

const session = {
  id: 7, name: "sess", hypothesis: "h", status: "open", notes: "",
  created_at: "2026-01-01T00:00:00Z", completed_at: null,
  algorithm_id: "algo-1", base_config: {}, parameter_space: {},
  pre_registered_criteria: {}, n_runs: 1,
  date_range_start: "2024-01-01", date_range_end: "2024-12-31",
  initial_cash: 10000, cost_profile: "default",
  benchmark_symbol: null, benchmark_source: null,
};
const job = {
  job_id: "j1", session_id: 7, kind: "sweep", status: "completed",
  progress_pct: 1, progress_message: null, run_ids: ["run-42"],
  error_message: null, started_at: null, completed_at: null, created_at: null,
};

vi.mock("../hooks/useResearchSession", () => ({
  useResearchSession: () => ({ data: session, isLoading: false, error: null }),
  useResearchJobs: () => ({ data: [job] }),
}));
vi.mock("../hooks/useResearchMutations", () => ({
  useCancelResearchJob: () => ({ mutate: vi.fn() }),
  useGenerateResearchReport: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateResearchSweep: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

function renderScoped() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/algorithms/algo-1/research/7"]}>
        <Routes>
          <Route path="/algorithms/:id/research/:sessionId" element={<ResearchSessionDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ResearchSessionDetail scope-aware navigation", () => {
  it("back link targets the algorithm's research list", () => {
    renderScoped();
    expect(screen.getByRole("link", { name: /back to sessions/i }))
      .toHaveAttribute("href", "/algorithms/algo-1/research");
  });

  it("expanded job run links target the algo-scoped backtest route", () => {
    renderScoped();
    screen.getByText("sweep").click();
    expect(screen.getByRole("link", { name: "run-42" }))
      .toHaveAttribute("href", "/algorithms/algo-1/backtests/run-42");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/pages/BacktestRunDetail.nav.test.tsx src/pages/ResearchSessionDetail.nav.test.tsx`
Expected: FAIL — hrefs are `/backtests`, `/research`, `/backtest-runs/run-42`.

Note: the expanded-job-link test fires a click via `.click()`; if the expand doesn't register, use `fireEvent.click(screen.getByText("sweep"))` from `@testing-library/react` instead.

- [ ] **Step 3: Implement**

In `dashboard/src/pages/BacktestRunDetail.tsx`, after line 37 (`const runId = ...`), add:

```tsx
  // On the scoped route /algorithms/:id/backtests/:runId, params.id is the
  // algorithm id; on legacy /backtest-runs/:id it is the run id.
  const algoId = params.runId ? params.id : undefined;
  const backHref = algoId ? `/algorithms/${algoId}/backtests` : "/algorithms";
```

Change line 51 `navigate("/backtests");` → `navigate(backHref);`
Change line 71 `<Link to="/backtests" ...>` → `<Link to={backHref} className="text-gray-400 hover:text-white">`

In `dashboard/src/pages/ResearchSessionDetail.tsx`, after line 18 (`const sessionId = ...`), add:

```tsx
  // On /algorithms/:id/research/:sessionId, params.id is the algorithm id.
  const algoIdFromRoute = params.sessionId ? params.id : undefined;
```

Replace both `<Link to="/research" ...>` (lines 34 and 44) with `<Link to={algoIdFromRoute ? \`/algorithms/${algoIdFromRoute}/research\` : "/algorithms"} ...>` keeping the existing classNames and children. For the line-44 link (rendered after `session` is loaded), prefer the session's own algorithm id so it also works on the legacy route:

```tsx
      <Link
        to={`/algorithms/${session.algorithm_id}/research`}
        className="text-sm text-gray-400 hover:text-gray-200 flex items-center gap-1"
      >
        <ArrowLeft size={14} /> Back to sessions
      </Link>
```

(The line-34 "Session not found" branch has no session object — use the `algoIdFromRoute ? ... : "/algorithms"` form there.)

Pass the algorithm id into each job row (line 79-82):

```tsx
            <ResearchJobRow
              job={job}
              algorithmId={session.algorithm_id}
              onCancel={(jobId) => void cancelMut.mutate(jobId)}
            />
```

In `dashboard/src/components/ResearchJobRow.tsx`, change the props interface:

```tsx
interface Props {
  job: ResearchJob;
  algorithmId?: string;
  onCancel: (jobId: string) => void;
}
```

Change the component signature to `export function ResearchJobRow({ job, algorithmId, onCancel }: Props) {` and the run link (line 105):

```tsx
                    to={algorithmId ? `/algorithms/${algorithmId}/backtests/${rid}` : `/backtest-runs/${rid}`}
```

In `dashboard/src/pages/Research.tsx`, change line 64 row click and line 88 onCreated to stay scoped when the `algorithmId` prop is present:

```tsx
                  onClick={() => nav(algorithmId ? `/algorithms/${algorithmId}/research/${s.id}` : `/research/sessions/${s.id}`)}
```

```tsx
        onCreated={(id) => {
          setModalOpen(false);
          nav(algorithmId ? `/algorithms/${algorithmId}/research/${id}` : `/research/sessions/${id}`);
        }}
```

- [ ] **Step 4: Run tests to verify they pass, plus existing suites**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/pages/BacktestRunDetail.nav.test.tsx src/pages/ResearchSessionDetail.nav.test.tsx src/pages/Research.test.tsx src/pages/ResearchSessionDetail.test.tsx src/pages/BacktestRunDetail.params.test.tsx src/components/ResearchJobRow.test.tsx 2>/dev/null || npx vitest run src/pages src/components`
Expected: PASS (run whatever subset of those files exists; ResearchJobRow tests may live elsewhere — `npx vitest run src` is the safe fallback).

Run: `npm run typecheck`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/pages/BacktestRunDetail.tsx dashboard/src/pages/ResearchSessionDetail.tsx dashboard/src/components/ResearchJobRow.tsx dashboard/src/pages/Research.tsx dashboard/src/pages/BacktestRunDetail.nav.test.tsx dashboard/src/pages/ResearchSessionDetail.nav.test.tsx
git commit -m "fix(dashboard): keep navigation inside algorithm scope on detail pages"
```

---

### Task 2: NewCpcvModal — validate parameter-space JSON, surface submit result (P0)

`NewCpcvModal.tsx:46` silently swallows JSON parse errors (`catch { /* ignore */ }`), so a typo in the parameter space submits `parameter_space: undefined` with no warning. The submit handler (lines 182-185) has no error handling — a failed POST closes nothing and shows nothing. Reuse the existing `JsonTextField` component (`dashboard/src/components/JsonTextField.tsx`), which debounce-validates JSON and reports errors via `onError`, and the `useUIStore.addAlert` toast (severities: `"info" | "warning" | "error" | "success"`).

**Files:**
- Modify: `dashboard/src/components/NewCpcvModal.tsx`
- Test: `dashboard/src/components/NewCpcvModal.test.tsx` (extend existing file)

- [ ] **Step 1: Write the failing tests**

Add to `dashboard/src/components/NewCpcvModal.test.tsx`. First extend the existing mocks at the top of the file — add a store mock after the existing `vi.mock("../api/hooks", ...)` block:

```tsx
const addAlert = vi.fn();
vi.mock("../stores/ui", () => ({
  useUIStore: (sel: (s: { addAlert: typeof addAlert }) => unknown) => sel({ addAlert }),
}));
```

Then add these tests inside the existing `describe("NewCpcvModal", ...)` block:

```tsx
  it("disables submit while parameter-space JSON is invalid in select mode", async () => {
    renderModal();
    fireEvent.click(screen.getByLabelText(/select/i));
    const textarea = screen.getByLabelText(/parameter space/i);
    fireEvent.change(textarea, { target: { value: "{not json" } });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /submit/i })).toBeDisabled(),
    );
  });

  it("shows a success toast and closes after submit", async () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: /submit/i }));
    await waitFor(() =>
      expect(addAlert).toHaveBeenCalledWith(
        expect.objectContaining({ severity: "success" }),
      ),
    );
  });

  it("shows an error toast when submit fails", async () => {
    mut.mockRejectedValueOnce(new Error("boom"));
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: /submit/i }));
    await waitFor(() =>
      expect(addAlert).toHaveBeenCalledWith(
        expect.objectContaining({ severity: "error" }),
      ),
    );
  });
```

Import `waitFor` from `@testing-library/react` in the existing import line, and add `beforeEach(() => addAlert.mockClear());` next to the existing `mut.mockClear()`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/components/NewCpcvModal.test.tsx`
Expected: the 3 new tests FAIL (submit never disables; addAlert never called). The 3 pre-existing tests must still pass.

- [ ] **Step 3: Implement**

In `dashboard/src/components/NewCpcvModal.tsx`:

Replace the imports at the top:

```tsx
import { useState, useMemo } from "react";
import { X } from "lucide-react";
import { useCreateCpcvJob } from "../api/hooks";
import { useUIStore } from "../stores/ui";
import { JsonTextField } from "./JsonTextField";
import type { CPCVRequest } from "../types/index";
```

Replace the `paramSpaceJson` state (line 36) with:

```tsx
  const addAlert = useUIStore((s) => s.addAlert);
  const [paramSpace, setParamSpace] = useState<Record<string, unknown> | null>({});
  const [paramSpaceValid, setParamSpaceValid] = useState(true);
```

Replace the `req` memo (lines 38-49) with:

```tsx
  const req: CPCVRequest = useMemo(() => {
    const r: CPCVRequest = {
      mode, n_groups: nGroups, test_groups_per_split: k,
      embargo, purge_horizon: purge,
    };
    if (mode === "select") {
      r.search = search;
      r.max_trials_per_split = maxTrials;
      r.parameter_space = paramSpace ?? {};
    }
    return r;
  }, [mode, nGroups, k, embargo, purge, search, maxTrials, paramSpace]);
```

Replace the parameter-space textarea block (lines 158-167) with:

```tsx
              <div className="col-span-2">
                <JsonTextField
                  label="Parameter space (JSON)"
                  value={paramSpace}
                  onChange={setParamSpace}
                  onError={(hasErr) => setParamSpaceValid(!hasErr)}
                  rows={4}
                  placeholder='{"lookback": [20, 50, 100]}'
                />
              </div>
```

Replace the submit button (lines 181-190) with:

```tsx
          <button
            onClick={async () => {
              try {
                await mut.mutateAsync({ sessionId, body: req });
                addAlert({
                  message: `CPCV job queued (${projected} projected backtests).`,
                  severity: "success",
                });
                onClose();
              } catch (e) {
                addAlert({
                  message: `Failed to queue CPCV job: ${e instanceof Error ? e.message : "unknown error"}`,
                  severity: "error",
                });
              }
            }}
            disabled={mut.isPending || (mode === "select" && !paramSpaceValid)}
            className="bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-4 py-2 rounded disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {mut.isPending ? "Queuing…" : "Submit"}
          </button>
```

Note: `JsonTextField` debounces validation by 200ms — the test's `waitFor` covers that.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/components/NewCpcvModal.test.tsx && npm run typecheck`
Expected: all 6 tests PASS, typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/NewCpcvModal.tsx dashboard/src/components/NewCpcvModal.test.tsx
git commit -m "fix(dashboard): validate CPCV parameter-space JSON and toast submit outcome"
```

---

### Task 3: NewSweepModal — success toast (P1)

`NewSweepModal.tsx` shows inline errors but gives zero feedback on success — the modal just closes. Add a success toast via `useUIStore.addAlert`.

**Files:**
- Modify: `dashboard/src/components/NewSweepModal.tsx:21-31`
- Test: `dashboard/src/components/NewSweepModal.test.tsx` (extend existing file)

- [ ] **Step 1: Write the failing test**

In `dashboard/src/components/NewSweepModal.test.tsx`, add a store mock at module level (alongside the file's existing mocks — it mocks `../hooks/useResearchMutations` for `useCreateResearchSweep`):

```tsx
const addAlert = vi.fn();
vi.mock("../stores/ui", () => ({
  useUIStore: (sel: (s: { addAlert: typeof addAlert }) => unknown) => sel({ addAlert }),
}));
```

Add a test (adjust the submit-button name regex to the existing file's conventions — the button text is "Start sweep"):

```tsx
  it("shows a success toast after the sweep is queued", async () => {
    renderModal(); // use this file's existing render helper
    fireEvent.click(screen.getByRole("button", { name: /start sweep/i }));
    await waitFor(() =>
      expect(addAlert).toHaveBeenCalledWith(
        expect.objectContaining({ severity: "success" }),
      ),
    );
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/components/NewSweepModal.test.tsx`
Expected: new test FAILS (addAlert never called); existing tests pass.

- [ ] **Step 3: Implement**

In `dashboard/src/components/NewSweepModal.tsx`:

Add import: `import { useUIStore } from "../stores/ui";`
Inside the component, after `const mut = ...`: `const addAlert = useUIStore((s) => s.addAlert);`

In `handleSubmit`, after `await mut.mutateAsync({...})` and before `onClose()`:

```tsx
      addAlert({ message: "Sweep queued.", severity: "success" });
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/components/NewSweepModal.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/NewSweepModal.tsx dashboard/src/components/NewSweepModal.test.tsx
git commit -m "feat(dashboard): success toast when a sweep is queued"
```

---

### Task 4: Parameter sets — remove dead Deploy button, fix "best" highlight (P0 + P2)

Two issues in `ParameterSetsSection.tsx`:
1. The per-row **Deploy** button (lines 189-194) calls `onDeploy`, whose only implementation is an empty TODO (`AlgorithmConfig.tsx:45-47`). There is no deployment-creation flow in the dashboard at all (`grep createDeployment` → nothing). A button that does literally nothing is worse than no button — remove it and the `onDeploy` prop; record the real deploy-from-parameter-set flow in the backlog.
2. `isBest={i === 0}` (line 350) highlights whatever row the server returns first, not the actual best. Compute best-by-Sharpe from `ps.best_backtest.sharpe_ratio`.

**Files:**
- Modify: `dashboard/src/components/ParameterSetsSection.tsx`
- Modify: `dashboard/src/pages/AlgorithmConfig.tsx:45-47`
- Modify: `docs/superpowers/backlog.md`
- Test (create): `dashboard/src/components/ParameterSetsSection.best.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `dashboard/src/components/ParameterSetsSection.best.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ParameterSetsSection } from "./ParameterSetsSection";
import type { ParameterSet } from "../types";

function makeSet(id: string, name: string, sharpe: number | null): ParameterSet {
  return {
    id, algorithm_id: "algo-1", name,
    config_values: { x: 1 },
    created_at: "2026-01-01", updated_at: "2026-01-01",
    best_backtest: sharpe == null ? null : {
      sharpe_ratio: sharpe, total_return: 0.1, max_drawdown: -0.05, run_count: 2,
    },
  };
}

const sets = [
  makeSet("aaaaaaaa-1", "first-but-mediocre", 0.5),
  makeSet("bbbbbbbb-2", "actual-best", 2.1),
  makeSet("cccccccc-3", "no-runs", null),
];

vi.mock("../api/hooks", async () => {
  const actual = await vi.importActual<object>("../api/hooks");
  return {
    ...actual,
    useParameterSets: () => ({ data: sets }),
    useCreateParameterSet: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useDeleteParameterSet: () => ({ mutateAsync: vi.fn() }),
    useImportParameterSets: () => ({ mutateAsync: vi.fn() }),
  };
});

describe("ParameterSetsSection", () => {
  it("highlights the row with the highest Sharpe, not the first row", () => {
    render(
      <ParameterSetsSection
        algorithmId="algo-1"
        manifestConfig={[]}
        onBacktest={() => {}}
      />,
    );
    const bestRow = screen.getByText("actual-best").closest("tr")!;
    const firstRow = screen.getByText("first-but-mediocre").closest("tr")!;
    expect(bestRow.className).toContain("bg-emerald-950/30");
    expect(firstRow.className).not.toContain("bg-emerald-950/30");
  });

  it("renders no Deploy button (no deploy flow exists)", () => {
    render(
      <ParameterSetsSection
        algorithmId="algo-1"
        manifestConfig={[]}
        onBacktest={() => {}}
      />,
    );
    expect(screen.queryByRole("button", { name: /^deploy$/i })).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/components/ParameterSetsSection.best.test.tsx`
Expected: FAIL — first failure is a TypeScript/props error (`onDeploy` is required) or the assertions (first row highlighted, Deploy button present).

- [ ] **Step 3: Implement**

In `dashboard/src/components/ParameterSetsSection.tsx`:

1. Remove `onDeploy: () => void;` from `Props` (line 21) and from `RowProps` (line 152). Update both destructurings: `function ParameterSetRow({ ps, isBest, onDeleteClick, onBacktest }: RowProps)` and `export function ParameterSetsSection({ algorithmId, manifestConfig, onBacktest }: Props)`.
2. Delete the Deploy button (lines 189-194):

```tsx
          <button
            onClick={onDeploy}
            className="text-xs text-gray-400 hover:text-gray-200 transition-colors"
          >
            Deploy
          </button>
```

3. In the main component, before the `return`, compute the best id:

```tsx
  const bestId = parameterSets.reduce<{ id: string | null; sharpe: number }>(
    (acc, ps) => {
      const s = ps.best_backtest?.sharpe_ratio;
      return s != null && s > acc.sharpe ? { id: ps.id, sharpe: s } : acc;
    },
    { id: null, sharpe: -Infinity },
  ).id;
```

4. Change the row render (line 346-355): `isBest={ps.id === bestId}` and remove `onDeploy={onDeploy}`.

In `dashboard/src/pages/AlgorithmConfig.tsx`, delete lines 45-47 (`onDeploy={() => { // TODO... }}`).

In `docs/superpowers/backlog.md`, under a new or existing dashboard section, add:

```markdown
- **Deploy-from-parameter-set flow** (deferred 2026-06-12, UX fixes): the dashboard has no
  deployment-creation flow at all; the dead per-row "Deploy" button in ParameterSetsSection
  was removed. Real fix: a CreateDeployment modal (pick account/worker, preload the
  parameter set's config_values) wired to POST /api/deployments.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/components/ParameterSetsSection.best.test.tsx src/pages && npm run typecheck`
Expected: PASS, typecheck clean (typecheck is what catches any other `onDeploy` call site).

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/ParameterSetsSection.tsx dashboard/src/pages/AlgorithmConfig.tsx dashboard/src/components/ParameterSetsSection.best.test.tsx docs/superpowers/backlog.md
git commit -m "fix(dashboard): remove dead Deploy button; highlight true best parameter set"
```

---

### Task 5: DeploymentDetail — visible run-filter scope note (P0)

`DeploymentDetail.tsx:128-135` documents (in a code comment only) that the run-filter dropdown filters **only the trades table**, not the KPI row/charts/report. Users can't see code comments — they select "Run #2" and reasonably believe the KPIs above now describe Run #2. Make the limitation visible in the UI and record the real fix in the backlog.

**Files:**
- Modify: `dashboard/src/pages/DeploymentDetail.tsx:271-297`
- Modify: `docs/superpowers/backlog.md`
- Test (extend or create): `dashboard/src/pages/DeploymentDetail.filter.test.tsx`

- [ ] **Step 1: Write the failing test**

If an existing DeploymentDetail test file exists (check `ls dashboard/src/pages/*.test.tsx dashboard/src/pages/__tests__ 2>/dev/null`), add the assertion there reusing its render harness. Otherwise create `dashboard/src/pages/DeploymentDetail.filter.test.tsx` — mock every hook the page uses (read the page's import list and mock each of `../api/hooks` exports it consumes, following the pattern from Task 1's test):

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DeploymentDetail } from "./DeploymentDetail";

vi.mock("../api/hooks", async () => {
  const actual = await vi.importActual<object>("../api/hooks");
  return {
    ...actual,
    useDeployment: () => ({
      data: { id: "d1", algorithm_id: "algo-1", status: "stopped", account_id: "acct" },
      isLoading: false,
    }),
    useDeploymentReport: () => ({ data: null }),
    useDeploymentRuns: () => ({ data: [{ id: "r1", run_number: 1, status: "stopped", started_at: null, stopped_at: null }] }),
    useDeploymentTrades: () => ({ data: { items: [], total: 0 } }),
  };
});

describe("DeploymentDetail run filter", () => {
  it("tells the user the run filter only narrows the trades table", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/algorithms/algo-1/deployments/d1"]}>
          <Routes>
            <Route path="/algorithms/:id/deployments/:instanceId" element={<DeploymentDetail />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByText(/trades table only/i)).toBeInTheDocument();
  });
});
```

If the page imports hooks not covered above (it has several — delete mutations, stop/start, etc.), mock each with `() => ({ data: undefined, mutateAsync: vi.fn(), isPending: false })` until the render succeeds. The single behavioral assertion is the visible note.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/pages/DeploymentDetail.filter.test.tsx`
Expected: FAIL — text not found.

- [ ] **Step 3: Implement**

In `dashboard/src/pages/DeploymentDetail.tsx`, in the run-filter block (line 271), add a visible hint before the select:

```tsx
      <div className="flex items-center justify-end gap-2">
        <span className="text-xs text-gray-500">
          Filter applies to the trades table only — KPIs and charts show lifetime data
        </span>
        <select
```

(Keep the existing select unchanged; just change the wrapper from `flex justify-end` to `flex items-center justify-end gap-2` and insert the span.)

In `docs/superpowers/backlog.md` add:

```markdown
- **Per-run deployment reports** (deferred 2026-06-12, UX fixes): /api/deployments/:id/report
  does not accept a run_id filter, so the dashboard run-filter dropdown only narrows the
  trades table (now labeled as such). Real fix: backend report endpoint accepts ?run_id=,
  computing KPIs/charts from that run's fills only; then pass runFilter through in
  DeploymentDetail.tsx (see TODO(M6.4-known-limitation)).
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/pages/DeploymentDetail.filter.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/pages/DeploymentDetail.tsx dashboard/src/pages/DeploymentDetail.filter.test.tsx docs/superpowers/backlog.md
git commit -m "fix(dashboard): label deployment run filter as trades-table-only"
```

---

### Task 6: BacktestRunDetail — trades load-more (P1)

The trades table fetches a fixed `limit=500` (`BacktestRunDetail.tsx:43`) with no way to see the rest. The header already shows the true total (`Trades ({totalTrades})`). Add a "Load more" button that grows the limit.

**Files:**
- Modify: `dashboard/src/pages/BacktestRunDetail.tsx`
- Test (extend): `dashboard/src/pages/BacktestRunDetail.nav.test.tsx` (add a describe block; harness from Task 1 already exists there)

- [ ] **Step 1: Write the failing test**

In `dashboard/src/pages/BacktestRunDetail.nav.test.tsx`, the Task-1 mock returns `{ items: [], total: 0 }`. Make the trades mock dynamic so this task can vary it — replace the `useBacktestTrades` line of the existing mock with:

```tsx
    useBacktestTrades: (...args: unknown[]) => tradesMock(...args),
```

and above the `vi.mock` block add:

```tsx
const tradesMock = vi.fn(() => ({ data: { items: [], total: 0 } }));
```

Add a new describe block:

```tsx
import { fireEvent } from "@testing-library/react";

describe("BacktestRunDetail trades load-more", () => {
  it("requests a larger limit when Load more is clicked", () => {
    const trade = {
      timestamp: "2024-01-02T15:30:00Z", symbol: "SPY", side: "buy", quantity: 1,
      requested_price: 1, fill_price: 1, slippage_dollars: 0, fees: 0, realized_pnl: null,
    };
    tradesMock.mockReturnValue({
      data: { items: Array.from({ length: 500 }, () => trade), total: 1200 },
    });
    renderAt("/algorithms/algo-1/backtests/run-9", "/algorithms/:id/backtests/:runId");
    const btn = screen.getByRole("button", { name: /load more/i });
    fireEvent.click(btn);
    // hook called with limit 1000 after the click (args: runId, limit, offset, opts)
    const calls = tradesMock.mock.calls.map((c) => c[1]);
    expect(calls).toContain(1000);
  });

  it("hides Load more when all trades are shown", () => {
    tradesMock.mockReturnValue({ data: { items: [], total: 0 } });
    renderAt("/algorithms/algo-1/backtests/run-9", "/algorithms/:id/backtests/:runId");
    expect(screen.queryByRole("button", { name: /load more/i })).toBeNull();
  });
});
```

(Merge the `fireEvent` import into the file's existing `@testing-library/react` import.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/pages/BacktestRunDetail.nav.test.tsx`
Expected: new tests FAIL — no "Load more" button exists.

- [ ] **Step 3: Implement**

In `dashboard/src/pages/BacktestRunDetail.tsx`:

Add state next to `deleteOpen` (line 45):

```tsx
  const [tradesLimit, setTradesLimit] = useState(500);
```

Change line 43 to use it:

```tsx
  const { data: tradesData } = useBacktestTrades(runId, tradesLimit, 0, { refetchInterval: liveRefetch });
```

After the closing `</table>` and its wrapping `</div>` (`overflow-auto max-h-[800px]`, line 178), inside the trades card, add:

```tsx
          {trades.length < totalTrades && (
            <div className="px-3 py-2 border-t border-gray-800 text-center">
              <button
                onClick={() => setTradesLimit((l) => l + 500)}
                className="text-xs text-indigo-400 hover:text-indigo-300"
              >
                Load more ({trades.length} of {totalTrades} shown)
              </button>
            </div>
          )}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/pages/BacktestRunDetail.nav.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/pages/BacktestRunDetail.tsx dashboard/src/pages/BacktestRunDetail.nav.test.tsx
git commit -m "feat(dashboard): load-more pagination for backtest trades table"
```

---

### Task 7: AlgorithmHub / AlgorithmConfig — loading & not-found states (P1)

Both pages do `if (!algo) return null;` (`AlgorithmHub.tsx:20`, `AlgorithmConfig.tsx:13`) — a blank content pane while loading and forever if the fetch fails. The parent `AlgorithmShell` has a spinner, but these pages also fetch independently and can render blank. Use `isLoading` from `useAlgorithm`.

**Files:**
- Modify: `dashboard/src/pages/AlgorithmHub.tsx:9-20`
- Modify: `dashboard/src/pages/AlgorithmConfig.tsx:8-13`
- Test (create): `dashboard/src/pages/AlgorithmConfig.loading.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `dashboard/src/pages/AlgorithmConfig.loading.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AlgorithmConfig } from "./AlgorithmConfig";

const algoMock = vi.fn<[], { data: unknown; isLoading: boolean }>(
  () => ({ data: undefined, isLoading: true }),
);
vi.mock("../api/hooks", async () => {
  const actual = await vi.importActual<object>("../api/hooks");
  return {
    ...actual,
    useAlgorithm: () => algoMock(),
    useParameterSets: () => ({ data: [] }),
    useCreateParameterSet: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useDeleteParameterSet: () => ({ mutateAsync: vi.fn() }),
    useImportParameterSets: () => ({ mutateAsync: vi.fn() }),
  };
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/algorithms/algo-1/config"]}>
        <Routes>
          <Route path="/algorithms/:id/config" element={<AlgorithmConfig />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AlgorithmConfig loading states", () => {
  it("shows a loading indicator while the algorithm is fetching", () => {
    algoMock.mockReturnValue({ data: undefined, isLoading: true });
    renderPage();
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("shows not-found when the fetch settles empty", () => {
    algoMock.mockReturnValue({ data: undefined, isLoading: false });
    renderPage();
    expect(screen.getByText(/not found/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/pages/AlgorithmConfig.loading.test.tsx`
Expected: FAIL — page renders null in both cases.

- [ ] **Step 3: Implement**

In `dashboard/src/pages/AlgorithmConfig.tsx`, change lines 9 and 13:

```tsx
  const { data: algo, isLoading } = useAlgorithm(id);
```

```tsx
  if (isLoading) return <p className="text-sm text-gray-400">Loading…</p>;
  if (!algo) return <p className="text-sm text-gray-400">Algorithm not found.</p>;
```

In `dashboard/src/pages/AlgorithmHub.tsx`, change lines 10 and 20 identically:

```tsx
  const { data: algo, isLoading } = useAlgorithm(id);
```

```tsx
  if (isLoading) return <p className="text-sm text-gray-400">Loading…</p>;
  if (!algo) return <p className="text-sm text-gray-400">Algorithm not found.</p>;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/pages/AlgorithmConfig.loading.test.tsx src/pages/AlgorithmHub.test.tsx && npm run typecheck`
Expected: PASS (AlgorithmHub.test.tsx exists — if its mocks return `{ data }` without `isLoading`, `isLoading` will be undefined → falls through to data check, still fine).

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/pages/AlgorithmHub.tsx dashboard/src/pages/AlgorithmConfig.tsx dashboard/src/pages/AlgorithmConfig.loading.test.tsx
git commit -m "fix(dashboard): loading and not-found states for algorithm hub/config pages"
```

---

### Task 8: Consistent date & number formatting (P1)

Raw ISO timestamps and ad-hoc `toFixed` calls are scattered through the algorithm section: `AlgorithmHub.tsx:60-62` (`sharpe_ratio?.toFixed(2)`, `(cagr*100).toFixed(1)%`, raw `completed_at`), `Research.tsx:77` (raw `created_at`), `ResearchJobRow.tsx:84,90` (raw `started_at`/`completed_at`). Centralize in `lib/formatNumbers.ts`.

**Files:**
- Modify: `dashboard/src/lib/formatNumbers.ts`
- Modify: `dashboard/src/pages/AlgorithmHub.tsx:60-62`
- Modify: `dashboard/src/pages/Research.tsx:77`
- Modify: `dashboard/src/components/ResearchJobRow.tsx:84,90`
- Test (create): `dashboard/src/lib/formatNumbers.test.ts`

- [ ] **Step 1: Write the failing test**

Create `dashboard/src/lib/formatNumbers.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { fmtDate, fmtDateTime, fmtPct, fmtNum } from "./formatNumbers";

describe("date formatters", () => {
  it("fmtDate renders a locale date for ISO input", () => {
    const iso = "2026-03-05T10:00:00Z";
    expect(fmtDate(iso)).toBe(new Date(iso).toLocaleDateString());
  });
  it("fmtDateTime renders a locale datetime for ISO input", () => {
    const iso = "2026-03-05T10:00:00Z";
    expect(fmtDateTime(iso)).toBe(new Date(iso).toLocaleString());
  });
  it("both return em-dash for null/undefined/empty", () => {
    expect(fmtDate(null)).toBe("—");
    expect(fmtDate(undefined)).toBe("—");
    expect(fmtDate("")).toBe("—");
    expect(fmtDateTime(null)).toBe("—");
  });
  it("both pass through unparseable strings", () => {
    expect(fmtDate("not-a-date")).toBe("not-a-date");
  });
});

describe("existing formatters keep behavior", () => {
  it("fmtPct", () => expect(fmtPct(0.1234)).toBe("12.34%"));
  it("fmtNum", () => expect(fmtNum(1.005, 2)).toBe("1.01"));
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/lib/formatNumbers.test.ts`
Expected: FAIL — `fmtDate`/`fmtDateTime` not exported.

- [ ] **Step 3: Implement the helpers**

Append to `dashboard/src/lib/formatNumbers.ts`:

```ts
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString();
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/lib/formatNumbers.test.ts`
Expected: PASS.

- [ ] **Step 5: Apply at call sites**

`dashboard/src/pages/AlgorithmHub.tsx` — add import:

```tsx
import { fmtPct, fmtNum, fmtDate } from "../lib/formatNumbers";
```

Replace lines 60-62:

```tsx
                  <td className="px-2 py-1 text-gray-400">{fmtNum(r.sharpe_ratio)}</td>
                  <td className="px-2 py-1 text-gray-400">{fmtPct(r.cagr)}</td>
                  <td className="px-2 py-1 text-gray-500">{fmtDate(r.completed_at)}</td>
```

`dashboard/src/pages/Research.tsx` — add import `import { fmtDate } from "../lib/formatNumbers";` and change line 77:

```tsx
                  <td className="px-4 py-2 text-gray-500">{fmtDate(s.created_at)}</td>
```

`dashboard/src/components/ResearchJobRow.tsx` — add import `import { fmtDateTime } from "../lib/formatNumbers";` and change the expanded started/completed values (lines 84 and 90):

```tsx
              <span className="text-gray-300">{fmtDateTime(job.started_at)}</span>
```

```tsx
              <span className="text-gray-300">{fmtDateTime(job.completed_at)}</span>
```

- [ ] **Step 6: Run affected suites and typecheck**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src && npm run typecheck`
Expected: PASS. If any existing test asserted the raw ISO string (e.g. Research.test.tsx matching `created_at` text), update that assertion to the formatted value — formatting is the intended behavior change.

- [ ] **Step 7: Commit**

```bash
git add dashboard/src/lib/formatNumbers.ts dashboard/src/lib/formatNumbers.test.ts dashboard/src/pages/AlgorithmHub.tsx dashboard/src/pages/Research.tsx dashboard/src/components/ResearchJobRow.tsx
git commit -m "feat(dashboard): shared date formatters; format dates/numbers in algorithm section"
```

---

### Task 9: Research sessions list — status badge + actionable error (P1)

`Research.tsx:72` renders session status as plain text while every other list uses colored badges, and line 31 shows a generic "Failed to load sessions" hiding the actual error.

**Files:**
- Modify: `dashboard/src/pages/Research.tsx`
- Test (extend): `dashboard/src/pages/Research.test.tsx` (or create `dashboard/src/pages/Research.badge.test.tsx` if extending is awkward)

- [ ] **Step 1: Write the failing test**

Create `dashboard/src/pages/Research.badge.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Research } from "./Research";

const sessionsMock = vi.fn();
vi.mock("../hooks/useResearchSessions", () => ({
  useResearchSessions: () => sessionsMock(),
}));
vi.mock("../api/hooks", async () => {
  const actual = await vi.importActual<object>("../api/hooks");
  return { ...actual, useAlgorithms: () => ({ data: [] }) };
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Research />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Research sessions list", () => {
  it("renders the session status as a colored badge", () => {
    sessionsMock.mockReturnValue({
      isLoading: false, error: null,
      data: [{
        id: 1, name: "s1", hypothesis: "h", status: "running", notes: "",
        created_at: "2026-01-01T00:00:00Z", completed_at: null,
        algorithm_id: "algo-1", base_config: {}, parameter_space: {},
        pre_registered_criteria: {}, n_runs: 0,
        date_range_start: "2024-01-01", date_range_end: "2024-12-31",
        initial_cash: 10000, cost_profile: "default",
        benchmark_symbol: null, benchmark_source: null,
      }],
    });
    renderPage();
    const badge = screen.getByText("running");
    expect(badge.className).toContain("rounded");
    expect(badge.className).toContain("bg-blue-700");
  });

  it("surfaces the error message when loading fails", () => {
    sessionsMock.mockReturnValue({
      isLoading: false, error: new Error("ECONNREFUSED"), data: undefined,
    });
    renderPage();
    expect(screen.getByText(/ECONNREFUSED/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/pages/Research.badge.test.tsx`
Expected: FAIL — status is a bare `<td>` text node, error text is generic.

- [ ] **Step 3: Implement**

In `dashboard/src/pages/Research.tsx`:

Add at module level (below imports):

```tsx
import type { ResearchSession } from "../api/client";

const SESSION_STATUS_COLORS: Record<ResearchSession["status"], string> = {
  open:      "bg-gray-700 text-gray-300",
  running:   "bg-blue-700 text-blue-100",
  completed: "bg-green-700 text-green-100",
  failed:    "bg-red-700 text-red-100",
};
```

Replace line 31:

```tsx
      {q.error && (
        <div className="text-red-400 text-sm">
          Failed to load sessions{q.error instanceof Error ? `: ${q.error.message}` : ""}
        </div>
      )}
```

Replace line 72 (`<td className="px-4 py-2">{s.status}</td>`):

```tsx
                  <td className="px-4 py-2">
                    <span className={`text-xs font-medium px-2 py-0.5 rounded ${SESSION_STATUS_COLORS[s.status] ?? "bg-gray-700 text-gray-300"}`}>
                      {s.status}
                    </span>
                  </td>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/pages/Research.badge.test.tsx src/pages/Research.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/pages/Research.tsx dashboard/src/pages/Research.badge.test.tsx
git commit -m "feat(dashboard): status badges and detailed errors on research sessions list"
```

---

### Task 10: AlgorithmHub — actionable empty states (P1)

`AlgorithmHub.tsx` empty states are dead ends: "No backtests yet." (line 48), "No active research sessions." (line 78), "No deployments. Deploy this algorithm to start trading." (lines 109-111 — instructs an action the UI doesn't offer). Give each a real next step.

**Files:**
- Modify: `dashboard/src/pages/AlgorithmHub.tsx:47-48,77-78,108-111`
- Test (extend): `dashboard/src/pages/AlgorithmHub.test.tsx`

- [ ] **Step 1: Write the failing test**

Add to `dashboard/src/pages/AlgorithmHub.test.tsx` (reuse its existing render harness/mocks; make sure the mocks return empty arrays for runs/sessions/deployments for these cases):

```tsx
  it("offers a Run Backtest action in the empty backtests state", () => {
    renderHub(); // existing helper with empty data mocks
    expect(
      screen.getByRole("button", { name: /run your first backtest/i }),
    ).toBeInTheDocument();
  });

  it("links to the research tab from the empty research state", () => {
    renderHub();
    expect(
      screen.getByRole("link", { name: /start a research session/i }),
    ).toBeInTheDocument();
  });

  it("does not instruct an unavailable deploy action", () => {
    renderHub();
    expect(screen.queryByText(/deploy this algorithm to start trading/i)).toBeNull();
  });
```

If the existing harness pre-seeds non-empty data, add a local render variant with empty mocks (follow the file's own mocking pattern).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/pages/AlgorithmHub.test.tsx`
Expected: the 3 new tests FAIL.

- [ ] **Step 3: Implement**

In `dashboard/src/pages/AlgorithmHub.tsx`:

Replace line 48 (`<p className="text-sm text-gray-500">No backtests yet.</p>`):

```tsx
          <p className="text-sm text-gray-500">
            No backtests yet.{" "}
            <button
              onClick={() => setRunBacktestOpen(true)}
              className="text-indigo-400 hover:text-indigo-300"
            >
              Run your first backtest →
            </button>
          </p>
```

Replace line 78 (`<p className="text-sm text-gray-500">No active research sessions.</p>`):

```tsx
          <p className="text-sm text-gray-500">
            No active research sessions.{" "}
            <Link to={`/algorithms/${id}/research`} className="text-indigo-400 hover:text-indigo-300">
              Start a research session →
            </Link>
          </p>
```

Replace lines 108-111 (the deployments empty state):

```tsx
          <p className="text-sm text-gray-500">
            No deployments yet.{" "}
            <Link to={`/algorithms/${id}/deployments`} className="text-indigo-400 hover:text-indigo-300">
              View deployments →
            </Link>
          </p>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/pages/AlgorithmHub.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/pages/AlgorithmHub.tsx dashboard/src/pages/AlgorithmHub.test.tsx
git commit -m "feat(dashboard): actionable empty states on algorithm hub"
```

---

### Task 11: Research jobs — serialize request_payload & result; show job identity (P2, backend + frontend)

Two backend serialization gaps in the research jobs API:
1. `ResearchJob.request_payload` (stored at `coordinator/database/models.py:514`, e.g. `{"search": "grid", "max_trials": 50, ...}`) is never returned — `_row_to_dict` (`coordinator/services/research_job_manager.py:372-386`) omits it and `JobResponse` (`coordinator/api/routes/research.py:151-162`) has no field. The dashboard job rows therefore show only "sweep / completed" with no way to tell two sweeps apart.
2. `_row_to_dict` includes `"result"` but `JobResponse` has no `result` field — pydantic silently drops it, so `job.result` is always undefined in the dashboard and `CpcvResultsPanel` (which reads `job.result`) can never show completed CPCV results from the API. This is a live bug, not just polish.

Then render the request params in `ResearchJobRow`'s expanded section.

**Files:**
- Modify: `coordinator/services/research_job_manager.py:372-386`
- Modify: `coordinator/api/routes/research.py:151-162`
- Modify: `dashboard/src/api/client.ts:241-254`
- Modify: `dashboard/src/components/ResearchJobRow.tsx` (expanded section)
- Test: `tests/coordinator/api/test_research_jobs_endpoints.py` (extend)
- Test (create): `dashboard/src/components/ResearchJobRow.payload.test.tsx`

- [ ] **Step 1: Write the failing backend test**

Append to `tests/coordinator/api/test_research_jobs_endpoints.py` (fixtures `_seed_session`/`test_app` already exist in this file):

```python
@pytest.mark.asyncio
async def test_get_job_returns_request_payload_and_result(test_app):
    """GET /jobs/{id} must serialize request_payload and result so the
    dashboard can identify jobs and render CPCV results."""
    from coordinator.api.dependencies import get_container

    container = get_container()
    session_id = await _seed_session(container)
    async with container.session_factory() as s:
        s.add(ResearchJob(
            id="jp", session_id=session_id, kind="sweep", status="completed",
            request_payload={"search": "grid", "max_trials": 50},
            result={"best_objective": 1.23},
            run_ids=[],
        ))
        await s.commit()

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        r = await ac.get(f"/api/research/sessions/{session_id}/jobs/jp")
    assert r.status_code == 200
    body = r.json()
    assert body["request_payload"] == {"search": "grid", "max_trials": 50}
    assert body["result"] == {"best_objective": 1.23}
```

- [ ] **Step 2: Run backend test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && .venv/bin/pytest tests/coordinator/api/test_research_jobs_endpoints.py::test_get_job_returns_request_payload_and_result -v`
Expected: FAIL — `request_payload` (and `result`) missing from response body.

- [ ] **Step 3: Implement backend**

In `coordinator/services/research_job_manager.py`, `_row_to_dict` (line 372), add after `"run_ids"`:

```python
        "request_payload": row.request_payload,
```

In `coordinator/api/routes/research.py`, `JobResponse` (line 151), add after `run_ids`:

```python
    request_payload: dict | None = None
    result: Any = None
```

(`Any` is already imported at the top of research.py.)

- [ ] **Step 4: Run backend tests**

Run: `cd /home/jkern/dev/quilt-trader && .venv/bin/pytest tests/coordinator/api/test_research_jobs_endpoints.py tests/coordinator/api/test_research_routes.py tests/coordinator/services/test_research_job_manager.py -v`
Expected: all PASS (new test green; no regressions in the touched modules).

- [ ] **Step 5: Write the failing frontend test**

Create `dashboard/src/components/ResearchJobRow.payload.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ResearchJobRow } from "./ResearchJobRow";
import type { ResearchJob } from "../api/client";

const job: ResearchJob = {
  job_id: "j1", session_id: 1, kind: "sweep", status: "completed",
  progress_pct: 1, progress_message: null, run_ids: [],
  error_message: null, started_at: null, completed_at: null, created_at: null,
  request_payload: { search: "grid", max_trials: 50, manifest_path: "/x/quilt.yaml" },
};

describe("ResearchJobRow request payload", () => {
  it("shows request params (minus manifest_path) in the expanded section", () => {
    render(
      <MemoryRouter>
        <ResearchJobRow job={job} onCancel={() => {}} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText("sweep"));
    expect(screen.getByText(/search=.?grid/)).toBeInTheDocument();
    expect(screen.getByText(/max_trials=50/)).toBeInTheDocument();
    expect(screen.queryByText(/manifest_path/)).toBeNull();
  });
});
```

- [ ] **Step 6: Run frontend test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/components/ResearchJobRow.payload.test.tsx`
Expected: FAIL — first as a type error (`request_payload` not on `ResearchJob`), then missing render.

- [ ] **Step 7: Implement frontend**

In `dashboard/src/api/client.ts`, add to the `ResearchJob` interface (after `created_at`):

```ts
  request_payload?: Record<string, unknown> | null;
```

In `dashboard/src/components/ResearchJobRow.tsx`, in the expanded section after the `job_id` block (line 80), add:

```tsx
          {job.request_payload && Object.keys(job.request_payload).length > 0 && (
            <div>
              <span className="text-gray-500">request:</span>{" "}
              <code className="text-gray-300 break-all">
                {Object.entries(job.request_payload)
                  .filter(([key]) => key !== "manifest_path")
                  .map(([key, v]) => `${key}=${JSON.stringify(v)}`)
                  .join("  ")}
              </code>
            </div>
          )}
```

- [ ] **Step 8: Run frontend tests**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/components/ResearchJobRow.payload.test.tsx && npm run typecheck`
Expected: PASS. (Note: `JSON.stringify("grid")` renders `"grid"` with quotes — the test regex `search=.?grid` allows it.)

- [ ] **Step 9: Commit**

```bash
git add coordinator/services/research_job_manager.py coordinator/api/routes/research.py tests/coordinator/api/test_research_jobs_endpoints.py dashboard/src/api/client.ts dashboard/src/components/ResearchJobRow.tsx dashboard/src/components/ResearchJobRow.payload.test.tsx
git commit -m "fix(research): serialize job request_payload and result; show job identity in dashboard"
```

---

### Task 12: Cost-profile dropdown backed by a real endpoint (P2)

The new-session form's cost profile is a free-text input (`ExperimentScopeFields.tsx:102-106`) — a typo like `defualt` passes client validation and only fails at backtest time (`load_named_profile` raises FileNotFoundError, `cost_model.py:46-50`). Profiles are YAML files in `coordinator/services/validation/cost_profiles/` (currently just `default.yaml`). Add `GET /api/research/cost-profiles` and turn the input into a select.

**Files:**
- Modify: `coordinator/services/validation/cost_model.py`
- Modify: `coordinator/api/routes/research.py`
- Modify: `dashboard/src/api/client.ts`
- Modify: `dashboard/src/api/hooks.ts`
- Modify: `dashboard/src/components/ExperimentScopeFields.tsx:98-107`
- Test: `tests/coordinator/api/test_research_routes.py` (extend)
- Test: `dashboard/src/components/ExperimentScopeFields.test.tsx` (extend; wrap renders in QueryClientProvider)

- [ ] **Step 1: Write the failing backend test**

Append to `tests/coordinator/api/test_research_routes.py` (follow that file's existing client fixture pattern; if it uses `test_app` + AsyncClient like test_research_jobs_endpoints.py, mirror this):

```python
@pytest.mark.asyncio
async def test_list_cost_profiles_returns_yaml_names(test_app):
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        r = await ac.get("/api/research/cost-profiles")
    assert r.status_code == 200
    names = r.json()
    assert isinstance(names, list)
    assert "default" in names
```

- [ ] **Step 2: Run backend test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && .venv/bin/pytest tests/coordinator/api/test_research_routes.py -v -k cost_profiles`
Expected: FAIL with 404 (route doesn't exist).

- [ ] **Step 3: Implement backend**

In `coordinator/services/validation/cost_model.py`, append:

```python
def list_profile_names() -> list[str]:
    return sorted(p.stem for p in _PROFILES_DIR.glob("*.yaml"))
```

In `coordinator/api/routes/research.py`, add the import near the other validation imports (top of file):

```python
from coordinator.services.validation.cost_model import list_profile_names
```

Add the route (place it ABOVE the `/sessions/...` routes or anywhere — the path doesn't collide):

```python
@router.get("/cost-profiles", response_model=list[str])
async def list_cost_profiles() -> list[str]:
    """Names of available cost-model profiles (YAML files on disk)."""
    return list_profile_names()
```

- [ ] **Step 4: Run backend tests**

Run: `cd /home/jkern/dev/quilt-trader && .venv/bin/pytest tests/coordinator/api/test_research_routes.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing frontend test**

Add to `dashboard/src/components/ExperimentScopeFields.test.tsx`:

```tsx
  it("renders cost profile as a select fed by the cost-profiles endpoint", () => {
    // (use this file's existing render/props helper)
    renderFields(); // must now be wrapped in QueryClientProvider — see step 6
    const select = screen.getByLabelText(/cost profile/i);
    expect(select.tagName).toBe("SELECT");
  });
```

And mock the hook at the top of the file:

```tsx
vi.mock("../api/hooks", async () => {
  const actual = await vi.importActual<object>("../api/hooks");
  return { ...actual, useCostProfiles: () => ({ data: ["default", "ibkr"] }) };
});
```

- [ ] **Step 6: Run frontend test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/components/ExperimentScopeFields.test.tsx`
Expected: new test FAILS (`tagName` is `INPUT`).

- [ ] **Step 7: Implement frontend**

`dashboard/src/api/client.ts` — add inside the `api` object (next to the other research functions, e.g. after `createCpcvJob`):

```ts
  listCostProfiles(): Promise<string[]> {
    return request<string[]>("/api/research/cost-profiles");
  },
```

`dashboard/src/api/hooks.ts` — add:

```ts
export function useCostProfiles() {
  return useQuery({
    queryKey: ["cost-profiles"] as const,
    queryFn: () => api.listCostProfiles(),
    staleTime: 5 * 60_000,
  });
}
```

`dashboard/src/components/ExperimentScopeFields.tsx` — add import:

```tsx
import { useCostProfiles } from "../api/hooks";
```

Inside the component (after the destructuring), add:

```tsx
  const { data: costProfiles } = useCostProfiles();
  const profileOptions = (() => {
    const base = costProfiles && costProfiles.length > 0 ? costProfiles : ["default"];
    return costProfile && !base.includes(costProfile) ? [costProfile, ...base] : base;
  })();
```

Replace the cost-profile input (lines 102-106) with:

```tsx
          <select
            id="sf-cost" value={costProfile} disabled={disabled}
            onChange={(e) => emit({ costProfile: e.target.value })}
            className={input}
          >
            {profileOptions.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
```

Because the component now uses a query hook, any test rendering `ExperimentScopeFields` (including via `NewSessionModal`) must run inside a `QueryClientProvider`. The hook mock from step 5 sidesteps this for the scope-fields file; if `NewSessionModal.test.tsx` breaks, either add the same `useCostProfiles` mock there or wrap its render in:

```tsx
const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
render(<QueryClientProvider client={qc}>…</QueryClientProvider>);
```

- [ ] **Step 8: Run frontend tests**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/components/ExperimentScopeFields.test.tsx src/components/NewSessionModal.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add coordinator/services/validation/cost_model.py coordinator/api/routes/research.py tests/coordinator/api/test_research_routes.py dashboard/src/api/client.ts dashboard/src/api/hooks.ts dashboard/src/components/ExperimentScopeFields.tsx dashboard/src/components/ExperimentScopeFields.test.tsx
git commit -m "feat(research): cost-profiles endpoint + dropdown in session scope fields"
```

---

### Task 13: NewSessionModal — don't discard a dirty form on backdrop click (P2)

`NewSessionModal.tsx:78`: clicking the dark backdrop calls `onClose()` unconditionally — one stray click discards a fully-typed hypothesis, JSON config, and scope. Guard: ignore backdrop clicks once the form is dirty (explicit Cancel/X still close).

**Files:**
- Modify: `dashboard/src/components/NewSessionModal.tsx`
- Test (extend): `dashboard/src/components/NewSessionModal.test.tsx` (or create `NewSessionModal.dirty.test.tsx` with the mocks below)

- [ ] **Step 1: Write the failing test**

Create `dashboard/src/components/NewSessionModal.dirty.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { NewSessionModal } from "./NewSessionModal";

vi.mock("../api/hooks", async () => {
  const actual = await vi.importActual<object>("../api/hooks");
  return {
    ...actual,
    useAlgorithms: () => ({ data: [] }),
    useCostProfiles: () => ({ data: ["default"] }),
  };
});
vi.mock("../hooks/useResearchMutations", () => ({
  useCreateResearchSession: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

function renderModal(onClose: () => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <NewSessionModal open onClose={onClose} onCreated={() => {}} />
    </QueryClientProvider>,
  );
}

function backdrop(container: HTMLElement): HTMLElement {
  return container.querySelector('[aria-hidden="true"]') as HTMLElement;
}

describe("NewSessionModal backdrop guard", () => {
  it("closes on backdrop click while the form is pristine", () => {
    const onClose = vi.fn();
    const { container } = renderModal(onClose);
    fireEvent.click(backdrop(container));
    expect(onClose).toHaveBeenCalled();
  });

  it("ignores backdrop clicks once the form is dirty", () => {
    const onClose = vi.fn();
    const { container } = renderModal(onClose);
    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: "my session" } });
    fireEvent.click(backdrop(container));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("the Cancel button still closes a dirty form", () => {
    const onClose = vi.fn();
    renderModal(onClose);
    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: "my session" } });
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClose).toHaveBeenCalled();
  });
});
```

(If Task 12 is not yet merged when this runs, drop the `useCostProfiles` line from the mock.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/components/NewSessionModal.dirty.test.tsx`
Expected: the "ignores backdrop clicks" test FAILS (onClose is called).

- [ ] **Step 3: Implement**

In `dashboard/src/components/NewSessionModal.tsx`, after the `canSubmit` computation (line 55), add:

```tsx
  const dirty =
    name.trim() !== "" ||
    algorithmId !== "" ||
    hypothesis.trim() !== "" ||
    notes.trim() !== "";
```

Change the backdrop div (line 78):

```tsx
      <div
        className="absolute inset-0 bg-black/70"
        onClick={() => { if (!dirty) onClose(); }}
        aria-hidden="true"
      />
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/components/NewSessionModal.dirty.test.tsx src/components/NewSessionModal.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/NewSessionModal.tsx dashboard/src/components/NewSessionModal.dirty.test.tsx
git commit -m "fix(dashboard): don't discard dirty new-session form on backdrop click"
```

---

## Final verification (after all tasks)

- [ ] Full dashboard suite: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run && npm run typecheck` — expected: all green.
- [ ] Touched backend suites: `cd /home/jkern/dev/quilt-trader && .venv/bin/pytest tests/coordinator/api/test_research_jobs_endpoints.py tests/coordinator/api/test_research_routes.py tests/coordinator/services/test_research_job_manager.py -v` — expected: all green.
- [ ] Manual smoke (golden path): `cd dashboard && npm run dev`, open http://localhost:3000/algorithms → pick an algorithm → Hub (loading state, formatted numbers, empty-state CTAs) → Config (no Deploy button, best-set highlight) → run detail (back link stays scoped, Load more) → research session (back link, job request params, CPCV modal invalid-JSON disables submit, sweep success toast) → deployment detail (run-filter note visible).

## Coverage map (review item → task)

| Review item | Task |
|---|---|
| P0-1 dead Deploy button | 4 |
| P0-2 navigation escapes algo scope | 1 |
| P0-3 CPCV modal swallows JSON errors / silent submit | 2 |
| P0-4 run filter silently partial | 5 |
| P1-5 no success feedback on sweep/CPCV submit | 2, 3 |
| P1-6 trades capped at 500 | 6 |
| P1-7 blank loading states | 7 |
| P1-8 raw dates / inconsistent numbers | 8 |
| P1-9 plain-text session status, generic error | 9 |
| P1-10 dead-end empty states | 10 |
| P2-11 jobs lack identity (request_payload) + result never serialized | 11 |
| P2-12 cost profile free-text | 12 |
| P2-13 isBest = first row | 4 |
| P2-14 backdrop click discards form | 13 |
| P2-15 date pickers (found already implemented — folded into 12) | 12 |
