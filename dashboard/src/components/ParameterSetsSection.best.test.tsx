import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ParameterSetsSection } from "./ParameterSetsSection";
import type { ParameterSet } from "../types";

function makeSet(id: string, name: string, sharpe: number | null): ParameterSet {
  return {
    id, algorithm_id: "algo-1", name,
    config_values: { x: 1 },
    created_at: "2026-01-01", updated_at: "2026-01-01",
    best_backtest: sharpe == null ? null : {
      sharpe_ratio: sharpe, total_return: 0.1, max_drawdown: -0.05, run_count: 2,
    },
  };
}

const sets = [
  makeSet("aaaaaaaa-1111-1111-1111-111111111111", "first-but-mediocre", 0.5),
  makeSet("bbbbbbbb-2222-2222-2222-222222222222", "actual-best", 2.1),
  makeSet("cccccccc-3333-3333-3333-333333333333", "no-runs", null),
];

vi.mock("../api/hooks", async () => {
  const actual = await vi.importActual<object>("../api/hooks");
  return {
    ...actual,
    useParameterSets: () => ({ data: sets }),
    useCreateParameterSet: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useDeleteParameterSet: () => ({ mutateAsync: vi.fn() }),
    useImportParameterSets: () => ({ mutateAsync: vi.fn() }),
  };
});

const addAlert = vi.fn();
vi.mock("../stores/ui", () => ({
  useUIStore: (sel: (s: { addAlert: typeof addAlert }) => unknown) => sel({ addAlert }),
}));

function renderSection() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ParameterSetsSection
        algorithmId="algo-1"
        manifestConfig={[]}
        onBacktest={() => {}}
      />
    </QueryClientProvider>,
  );
}

describe("ParameterSetsSection", () => {
  it("highlights the row with the highest Sharpe, not the first row", () => {
    renderSection();
    const bestRow = screen.getByText("actual-best").closest("tr")!;
    const firstRow = screen.getByText("first-but-mediocre").closest("tr")!;
    expect(bestRow.className).toContain("bg-emerald-950/30");
    expect(firstRow.className).not.toContain("bg-emerald-950/30");
  });

  it("renders no Deploy button (no deploy flow exists)", () => {
    renderSection();
    expect(screen.queryAllByRole("button", { name: /^deploy$/i })).toHaveLength(0);
  });
});
