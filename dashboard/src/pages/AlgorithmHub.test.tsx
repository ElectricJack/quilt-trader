import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AlgorithmHub } from "./AlgorithmHub";

const algo = {
  id: "a-1", name: "crypto-tsmom", version: "1.4.2",
  summary: {
    status: "live", headline_sharpe: 1.4, headline_sharpe_source: "live_30d",
    counts: { deployments: 1, backtests: 3, research_sessions: 1 },
    equity_sparkline: [], last_activity_at: null,
  },
};
const recentRuns = [
  { id: "r-1", name: "sweep-1.1", status: "completed", sharpe_ratio: 1.2, cagr: 0.15, completed_at: "2026-06-09" },
  { id: "r-2", name: "sweep-1.2", status: "running", sharpe_ratio: null, cagr: null, completed_at: null },
];
const sessions = [
  { id: 7, name: "vol-target-sweep", kind: "sweep", status: "open", progress: 0.5 },
];

let mockRuns: any[] = recentRuns;
let mockSessions: any[] = sessions;
let mockDeployments: any[] = [];

vi.mock("../api/hooks", async () => {
  const actual = await vi.importActual<any>("../api/hooks");
  return {
    ...actual,
    useAlgorithm: () => ({ data: algo }),
    useBacktestRuns: (_params: any) => ({ data: mockRuns, isLoading: false }),
    useDeployments: (_params: any) => ({ data: mockDeployments, isLoading: false }),
  };
});

vi.mock("../hooks/useResearchSessions", () => ({
  useResearchSessions: (_params: any) => ({ data: mockSessions, isLoading: false }),
}));

function renderAt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/algorithms/a-1"]}>
        <Routes>
          <Route path="/algorithms/:id" element={<AlgorithmHub />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderHub() {
  mockRuns = [];
  mockSessions = [];
  mockDeployments = [];
  return renderAt();
}

describe("AlgorithmHub – recent backtests", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRuns = recentRuns;
    mockSessions = sessions;
    mockDeployments = [];
  });

  it("renders up to 5 recent backtests with link to view all", () => {
    renderAt();
    expect(screen.getByText("sweep-1.1")).toBeInTheDocument();
    expect(screen.getByText("sweep-1.2")).toBeInTheDocument();
    const viewAll = screen.getByRole("link", { name: /view all 3/i });
    expect(viewAll.getAttribute("href")).toBe("/algorithms/a-1/backtests");
  });

  it("renders active research session with progress", () => {
    renderAt();
    expect(screen.getByText("vol-target-sweep")).toBeInTheDocument();
    expect(screen.getByText(/50%|0\.5/)).toBeInTheDocument();
  });
});

describe("AlgorithmHub – empty states", () => {
  it("offers a Run Backtest action in the empty backtests state", () => {
    renderHub();
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
});
