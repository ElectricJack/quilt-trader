import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
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
vi.mock("../components/NewSweepModal", () => ({ NewSweepModal: () => null }));
vi.mock("../components/NewCpcvModal", () => ({ NewCpcvModal: () => null }));
vi.mock("../components/cpcv/CpcvResultsPanel", () => ({ CpcvResultsPanel: () => null }));

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
    fireEvent.click(screen.getByText("sweep"));
    expect(screen.getByRole("link", { name: "run-42" }))
      .toHaveAttribute("href", "/algorithms/algo-1/backtests/run-42");
  });
});
