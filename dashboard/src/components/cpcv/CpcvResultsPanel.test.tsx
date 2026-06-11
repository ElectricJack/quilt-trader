import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { CpcvResultsPanel } from "./CpcvResultsPanel";
import type { CPCVResult } from "../../types";

const r: CPCVResult = {
  mode: "select", n_groups: 4, test_groups_per_split: 2, embargo: 5, purge_horizon: 0,
  groups: [], splits: [], segment_run_ids: [], paths: [],
  summary: {
    path_sharpes: [1.1, 1.2, 0.9],
    mean_path_sharpe: 1.07, median_path_sharpe: 1.1, std_path_sharpe: 0.15,
    bootstrap_ci_lower: 0.7, bootstrap_ci_upper: 1.4,
    deflated_sharpe_ratio: 0.6, probabilistic_sharpe_zero: 0.85,
  },
};

describe("CpcvResultsPanel", () => {
  it("renders summary + distribution + heatmap for mode B", () => {
    render(
      <MemoryRouter>
        <CpcvResultsPanel result={r} status="completed" progress={null} />
      </MemoryRouter>,
    );
    expect(screen.getByText(/mean path sharpe/i)).toBeInTheDocument();
  });

  it("renders progress when status is running", () => {
    render(
      <MemoryRouter>
        <CpcvResultsPanel result={null} status="running" progress={{ pct: 0.42, message: "split 6/15" }} />
      </MemoryRouter>,
    );
    expect(screen.getByText(/split 6\/15/)).toBeInTheDocument();
  });
});
