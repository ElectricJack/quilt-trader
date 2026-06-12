import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BacktestRunDetail } from "./BacktestRunDetail";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const tradesMock = vi.fn((..._args: any[]) => ({ data: { items: [] as unknown[], total: 0 } }));

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
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    useBacktestTrades: (...args: any[]) => tradesMock(...args),
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

beforeEach(() => {
  tradesMock.mockReturnValue({ data: { items: [], total: 0 } });
});

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

describe("BacktestRunDetail trades load-more", () => {
  it("requests a larger limit when Load more is clicked", () => {
    const trade = {
      timestamp: "2024-01-02T15:30:00Z", symbol: "SPY", side: "buy", quantity: 1,
      requested_price: 1, fill_price: 1, slippage_dollars: 0, fees: 0, realized_pnl: null,
    };
    tradesMock.mockReturnValue({
      data: { items: Array.from({ length: 5 }, () => trade) as unknown[], total: 1200 },
    });
    renderAt("/algorithms/algo-1/backtests/run-9", "/algorithms/:id/backtests/:runId");
    const btn = screen.getByRole("button", { name: /load more/i });
    fireEvent.click(btn);
    // limit is the second arg (index 1) of useBacktestTrades(id, limit, offset, opts)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const calls = (tradesMock.mock.calls as any[][]).map((c) => c[1]);
    expect(calls).toContain(1000);
  });

  it("hides Load more when all trades are shown", () => {
    tradesMock.mockReturnValue({ data: { items: [], total: 0 } });
    renderAt("/algorithms/algo-1/backtests/run-9", "/algorithms/:id/backtests/:runId");
    expect(screen.queryByRole("button", { name: /load more/i })).toBeNull();
  });
});
