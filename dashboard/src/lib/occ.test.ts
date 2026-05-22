import { describe, it, expect } from "vitest";
import { parseOCC, isOCCSymbol, formatOCCReadable } from "./occ";

describe("isOCCSymbol", () => {
  it("detects standard OCC symbols", () => {
    expect(isOCCSymbol("SPY241029C00450000")).toBe(true);
    expect(isOCCSymbol("GLD250117P00185000")).toBe(true);
    expect(isOCCSymbol("AAPL250620C00200000")).toBe(true);
  });
  it("rejects equity/crypto symbols", () => {
    expect(isOCCSymbol("SPY")).toBe(false);
    expect(isOCCSymbol("BTCUSD")).toBe(false);
    expect(isOCCSymbol("UNFI")).toBe(false);
  });
});

describe("parseOCC", () => {
  it("parses call option", () => {
    const result = parseOCC("SPY241029C00450000");
    expect(result).toEqual({
      underlying: "SPY",
      expiration: "2024-10-29",
      side: "Call",
      strike: 450,
    });
  });
  it("parses put option", () => {
    const result = parseOCC("GLD250117P00185000");
    expect(result).toEqual({
      underlying: "GLD",
      expiration: "2025-01-17",
      side: "Put",
      strike: 185,
    });
  });
  it("parses fractional strike", () => {
    const result = parseOCC("AAPL250620C00197500");
    expect(result).toEqual({
      underlying: "AAPL",
      expiration: "2025-06-20",
      side: "Call",
      strike: 197.5,
    });
  });
  it("returns null for non-OCC symbol", () => {
    expect(parseOCC("SPY")).toBeNull();
  });
});

describe("formatOCCReadable", () => {
  it("formats call option", () => {
    expect(formatOCCReadable("SPY241029C00450000")).toBe("SPY $450 Call 10/29/24");
  });
  it("formats put option with fractional strike", () => {
    expect(formatOCCReadable("GLD250117P00185500")).toBe("GLD $185.50 Put 01/17/25");
  });
  it("returns original symbol if not OCC", () => {
    expect(formatOCCReadable("SPY")).toBe("SPY");
  });
});
