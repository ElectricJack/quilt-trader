import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AlgorithmConfig } from "./AlgorithmConfig";

const algoMock = vi.fn(() => ({ data: undefined as unknown, isLoading: true }));
vi.mock("../api/hooks", async () => {
  const actual = await vi.importActual<object>("../api/hooks");
  return {
    ...actual,
    useAlgorithm: () => algoMock(),
    useParameterSets: () => ({ data: [] }),
    useCreateParameterSet: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useDeleteParameterSet: () => ({ mutateAsync: vi.fn() }),
    useImportParameterSets: () => ({ mutateAsync: vi.fn() }),
    useCreateBacktestRun: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useProviderAvailability: () => ({ data: undefined }),
  };
});

const addAlert = vi.fn();
vi.mock("../stores/ui", () => ({
  useUIStore: (sel: (s: { addAlert: typeof addAlert }) => unknown) => sel({ addAlert }),
}));

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/algorithms/algo-1/config"]}>
        <Routes>
          <Route path="/algorithms/:id/config" element={<AlgorithmConfig />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AlgorithmConfig loading states", () => {
  it("shows a loading indicator while the algorithm is fetching", () => {
    algoMock.mockReturnValue({ data: undefined, isLoading: true });
    renderPage();
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("shows not-found when the fetch settles empty", () => {
    algoMock.mockReturnValue({ data: undefined, isLoading: false });
    renderPage();
    expect(screen.getByText(/not found/i)).toBeInTheDocument();
  });
});
