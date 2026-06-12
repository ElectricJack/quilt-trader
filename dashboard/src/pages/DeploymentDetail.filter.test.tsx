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
    useStartDeployment: () => ({ mutate: vi.fn(), isPending: false }),
    useStopDeployment: () => ({ mutate: vi.fn(), isPending: false }),
    useDeleteDeployment: () => ({ mutate: vi.fn(), isPending: false }),
    useRedeployDeployment: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
    useWorkerActivity: () => ({ data: undefined, isLoading: false }),
    useDeploymentActivity: () => ({ data: undefined, isLoading: false }),
  };
});

vi.mock("../stores/ui", () => ({
  useUIStore: (selector: (s: { addAlert: (a: unknown) => void }) => unknown) =>
    selector({ addAlert: vi.fn() }),
}));

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

  it("still renders the run filter select alongside the scope note", () => {
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
    expect(screen.getByRole("combobox")).toBeInTheDocument();
  });
});
