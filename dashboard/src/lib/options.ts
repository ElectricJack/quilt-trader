// dashboard/src/lib/options.ts
// Pure Black-Scholes + payoff math for the strategy builder.

export type Side = "buy" | "sell";
export type Right = "call" | "put";

export type OptionLeg = {
  side: Side;
  right: Right;
  strike: number;
  quantity: number;
  expiry: string;       // YYYY-MM-DD
  bid?: number;
  ask?: number;
  iv: number;           // decimal
};

export type GreeksOut = {
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
};

// Standard normal CDF via Abramowitz & Stegun 7.1.26 (max error ~7.5e-8).
function normCdf(x: number): number {
  const a1 =  0.254829592, a2 = -0.284496736, a3 =  1.421413741;
  const a4 = -1.453152027, a5 =  1.061405429, p  =  0.3275911;
  const sign = x < 0 ? -1 : 1;
  const ax = Math.abs(x) / Math.SQRT2;
  const t = 1 / (1 + p * ax);
  const y = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-ax * ax);
  return 0.5 * (1 + sign * y);
}

function normPdf(x: number): number {
  return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);
}

function d1(S: number, K: number, T: number, r: number, sigma: number): number {
  return (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * Math.sqrt(T));
}

function d2(d1v: number, sigma: number, T: number): number {
  return d1v - sigma * Math.sqrt(T);
}

export function bsCall(S: number, K: number, T: number, r: number, sigma: number): number {
  if (T <= 0) return Math.max(S - K, 0);
  const D1 = d1(S, K, T, r, sigma);
  const D2 = d2(D1, sigma, T);
  return S * normCdf(D1) - K * Math.exp(-r * T) * normCdf(D2);
}

export function bsPut(S: number, K: number, T: number, r: number, sigma: number): number {
  if (T <= 0) return Math.max(K - S, 0);
  const D1 = d1(S, K, T, r, sigma);
  const D2 = d2(D1, sigma, T);
  return K * Math.exp(-r * T) * normCdf(-D2) - S * normCdf(-D1);
}

export function greeks(side: Side, right: Right, S: number, K: number,
                       T: number, r: number, sigma: number): GreeksOut {
  if (T <= 0) return { delta: 0, gamma: 0, theta: 0, vega: 0 };
  const D1 = d1(S, K, T, r, sigma);
  const D2 = d2(D1, sigma, T);
  const sign = side === "buy" ? 1 : -1;
  const callDelta = normCdf(D1);
  const putDelta  = callDelta - 1;
  const delta = (right === "call" ? callDelta : putDelta) * sign;
  const gamma = (normPdf(D1) / (S * sigma * Math.sqrt(T))) * sign;
  const vega  = (S * normPdf(D1) * Math.sqrt(T)) / 100 * sign;       // per 1% vol move
  const thetaAnnual = right === "call"
    ? -(S * normPdf(D1) * sigma) / (2 * Math.sqrt(T)) - r * K * Math.exp(-r * T) * normCdf(D2)
    : -(S * normPdf(D1) * sigma) / (2 * Math.sqrt(T)) + r * K * Math.exp(-r * T) * normCdf(-D2);
  const theta = (thetaAnnual / 365) * sign;                          // per day
  return { delta, gamma, theta, vega };
}

// ---- Payoffs ----

function legMidPrice(leg: OptionLeg): number {
  if (leg.bid != null && leg.ask != null) return (leg.bid + leg.ask) / 2;
  if (leg.ask != null) return leg.ask;
  if (leg.bid != null) return leg.bid;
  return 0;
}

export function legCostAtExpiry(leg: OptionLeg, S: number): number {
  const intrinsic = leg.right === "call"
    ? Math.max(S - leg.strike, 0)
    : Math.max(leg.strike - S, 0);
  const entry = legMidPrice(leg);
  const sign = leg.side === "buy" ? 1 : -1;
  return (intrinsic - entry) * sign * leg.quantity;
}

export function legCostAtDate(
  leg: OptionLeg, S: number, dateMs: number, r = 0.04,
): number {
  const expiryMs = Date.parse(leg.expiry + "T16:00:00Z");
  const T = Math.max((expiryMs - dateMs) / (365.25 * 24 * 3600 * 1000), 0);
  const theo = leg.right === "call"
    ? bsCall(S, leg.strike, T, r, leg.iv)
    : bsPut(S, leg.strike, T, r, leg.iv);
  const entry = legMidPrice(leg);
  const sign = leg.side === "buy" ? 1 : -1;
  return (theo - entry) * sign * leg.quantity;
}

export function strategyPnl(
  legs: OptionLeg[], S: number, dateMs: number | "expiry", r = 0.04,
): number {
  if (dateMs === "expiry") {
    return legs.reduce((sum, l) => sum + legCostAtExpiry(l, S), 0);
  }
  return legs.reduce((sum, l) => sum + legCostAtDate(l, S, dateMs, r), 0);
}

export function strategyGreeks(
  legs: OptionLeg[], S: number, dateMs: number, r = 0.04,
): GreeksOut {
  return legs.reduce((agg, l) => {
    const expiryMs = Date.parse(l.expiry + "T16:00:00Z");
    const T = Math.max((expiryMs - dateMs) / (365.25 * 24 * 3600 * 1000), 0);
    const g = greeks(l.side, l.right, S, l.strike, T, r, l.iv);
    return {
      delta: agg.delta + g.delta * l.quantity,
      gamma: agg.gamma + g.gamma * l.quantity,
      theta: agg.theta + g.theta * l.quantity,
      vega:  agg.vega  + g.vega  * l.quantity,
    };
  }, { delta: 0, gamma: 0, theta: 0, vega: 0 });
}

export function pnlCurve(
  legs: OptionLeg[],
  spotRange: [number, number],
  dateMs: number | "expiry",
  steps: number = 200,
): { x: number; y: number }[] {
  const [lo, hi] = spotRange;
  const step = (hi - lo) / steps;
  const out: { x: number; y: number }[] = [];
  for (let i = 0; i <= steps; i++) {
    const x = lo + i * step;
    out.push({ x, y: strategyPnl(legs, x, dateMs) });
  }
  return out;
}
