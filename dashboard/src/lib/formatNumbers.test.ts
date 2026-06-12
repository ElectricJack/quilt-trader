import { describe, it, expect } from "vitest";
import { fmtDate, fmtDateTime, fmtPct, fmtNum } from "./formatNumbers";

describe("date formatters", () => {
  it("fmtDate renders a locale date for ISO input", () => {
    const iso = "2026-03-05T10:00:00Z";
    expect(fmtDate(iso)).toBe(new Date(iso).toLocaleDateString());
  });
  it("fmtDateTime renders a locale datetime for ISO input", () => {
    const iso = "2026-03-05T10:00:00Z";
    expect(fmtDateTime(iso)).toBe(new Date(iso).toLocaleString());
  });
  it("both return em-dash for null/undefined/empty", () => {
    expect(fmtDate(null)).toBe("—");
    expect(fmtDate(undefined)).toBe("—");
    expect(fmtDate("")).toBe("—");
    expect(fmtDateTime(null)).toBe("—");
  });
  it("both pass through unparseable strings", () => {
    expect(fmtDate("not-a-date")).toBe("not-a-date");
  });
});

describe("existing formatters keep behavior", () => {
  it("fmtPct", () => expect(fmtPct(0.1234)).toBe("12.34%"));
  it("fmtNum", () => expect(fmtNum(1.005, 2)).toBe("1.01"));
});
