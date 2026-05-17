import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DeploymentDetail } from "./DeploymentDetail";

vi.mock("../api/hooks", () => ({
  useDeployment: () => ({
    data: {
      id: "d1",
      algorithm_id: "a1",
      account_id: "ac1",
      worker_id: "w1",
      algorithm_name: "TrendBot",
      account_name: "Paper",
      worker_name: "Pi-1",
      status: "running",
      active_run_id: "r1",
      config_values: {},
      lifetime_metrics: {},
      created_at: "2026-05-16T12:00:00Z",
      updated_at: "2026-05-16T12:00:00Z",
    },
    isLoading: false,
  }),
  useDeploymentReport: () => ({ data: null }),
  useDeploymentRuns: () => ({ data: [] }),
  useDeploymentTrades: () => ({ data: { items: [] } }),
  useStartDeployment: () => ({ mutate: vi.fn(), isPending: false }),
  useStopDeployment: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteDeployment: () => ({ mutate: vi.fn(), isPending: false }),
  useWorkerActivity: () => ({ data: null, isLoading: false }),
  useDeploymentActivity: () => ({ data: null, isLoading: false }),
}));

vi.mock("../stores/ui", () => ({
  useUIStore: (selector: (s: { addAlert: (a: unknown) => void }) => unknown) =>
    selector({ addAlert: vi.fn() }),
}));

describe("DeploymentDetail", () => {
  it("renders header with algorithm, account, worker names and a Stop button when running", () => {
    const qc = new QueryClient();
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/deployments/d1"]}>
          <Routes>
            <Route path="/deployments/:id" element={<DeploymentDetail />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getAllByText("TrendBot").length).toBeGreaterThan(0);
    expect(screen.getByText("Paper")).toBeInTheDocument();
    expect(screen.getByText("Pi-1")).toBeInTheDocument();
    expect(screen.getByText("Stop")).toBeInTheDocument();
  });
});
