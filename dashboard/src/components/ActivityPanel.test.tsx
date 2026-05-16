import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ActivityPanel } from "./ActivityPanel";

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient();
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

describe("ActivityPanel", () => {
  it("renders the empty state when initialRows is empty", () => {
    render(wrap(<ActivityPanel target="worker:fake" initialRows={[]} />));
    expect(screen.getByText(/no activity/i)).toBeInTheDocument();
  });

  it("renders an event row from initialRows", () => {
    render(
      wrap(
        <ActivityPanel
          target="worker:fake"
          initialRows={[{
            id: "1", worker_id: "w", instance_id: null,
            timestamp: "2026-05-16T12:00:00Z", kind: "event",
            event_type: "trade_executed", severity: "info",
            logger_name: null, message: "BUY 10 AAPL", payload: {},
          }]}
        />,
      ),
    );
    expect(screen.getByText("trade_executed")).toBeInTheDocument();
    expect(screen.getByText("BUY 10 AAPL")).toBeInTheDocument();
  });
});
