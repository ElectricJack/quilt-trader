import { describe, it, expect } from "vitest";
import { bsCall, bsPut, greeks, legCostAtExpiry, strategyPnl } from "./options";

describe("Black-Scholes", () => {
  // Hull, Options Futures and Other Derivatives, Ch. 15 worked example:
  // S=42, K=40, r=0.10, sigma=0.20, T=0.5 -> call ~ 4.7594, put ~ 0.8086
  it("prices a call option per Hull", () => {
    const c = bsCall(42, 40, 0.5, 0.10, 0.20);
    expect(c).toBeCloseTo(4.7594, 3);
  });
  it("prices a put option per Hull", () => {
    const p = bsPut(42, 40, 0.5, 0.10, 0.20);
    expect(p).toBeCloseTo(0.8086, 3);
  });
  it("respects put-call parity", () => {
    // C - P = S - K*e^(-rT)
    const S = 100, K = 100, T = 0.25, r = 0.05, sigma = 0.30;
    const lhs = bsCall(S, K, T, r, sigma) - bsPut(S, K, T, r, sigma);
    const rhs = S - K * Math.exp(-r * T);
    expect(lhs).toBeCloseTo(rhs, 4);
  });
});

describe("Greeks", () => {
  it("ATM call delta ~ 0.5", () => {
    const g = greeks("buy", "call", 100, 100, 0.25, 0.05, 0.30);
    expect(g.delta).toBeGreaterThan(0.45);
    expect(g.delta).toBeLessThan(0.65);
  });
  it("short position flips delta sign", () => {
    const buy = greeks("buy", "call", 100, 100, 0.25, 0.05, 0.30);
    const sell = greeks("sell", "call", 100, 100, 0.25, 0.05, 0.30);
    expect(sell.delta).toBeCloseTo(-buy.delta, 6);
  });
});

describe("Expiry payoff", () => {
  it("long call: intrinsic above strike, zero below", () => {
    const leg = { side: "buy", right: "call", strike: 100, quantity: 1,
                  bid: 5, ask: 5.2, expiry: "2026-06-20", iv: 0.3 } as const;
    expect(legCostAtExpiry(leg, 110)).toBeCloseTo(10 - 5.1, 4);  // intrinsic - mid
    expect(legCostAtExpiry(leg, 90)).toBeCloseTo(-5.1, 4);        // -mid
  });
  it("vertical spread max profit equals strike width minus net debit", () => {
    const legs = [
      { side: "buy",  right: "call", strike: 100, quantity: 1, bid: 8, ask: 8.2, expiry: "2026-06-20", iv: 0.3 },
      { side: "sell", right: "call", strike: 110, quantity: 1, bid: 4, ask: 4.2, expiry: "2026-06-20", iv: 0.3 },
    ] as const;
    // mid debit = 8.1 - 4.1 = 4.0; width = 10; max profit at expiry above 110 = 10 - 4 = 6
    const pnlAt120 = strategyPnl(legs as any, 120, "expiry");
    expect(pnlAt120).toBeCloseTo(6.0, 4);
  });
});
