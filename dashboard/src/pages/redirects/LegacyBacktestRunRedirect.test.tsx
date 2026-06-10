import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

const fetchSpy = vi.fn();
vi.mock("../../api/hooks", async () => {
  const actual = await vi.importActual<any>("../../api/hooks");
  return {
    ...actual,
    useBacktestRun: (id: string) => {
      fetchSpy(id);
      if (id === "missing") return { data: undefined, isLoading: false, error: new Error("404") };
      return { data: { id, algorithm_id: "algo-xyz" }, isLoading: false, error: null };
    },
  };
});

import { LegacyBacktestRunRedirect } from "./LegacyBacktestRunRedirect";

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/backtest-runs/:id" element={<LegacyBacktestRunRedirect />} />
          <Route path="/algorithms/:algoId/backtests/:runId" element={<div>NEW URL</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("LegacyBacktestRunRedirect", () => {
  beforeEach(() => { fetchSpy.mockClear(); });

  it("redirects to the nested URL when the run resolves", async () => {
    renderAt("/backtest-runs/run-1");
    await waitFor(() => expect(screen.getByText("NEW URL")).toBeInTheDocument());
  });

  it("shows 'not found' when the run doesn't exist", () => {
    renderAt("/backtest-runs/missing");
    expect(screen.getByText(/not found/i)).toBeInTheDocument();
  });
});
