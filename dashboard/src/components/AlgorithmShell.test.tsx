import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AlgorithmShell } from "./AlgorithmShell";

vi.mock("../api/hooks", () => ({
  useAlgorithm: (id: string) => {
    if (id === "missing") return { data: undefined, isLoading: false, error: new Error("404") };
    if (id === "loading") return { data: undefined, isLoading: true, error: null };
    return {
      data: {
        id,
        name: "crypto-tsmom",
        version: "1.4.2",
        summary: {
          status: "live",
          counts: { deployments: 3, backtests: 47, research_sessions: 2 },
        },
      },
      isLoading: false,
      error: null,
    };
  },
}));

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/algorithms/:id" element={<AlgorithmShell />}>
            <Route index element={<div>HUB CONTENT</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AlgorithmShell", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders the algorithm name and version in the header", () => {
    renderAt("/algorithms/abc");
    expect(screen.getByText("crypto-tsmom")).toBeInTheDocument();
    expect(screen.getByText(/v1\.4\.2/)).toBeInTheDocument();
  });

  it("renders the outlet content below the header", () => {
    renderAt("/algorithms/abc");
    expect(screen.getByText("HUB CONTENT")).toBeInTheDocument();
  });

  it("renders rollup counts in the header subline", () => {
    renderAt("/algorithms/abc");
    expect(screen.getByText(/3 deployments/)).toBeInTheDocument();
    expect(screen.getByText(/47 backtests/)).toBeInTheDocument();
    expect(screen.getByText(/2 research sessions/)).toBeInTheDocument();
  });

  it("shows a loading state", () => {
    renderAt("/algorithms/loading");
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows not-found state", () => {
    renderAt("/algorithms/missing");
    expect(screen.getByText(/Algorithm not found/i)).toBeInTheDocument();
  });
});
