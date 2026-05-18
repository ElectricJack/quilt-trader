import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const closeMutate = vi.fn();

// Mutable state so individual tests can flip isPending without re-mocking.
const closePositionState = { isPending: false };

vi.mock("../api/hooks", () => ({
  useAccount: () => ({
    data: {
      id: "ac1",
      name: "Alpaca Test",
      broker_type: "alpaca",
      environment: "paper",
      supported_asset_types: ["equities"],
      pdt_mode: "off",
    },
    isLoading: false,
  }),
  useBrokerInfo: () => ({
    data: {
      account_info: { cash: 1000, equity: 1500 },
      positions: [
        {
          symbol: "SPY", quantity: 5, side: "long",
          avg_price: 500, current_price: 521.23,
          unrealized_pnl: 106.15, market_value: 2606.15,
        },
      ],
    },
    isLoading: false,
    error: null,
  }),
  useClosePosition: () => ({
    mutate: closeMutate,
    get isPending() { return closePositionState.isPending; },
  }),
  // All other hooks AccountDetail consumes — stubbed with empty/no-op defaults
  // so the page renders. Add fields here if a future page change needs them.
  useAccountEquityCurve: () => ({ data: [], isLoading: false }),
  useAccountTrades: () => ({ data: { items: [] }, isLoading: false }),
  useAllInstances: () => ({ data: [], isLoading: false }),
  useCashFlows: () => ({ data: [], isLoading: false }),
  useCreateCashFlow: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteAccount: () => ({ mutate: vi.fn(), isPending: false }),
  useSyncAccount: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateAccount: () => ({ mutate: vi.fn(), isPending: false }),
  useOpenPosition: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("../stores/ui", () => ({
  useUIStore: (selector: (s: { addAlert: (a: unknown) => void }) => unknown) =>
    selector({ addAlert: vi.fn() }),
}));

import { AccountDetail } from "./AccountDetail";

function renderPage() {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/accounts/ac1"]}>
        <Routes>
          <Route path="/accounts/:id" element={<AccountDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  closeMutate.mockClear();
  closePositionState.isPending = false;
});

describe("AccountDetail — close position", () => {
  it("renders a Close button for each position row", () => {
    renderPage();
    expect(screen.getByRole("button", { name: /close/i })).toBeInTheDocument();
  });

  it("opens the confirm dialog with position details when Close is clicked", () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(screen.getByText(/close 5 long spy at market/i)).toBeInTheDocument();
    expect(screen.getByText(/sell order to alpaca/i)).toBeInTheDocument();
  });

  it("fires useClosePosition.mutate with the row's symbol/side/qty on confirm", async () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    fireEvent.click(screen.getByRole("button", { name: /close position/i }));
    await waitFor(() => expect(closeMutate).toHaveBeenCalledTimes(1));
    expect(closeMutate.mock.calls[0][0]).toMatchObject({
      symbol: "SPY",
      side: "long",
      quantity: 5,
      asset_type: "equities",
    });
  });
});

describe("AccountDetail — close position (pending state)", () => {
  it("does not fire the mutation again while a previous close is in flight", async () => {
    // Simulate an already-in-flight close by setting isPending before render.
    closePositionState.isPending = true;
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    // The confirm button label becomes "Closing…" when isPending is true.
    fireEvent.click(screen.getByRole("button", { name: /closing/i }));
    // Mutation should NOT fire — the onConfirmClose handler early-returns
    // while isPending. (mockClear in beforeEach means count starts at 0.)
    expect(closeMutate).not.toHaveBeenCalled();
  });
});
