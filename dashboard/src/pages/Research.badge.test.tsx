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
