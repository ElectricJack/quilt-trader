import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

const fetchSpy = vi.fn((_id: string) => ({ data: undefined, isLoading: false, error: null }));
vi.mock("../api/hooks", async () => {
  const actual = await vi.importActual<any>("../api/hooks");
  return {
    ...actual,
    useBacktestReport: (id: string) => {
      fetchSpy(id);
      return { data: undefined, isLoading: false, error: null };
    },
  };
});

import { BacktestRunDetail } from "./BacktestRunDetail";

function renderAt(path: string, routePattern: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={routePattern} element={<BacktestRunDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("BacktestRunDetail param resolution", () => {
  it("reads runId when nested under /algorithms/:id/backtests/:runId", () => {
    fetchSpy.mockClear();
    renderAt("/algorithms/algo-x/backtests/run-99", "/algorithms/:id/backtests/:runId");
    expect(fetchSpy).toHaveBeenCalledWith("run-99");
  });

  it("falls back to :id under legacy /backtest-runs/:id", () => {
    fetchSpy.mockClear();
    renderAt("/backtest-runs/run-42", "/backtest-runs/:id");
    expect(fetchSpy).toHaveBeenCalledWith("run-42");
  });
});
