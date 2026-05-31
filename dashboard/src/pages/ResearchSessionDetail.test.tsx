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
