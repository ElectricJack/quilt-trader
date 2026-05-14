// dashboard/src/components/strategy/GreeksPanel.tsx
// Display the aggregated strategy Greeks (Δ Γ Θ V) at the current spot and
// scrub date, plus the structural cost/max-P/max-L/breakeven summary derived
// from the at-expiry P&L curve.

import { useMemo } from "react";
import type { OptionLeg } from "../../lib/options";
import { strategyGreeks, pnlCurve, strategyPnl } from "../../lib/options";

interface GreeksPanelProps {
  legs: OptionLeg[];
  spot: number | undefined;
  scrubMs: number;
}

function legMidPrice(l: OptionLeg): number {
  if (l.bid != null && l.ask != null) return (l.bid + l.ask) / 2;
  if (l.ask != null) return l.ask;
  if (l.bid != null) return l.bid;
  return 0;
}

function netCost(legs: OptionLeg[]): number {
  return legs.reduce((sum, l) => {
    const mid = legMidPrice(l);
    return sum + mid * l.quantity * (l.side === "buy" ? 1 : -1);
  }, 0);
}

function fmt(n: number, digits = 4, withSign = false): string {
  if (!Number.isFinite(n)) return "—";
  const s = n.toFixed(digits);
  if (withSign && n > 0 && !s.startsWith("-")) return `+${s}`;
  return s;
}

function Cell({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-gray-500">{label}</div>
      <div className="text-sm font-medium text-gray-100 tabular-nums">{value}</div>
      {sub && <div className="text-[10px] text-gray-500 tabular-nums">{sub}</div>}
    </div>
  );
}

export function GreeksPanel({ legs, spot, scrubMs }: GreeksPanelProps) {
  const { greeks, cost, maxProfit, maxLoss, breakevens } = useMemo(() => {
    if (legs.length === 0 || spot == null) {
      return {
        greeks: { delta: 0, gamma: 0, theta: 0, vega: 0 },
        cost: 0,
        maxProfit: 0,
        maxLoss: 0,
        breakevens: [] as number[],
      };
    }
    const g = strategyGreeks(legs, spot, scrubMs);
    const c = netCost(legs);

    // Sample the at-expiry curve over a wide range to estimate max P/L.
    const strikes = legs.map((l) => l.strike);
    const sLo = Math.min(spot, ...strikes);
    const sHi = Math.max(spot, ...strikes);
    const pad = Math.max((sHi - sLo) * 2, sHi * 0.3, 1);
    const curve = pnlCurve(legs, [Math.max(0.01, sLo - pad), sHi + pad], "expiry", 400);
    const ys = curve.map((p) => p.y);
    const maxP = Math.max(...ys);
    const minP = Math.min(...ys);

    // Breakevens: zero-crossings on the same curve.
    const bes: number[] = [];
    for (let i = 1; i < curve.length; i++) {
      const a = curve[i - 1];
      const b = curve[i];
      if ((a.y <= 0 && b.y >= 0) || (a.y >= 0 && b.y <= 0)) {
        if (b.y !== a.y) {
          const t = -a.y / (b.y - a.y);
          bes.push(a.x + t * (b.x - a.x));
        }
      }
    }

    return {
      greeks: g,
      cost: c,
      maxProfit: maxP,
      maxLoss: minP,
      breakevens: bes,
    };
  }, [legs, spot, scrubMs]);

  const nowPnl = useMemo(() => {
    if (legs.length === 0 || spot == null) return 0;
    return strategyPnl(legs, spot, scrubMs);
  }, [legs, spot, scrubMs]);

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900">
      <div className="px-3 py-2 border-b border-gray-800">
        <h3 className="text-sm font-semibold text-gray-200">Greeks &amp; Summary</h3>
      </div>
      <div className="grid grid-cols-4 divide-x divide-gray-800 border-b border-gray-800">
        <Cell label="Δ Delta" value={fmt(greeks.delta, 3, true)} />
        <Cell label="Γ Gamma" value={fmt(greeks.gamma, 4, true)} />
        <Cell label="Θ Theta /day" value={fmt(greeks.theta, 3, true)} />
        <Cell label="V Vega /1%" value={fmt(greeks.vega, 3, true)} />
      </div>
      <div className="grid grid-cols-2 divide-x divide-gray-800">
        <Cell
          label="Net cost"
          value={`${cost >= 0 ? "" : "+"}${(-cost).toFixed(2)}`}
          sub={cost > 0 ? "debit" : cost < 0 ? "credit" : ""}
        />
        <Cell
          label="Now P&L"
          value={`${nowPnl >= 0 ? "+" : ""}${nowPnl.toFixed(2)}`}
        />
      </div>
      <div className="grid grid-cols-2 divide-x divide-gray-800 border-t border-gray-800">
        <Cell
          label="Max profit"
          value={Number.isFinite(maxProfit) ? `+${maxProfit.toFixed(2)}` : "∞"}
        />
        <Cell
          label="Max loss"
          value={Number.isFinite(maxLoss) ? maxLoss.toFixed(2) : "-∞"}
        />
      </div>
      <div className="px-3 py-2 border-t border-gray-800 text-xs">
        <span className="text-gray-500 mr-2">Breakeven:</span>
        {breakevens.length === 0 ? (
          <span className="text-gray-400">—</span>
        ) : (
          <span className="text-gray-200 tabular-nums">
            {breakevens.map((b) => b.toFixed(2)).join(", ")}
          </span>
        )}
      </div>
    </div>
  );
}
