// dashboard/src/components/strategy/LegsTable.tsx
// Editable table of option legs in the active strategy. Each row exposes
// side / right / strike / quantity inline editing plus a remove button.
// Strikes/expiries are populated from the live chain (read-only). The
// "Add leg" button promotes a default at-the-money call onto the table.

import { X, Plus } from "lucide-react";
import type { OptionLeg } from "../../lib/options";
import type { OptionChainResponse } from "../../api/client";

interface LegsTableProps {
  legs: OptionLeg[];
  onChange: (legs: OptionLeg[]) => void;
  chain: OptionChainResponse | undefined;
  expiry: string | null;
}

function midPrice(l: OptionLeg): number | null {
  if (l.bid != null && l.ask != null) return (l.bid + l.ask) / 2;
  if (l.ask != null) return l.ask;
  if (l.bid != null) return l.bid;
  return null;
}

export function LegsTable({ legs, onChange, chain, expiry }: LegsTableProps) {
  function updateLeg(idx: number, patch: Partial<OptionLeg>) {
    const next = legs.map((l, i) => (i === idx ? { ...l, ...patch } : l));
    onChange(next);
  }

  function removeLeg(idx: number) {
    onChange(legs.filter((_, i) => i !== idx));
  }

  function addLeg() {
    if (!chain || !expiry) return;
    // Pick a contract close to spot as default
    const calls = chain.contracts.filter((c) => c.right === "call");
    if (!calls.length) return;
    const atm = calls.reduce((best, c) =>
      Math.abs(c.strike - chain.spot) < Math.abs(best.strike - chain.spot) ? c : best
    );
    onChange([
      ...legs,
      {
        side: "buy",
        right: "call",
        strike: atm.strike,
        quantity: 1,
        expiry,
        bid: atm.bid ?? undefined,
        ask: atm.ask ?? undefined,
        iv: atm.iv ?? 0.3,
      },
    ]);
  }

  // Build a list of strike values per right for the dropdowns
  const callStrikes = (chain?.contracts ?? [])
    .filter((c) => c.right === "call")
    .map((c) => c.strike)
    .sort((a, b) => a - b);
  const putStrikes = (chain?.contracts ?? [])
    .filter((c) => c.right === "put")
    .map((c) => c.strike)
    .sort((a, b) => a - b);

  function strikeOptions(right: "call" | "put"): number[] {
    return right === "call" ? callStrikes : putStrikes;
  }

  // When a strike or right changes, re-bind market data from the chain.
  function setStrike(idx: number, strike: number) {
    const leg = legs[idx];
    const found = chain?.contracts.find(
      (c) => c.right === leg.right && c.strike === strike
    );
    updateLeg(idx, {
      strike,
      bid: found?.bid ?? undefined,
      ask: found?.ask ?? undefined,
      iv: found?.iv ?? leg.iv,
    });
  }

  function setRight(idx: number, right: "call" | "put") {
    const leg = legs[idx];
    const found = chain?.contracts.find(
      (c) => c.right === right && c.strike === leg.strike
    );
    updateLeg(idx, {
      right,
      bid: found?.bid ?? undefined,
      ask: found?.ask ?? undefined,
      iv: found?.iv ?? leg.iv,
    });
  }

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900">
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-800">
        <h3 className="text-sm font-semibold text-gray-200">Legs</h3>
        <button
          onClick={addLeg}
          disabled={!chain || !expiry}
          className="flex items-center gap-1 text-xs px-2 py-1 rounded bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white"
          title={!chain ? "Select an expiry to load the chain first" : ""}
        >
          <Plus size={12} /> Add leg
        </button>
      </div>
      {legs.length === 0 ? (
        <div className="px-3 py-6 text-center text-sm text-gray-500">
          No legs yet. Pick a template or click "Add leg".
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-xs text-gray-500 uppercase tracking-wide">
            <tr>
              <th className="text-left px-3 py-2 font-medium">Side</th>
              <th className="text-left px-3 py-2 font-medium">Type</th>
              <th className="text-left px-3 py-2 font-medium">Strike</th>
              <th className="text-left px-3 py-2 font-medium">Expiry</th>
              <th className="text-right px-3 py-2 font-medium">Qty</th>
              <th className="text-right px-3 py-2 font-medium">Mid</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {legs.map((leg, idx) => {
              const mid = midPrice(leg);
              const strikes = strikeOptions(leg.right);
              const hasOwnStrike = strikes.includes(leg.strike);
              return (
                <tr key={idx} className="border-t border-gray-800">
                  <td className="px-3 py-1.5">
                    <select
                      value={leg.side}
                      onChange={(e) => updateLeg(idx, { side: e.target.value as "buy" | "sell" })}
                      className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs"
                    >
                      <option value="buy">Buy</option>
                      <option value="sell">Sell</option>
                    </select>
                  </td>
                  <td className="px-3 py-1.5">
                    <select
                      value={leg.right}
                      onChange={(e) => setRight(idx, e.target.value as "call" | "put")}
                      className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs"
                    >
                      <option value="call">Call</option>
                      <option value="put">Put</option>
                    </select>
                  </td>
                  <td className="px-3 py-1.5">
                    {strikes.length > 0 ? (
                      <select
                        value={leg.strike}
                        onChange={(e) => setStrike(idx, parseFloat(e.target.value))}
                        className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs w-24"
                      >
                        {!hasOwnStrike && <option value={leg.strike}>{leg.strike}</option>}
                        {strikes.map((k) => (
                          <option key={k} value={k}>
                            {k}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type="number"
                        value={leg.strike}
                        onChange={(e) => updateLeg(idx, { strike: parseFloat(e.target.value) || 0 })}
                        className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs w-24"
                      />
                    )}
                  </td>
                  <td className="px-3 py-1.5 text-xs text-gray-300">{leg.expiry}</td>
                  <td className="px-3 py-1.5 text-right">
                    <input
                      type="number"
                      min={1}
                      value={leg.quantity}
                      onChange={(e) =>
                        updateLeg(idx, { quantity: Math.max(1, parseInt(e.target.value, 10) || 1) })
                      }
                      className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs w-16 text-right"
                    />
                  </td>
                  <td className="px-3 py-1.5 text-right text-xs text-gray-400 tabular-nums">
                    {mid != null ? mid.toFixed(2) : "—"}
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    <button
                      onClick={() => removeLeg(idx)}
                      className="text-gray-400 hover:text-red-400"
                      aria-label="Remove leg"
                    >
                      <X size={14} />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
