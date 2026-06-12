import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { CpcvSummaryCard } from "./CpcvSummaryCard";
import type { CPCVResult } from "../../types";

const fixedResult: CPCVResult = {
  mode: "fixed", n_groups: 6, test_groups_per_split: 1, embargo: 5, purge_horizon: 0,
  groups: [], splits: [], segment_run_ids: ["r1","r2","r3","r4","r5","r6"], paths: [],
  summary: {
    segment_sharpes: [1.1, 1.3, 0.9, 1.4, 0.8, 1.2],
    mean_segment_sharpe: 1.12, median_segment_sharpe: 1.15, std_segment_sharpe: 0.22,
    bootstrap_ci_lower: 0.85, bootstrap_ci_upper: 1.40,
  },
};

const selectResult: CPCVResult = {
  ...fixedResult,
  mode: "select",
  summary: {
    path_sharpes: [1.2, 0.9, 1.4, 1.1, 1.3],
    mean_path_sharpe: 1.18, median_path_sharpe: 1.2, std_path_sharpe: 0.18,
    bootstrap_ci_lower: 0.92, bootstrap_ci_upper: 1.42,
    deflated_sharpe_ratio: 0.78, probabilistic_sharpe_zero: 0.96,
  },
};

describe("CpcvSummaryCard", () => {
  it("mode fixed renders 4 KPIs from segment summary", () => {
    render(<CpcvSummaryCard result={fixedResult} />);
    expect(screen.getByText(/mean segment sharpe/i)).toBeInTheDocument();
    expect(screen.getByText(/1\.12/)).toBeInTheDocument();
    expect(screen.queryByText(/deflated/i)).not.toBeInTheDocument();
  });

  it("mode select renders 6 KPIs including DSR and PSR", () => {
    render(<CpcvSummaryCard result={selectResult} />);
    expect(screen.getByText(/mean path sharpe/i)).toBeInTheDocument();
    expect(screen.getByText(/deflated sharpe/i)).toBeInTheDocument();
    expect(screen.getByText(/0\.78/)).toBeInTheDocument();
    expect(screen.getByText(/psr/i)).toBeInTheDocument();
    expect(screen.getByText(/0\.96/)).toBeInTheDocument();
  });

  it("mode select colors DSR badge green when positive", () => {
    const { container } = render(<CpcvSummaryCard result={selectResult} />);
    const dsrBadge = container.querySelector("[data-testid='dsr-badge']");
    expect(dsrBadge?.className).toMatch(/emerald/);
  });

  it("mode select colors DSR badge red when zero or negative", () => {
    const r = { ...selectResult, summary: { ...selectResult.summary, deflated_sharpe_ratio: 0.0 } };
    const { container } = render(<CpcvSummaryCard result={r} />);
    const dsrBadge = container.querySelector("[data-testid='dsr-badge']");
    expect(dsrBadge?.className).toMatch(/red/);
  });
});
