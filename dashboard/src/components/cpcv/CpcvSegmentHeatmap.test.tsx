import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { CpcvSegmentHeatmap } from "./CpcvSegmentHeatmap";
import type { CPCVResult } from "../../types";

const modeA: CPCVResult = {
  mode: "fixed", n_groups: 4, test_groups_per_split: 1, embargo: 0, purge_horizon: 0,
  groups: [
    { index: 0, start: "2024-01-01", end: "2024-03-31", n_bars: 100 },
    { index: 1, start: "2024-04-01", end: "2024-06-30", n_bars: 100 },
    { index: 2, start: "2024-07-01", end: "2024-09-30", n_bars: 100 },
    { index: 3, start: "2024-10-01", end: "2024-12-31", n_bars: 100 },
  ],
  splits: [], segment_run_ids: ["r0", "r1", "r2", "r3"], paths: [],
  summary: { segment_sharpes: [1.2, -0.3, 0.8, 1.5], bootstrap_ci_lower: 0, bootstrap_ci_upper: 1.5 },
};

describe("CpcvSegmentHeatmap", () => {
  it("mode fixed renders N cells in a single row", () => {
    const { container } = render(<CpcvSegmentHeatmap result={modeA} />);
    const cells = container.querySelectorAll("[data-testid^='heatmap-cell']");
    expect(cells.length).toBe(4);
  });

  it("colors positive Sharpe green and negative red", () => {
    const { container } = render(<CpcvSegmentHeatmap result={modeA} />);
    const cellPos = container.querySelector("[data-testid='heatmap-cell-0-0']");
    const cellNeg = container.querySelector("[data-testid='heatmap-cell-1-0']");
    expect(cellPos?.getAttribute("fill")).toMatch(/#10b981|emerald/);
    expect(cellNeg?.getAttribute("fill")).toMatch(/#ef4444|red/);
  });
});
