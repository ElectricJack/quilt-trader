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
