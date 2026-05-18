import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("../api/hooks", () => ({
  useLiveSubscriptions: () => ({
    data: [
      {
        id: "sub-1",
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
          { id: "c1", consumer_type: "manual", consumer_id: null, created_at: null },
          { id: "c2", consumer_type: "algo", consumer_id: "deployment-abc12345", created_at: null },
        ],
      },
    ],
    isLoading: false,
  }),
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
    <QueryClientProvider client={qc}>
      <LiveSubscriptionsSection />
    </QueryClientProvider>
  );
}

describe("LiveSubscriptionsSection", () => {
  it("renders subscription with asset_class and consumer list", () => {
    renderIt();
    expect(screen.getByText("SPY")).toBeInTheDocument();
    expect(screen.getByText("equities")).toBeInTheDocument();
    expect(screen.getByText(/manual/)).toBeInTheDocument();
    expect(screen.getByText(/algo: deployme/)).toBeInTheDocument();
  });

  it("shows green 'last tick' badge for fresh subscription", () => {
    renderIt();
    expect(screen.getByText(/last tick:/)).toBeInTheDocument();
  });
});
