import { describe, it, expect } from "vitest";
import { buildRunMarkers } from "./EquitySlot";

describe("buildRunMarkers", () => {
  it("returns empty array when runsIndex is null or undefined", () => {
    expect(buildRunMarkers(null)).toEqual([]);
    expect(buildRunMarkers(undefined)).toEqual([]);
  });

  it("emits a start marker per started_at and a stop marker per stopped_at", () => {
    const markers = buildRunMarkers([
      { run_id: "r1", run_number: 1, started_at: "2026-05-01T00:00:00Z", stopped_at: "2026-05-03T00:00:00Z", status: "stopped" },
      { run_id: "r2", run_number: 2, started_at: "2026-05-04T00:00:00Z", stopped_at: null, status: "running" },
    ]);
    expect(markers).toHaveLength(3);
    expect(markers[0]).toMatchObject({ position: "aboveBar", text: "Run #1 start" });
    expect(markers[1]).toMatchObject({ position: "belowBar", text: "Run #1 stop" });
    expect(markers[2]).toMatchObject({ position: "aboveBar", text: "Run #2 start" });
  });

  it("returns time as a unix seconds number", () => {
    const markers = buildRunMarkers([
      { run_id: "r1", run_number: 1, started_at: "2026-05-01T00:00:00Z", stopped_at: null, status: "running" },
    ]);
    expect(typeof markers[0].time).toBe("number");
    expect(markers[0].time).toBe(Date.parse("2026-05-01T00:00:00Z") / 1000);
  });
});
