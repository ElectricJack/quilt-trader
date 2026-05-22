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
      {
        provider: "polygon",
        symbol: "SPY",
        ranges: [{ start: "2024-01-01", end: "2026-05-18" }],
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

  it("groups same symbol from multiple providers into a multi-provider row with per-timeframe children", () => {
    const spyRow = result.rows.find(
      (r) => r.kind === "multi-provider" && r.symbol === "SPY"
    );
    expect(spyRow).toBeDefined();
    if (spyRow?.kind === "multi-provider") {
      // polygon:1min + tradier:1day + tradier:1min (from tradier_live merge)
      expect(spyRow.children.length).toBeGreaterThanOrEqual(3);
      const childKeys = spyRow.children.map((c) => `${c.provider}:${c.timeframe}`).sort();
      expect(childKeys).toContain("polygon:1min");
      expect(childKeys).toContain("tradier:1day");
      expect(childKeys).toContain("tradier:1min");
    }
  });

  it("keeps single-provider assets as plain rows", () => {
    const aapl = result.rows.find((r) => r.kind === "asset" && r.symbol === "AAPL");
    expect(aapl).toBeDefined();
    expect(aapl!.kind).toBe("asset");
  });

  it("sorts rows alphabetically, options group after equity", () => {
    const labels = result.rows.map((r) =>
      r.kind === "options-group" ? r.label
        : r.kind === "multi-provider" ? r.symbol
        : r.symbol
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
