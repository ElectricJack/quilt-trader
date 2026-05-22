import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

vi.mock("../api/hooks", () => ({
  useLiveSubscriptions: () => ({
    data: [
      {
        id: "sub-1",
        account_id: "acct-42",
        account_name: "Alpaca Live",
        provider_type: null,
        broker: "alpaca",
        symbol: "SPY",
        asset_class: "equities",
        tick_retention_hours: 168,
        tick_rate_per_min: 200,
        status: "running",
        created_at: null,
        last_tick_at: new Date().toISOString(),
        error_message: null,
        consumers: [
          {
            id: "c1",
            consumer_type: "manual",
            consumer_id: null,
            created_at: null,
            algorithm_id: null,
            algorithm_name: null,
          },
          {
            id: "c2",
            consumer_type: "algo",
            consumer_id: "deployment-abc12345",
            created_at: null,
            algorithm_id: "algo-99",
            algorithm_name: "Simple MA Crossover",
          },
        ],
      },
    ],
    isLoading: false,
  }),
  useAccounts: () => ({ data: [] }),
  useCreateLiveSubscription: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUnsubscribeLiveSubscription: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useLiveSubStorageEstimate: () => ({ data: null }),
}));

vi.mock("../stores/ui", () => ({
  useUIStore: () => vi.fn(),
}));

import { LiveSubscriptionsSection } from "./LiveSubscriptionsSection";

function renderIt() {
  const qc = new QueryClient();
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <LiveSubscriptionsSection />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

describe("LiveSubscriptionsSection", () => {
  it("renders account name as a link to /accounts/<id>", () => {
    renderIt();
    const link = screen.getByRole("link", { name: "Alpaca Live" });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "/accounts/acct-42");
  });

  it("groups subscriptions under account and shows symbol chip", () => {
    renderIt();
    expect(screen.getByText("1 subscription")).toBeInTheDocument();
    expect(screen.getByText("SPY")).toBeInTheDocument();
  });

  it("renders subscription with asset_class badge", () => {
    renderIt();
    expect(screen.getByText("SPY")).toBeInTheDocument();
    expect(screen.getByText("equities")).toBeInTheDocument();
  });

  it("shows green status dot for fresh subscription", () => {
    renderIt();
    const dot = screen.getByTitle(/last tick:/);
    expect(dot).toBeInTheDocument();
    expect(dot.className).toContain("bg-green-400");
  });
});
