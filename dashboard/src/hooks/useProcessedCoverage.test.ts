import { describe, it, expect } from "vitest";
import { processCoverage } from "./useProcessedCoverage";
import type { CoverageResponse } from "../api/client";

const MOCK_COVERAGE: CoverageResponse = {
  providers: {
    tradier: [
      {
        provider: "tradier",
        symbol: "SPY",
        ranges: [{ start: "2024-05-01", end: "2026-05-20" }],
        timeframes_on_disk: ["1day"],
      },
      {
        provider: "tradier",
        symbol: "SPY241029C00450000",
        ranges: [{ start: "2024-10-14", end: "2024-10-28" }],
        timeframes_on_disk: ["1day"],
      },
      {
        provider: "tradier",
        symbol: "SPY250221C00600000",
        ranges: [{ start: "2024-11-07", end: "2025-02-20" }],
        timeframes_on_disk: ["1day"],
      },
    ],
    tradier_live: [
      {
        provider: "tradier_live",
        symbol: "SPY",
        ranges: [{ start: "2026-05-17", end: "2026-05-20" }],
        timeframes_on_disk: ["1min"],
      },
    ],
    polygon: [
      {
        provider: "polygon",
        symbol: "AAPL",
        ranges: [{ start: "2024-01-01", end: "2026-05-20" }],
        timeframes_on_disk: ["1min"],
      },
    ],
  },
};

describe("processCoverage", () => {
  const result = processCoverage(MOCK_COVERAGE);

  it("returns globalMin and globalMax from all ranges", () => {
    expect(result.globalMin).toBe("2024-01-01");
    expect(result.globalMax).toBe("2026-05-20");
  });

  it("merges _live providers into base name", () => {
    const providers = new Set(result.rows.flatMap((r) =>
      r.kind === "options-group"
        ? r.children.map((c) => c.provider)
        : [r.provider]
    ));
    expect(providers.has("tradier_live")).toBe(false);
    expect(providers.has("tradier")).toBe(true);
  });

  it("groups OCC symbols into options-group rows", () => {
    const group = result.rows.find(
      (r) => r.kind === "options-group" && r.underlying === "SPY"
    );
    expect(group).toBeDefined();
    expect(group!.kind).toBe("options-group");
    if (group!.kind === "options-group") {
      expect(group!.children).toHaveLength(2);
      expect(group!.label).toBe("SPY Options (2)");
    }
  });

  it("deduplicates same symbol across base and _live provider into one row", () => {
    const spyEquity = result.rows.filter(
      (r) => r.kind === "asset" && r.symbol === "SPY"
    );
    expect(spyEquity).toHaveLength(1);
    const spy = spyEquity[0] as { timeframes: string[]; ranges: { start: string; end: string }[] };
    expect(spy.timeframes).toContain("1day");
    expect(spy.timeframes).toContain("1min");
    expect(spy.ranges.length).toBeGreaterThanOrEqual(1);
  });

  it("sorts rows alphabetically, options group after equity", () => {
    const labels = result.rows.map((r) =>
      r.kind === "options-group" ? r.label : r.symbol
    );
    const aaplIdx = labels.indexOf("AAPL");
    const spyIdx = labels.findIndex((l) => l === "SPY");
    const spyOptIdx = labels.findIndex((l) => l.startsWith("SPY Options"));
    expect(aaplIdx).toBeLessThan(spyIdx);
    expect(spyIdx).toBeLessThan(spyOptIdx);
  });

  it("collects unique provider names", () => {
    expect(result.providers.sort()).toEqual(["polygon", "tradier"]);
  });

  it("detects asset types", () => {
    const types = new Set(result.rows.map((r) => r.assetType));
    expect(types.has("equities")).toBe(true);
    expect(types.has("options")).toBe(true);
  });
});
