import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AlgorithmsGrid } from "./AlgorithmsGrid";

const algos = [
  {
    id: "a-1", name: "crypto-tsmom", version: "1.4.2",
    summary: {
      status: "live", status_source: null,
      headline_sharpe: 1.4, headline_sharpe_source: "live_30d",
      equity_sparkline: [1, 2, 3], equity_sparkline_source: "live",
      counts: { deployments: 3, backtests: 47, research_sessions: 2 },
      last_activity_at: "2026-06-10T11:00:00Z",
    },
  },
  {
    id: "a-2", name: "alpha-picks", version: "2.1.0",
    summary: {
      status: "idle", status_source: null,
      headline_sharpe: 0.8, headline_sharpe_source: "last_backtest",
      equity_sparkline: [], equity_sparkline_source: null,
      counts: { deployments: 0, backtests: 8, research_sessions: 0 },
      last_activity_at: "2026-06-05T00:00:00Z",
    },
  },
  {
    id: "a-3", name: "zeta-mean-revert", version: "0.3.1",
    summary: {
      status: "idle", status_source: null,
      headline_sharpe: 0.4, headline_sharpe_source: "last_backtest",
      equity_sparkline: [], equity_sparkline_source: null,
      counts: { deployments: 0, backtests: 3, research_sessions: 0 },
      last_activity_at: "2026-06-01T00:00:00Z",
    },
  },
];

vi.mock("../api/hooks", async () => {
  const actual = await vi.importActual<any>("../api/hooks");
  return { ...actual, useAlgorithms: () => ({ data: algos, isLoading: false }) };
});

function renderGrid() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AlgorithmsGrid />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AlgorithmsGrid", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders one card per algorithm", () => {
    renderGrid();
    expect(screen.getByText("crypto-tsmom")).toBeInTheDocument();
    expect(screen.getByText("alpha-picks")).toBeInTheDocument();
  });

  it("filters by name when the search input changes", () => {
    renderGrid();
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: "alpha" } });
    expect(screen.queryByText("crypto-tsmom")).not.toBeInTheDocument();
    expect(screen.getByText("alpha-picks")).toBeInTheDocument();
  });

  it("sorts by name within each group when selected", () => {
    renderGrid();
    fireEvent.change(screen.getByLabelText(/sort/i), { target: { value: "name" } });
    const allHeading = screen.getByRole("heading", { name: /all algorithms/i });
    const allSection = allHeading.parentElement as HTMLElement;
    const names = within(allSection).getAllByRole("link").map((n) => n.textContent ?? "");
    expect(names[0]).toContain("alpha-picks");
    expect(names[1]).toContain("zeta-mean-revert");
  });

  it("groups running algos in a Running section above the rest", () => {
    renderGrid();
    expect(screen.getByRole("heading", { name: /running/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /all algorithms/i })).toBeInTheDocument();
    const sections = screen.getAllByRole("heading");
    const runningIdx = sections.findIndex((h) => /running/i.test(h.textContent ?? ""));
    const allIdx = sections.findIndex((h) => /all algorithms/i.test(h.textContent ?? ""));
    expect(runningIdx).toBeLessThan(allIdx);
    expect(screen.getByText("crypto-tsmom")).toBeInTheDocument();
    expect(screen.getByText("alpha-picks")).toBeInTheDocument();
  });

  it("hides the Running heading when no algo is live or paper", () => {
    renderGrid();
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: "alpha" } });
    expect(screen.queryByRole("heading", { name: /running/i })).not.toBeInTheDocument();
    expect(screen.getByText("alpha-picks")).toBeInTheDocument();
  });
});
