import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AlgorithmCard } from "./AlgorithmCard";

const baseAlgo = {
  id: "a-1",
  name: "crypto-tsmom",
  version: "1.4.2",
  summary: {
    status: "live" as const,
    status_source: "inst-1",
    headline_sharpe: 1.4,
    headline_sharpe_source: "live_30d" as const,
    equity_sparkline: [100, 101, 99, 103, 105],
    equity_sparkline_source: "live" as const,
    counts: { deployments: 3, backtests: 47, research_sessions: 2 },
    last_activity_at: "2026-06-10T11:00:00Z",
  },
};

function renderCard(algo: typeof baseAlgo) {
  return render(<MemoryRouter><AlgorithmCard algorithm={algo as any} /></MemoryRouter>);
}

describe("AlgorithmCard", () => {
  it("renders name, version, and counts", () => {
    renderCard(baseAlgo);
    expect(screen.getByText("crypto-tsmom")).toBeInTheDocument();
    expect(screen.getByText(/v1\.4\.2/)).toBeInTheDocument();
    expect(screen.getByText(/3 deployments/)).toBeInTheDocument();
    expect(screen.getByText(/47 backtests/)).toBeInTheDocument();
  });

  it("renders headline Sharpe with source label", () => {
    renderCard(baseAlgo);
    expect(screen.getByText(/1\.40/)).toBeInTheDocument();
    expect(screen.getByText(/live/i)).toBeInTheDocument();
  });

  it("renders an SVG sparkline when points are present", () => {
    const { container } = renderCard(baseAlgo);
    expect(container.querySelector("svg path")).not.toBeNull();
  });

  it("links to the algorithm hub", () => {
    renderCard(baseAlgo);
    const link = screen.getByRole("link", { name: /crypto-tsmom/i });
    expect(link.getAttribute("href")).toBe("/algorithms/a-1");
  });
});
