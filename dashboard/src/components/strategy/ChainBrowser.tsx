// dashboard/src/components/strategy/ChainBrowser.tsx
// Browse the option chain for the selected expiry. Each row shows a strike
// with call/put bid/ask side-by-side. Clicking the call or put cell promotes
// that contract into the legs table.

import type { OptionLeg } from "../../lib/options";
import type { OptionChainResponse } from "../../api/client";

interface ChainBrowserProps {
  chain: OptionChainResponse | undefined;
  expiry: string | null;
  onPick: (leg: OptionLeg) => void;
  isLoading?: boolean;
}

function fmt(n: number | null | undefined, digits = 2): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}

export function ChainBrowser({ chain, expiry, onPick, isLoading }: ChainBrowserProps) {
  if (isLoading) {
    return (
      <div className="rounded-lg border border-gray-800 bg-gray-900 px-3 py-6 text-center text-sm text-gray-500">
        Loading chain…
      </div>
    );
  }
  if (!chain || !expiry) {
    return (
      <div className="rounded-lg border border-gray-800 bg-gray-900 px-3 py-6 text-center text-sm text-gray-500">
        Pick an underlying + expiry to view the chain.
      </div>
    );
  }

  // Group contracts by strike
  const byStrike = new Map<number, { call?: typeof chain.contracts[number]; put?: typeof chain.contracts[number] }>();
  for (const c of chain.contracts) {
    const row = byStrike.get(c.strike) ?? {};
    if (c.right === "call") row.call = c;
    else row.put = c;
    byStrike.set(c.strike, row);
  }
  const strikes = Array.from(byStrike.keys()).sort((a, b) => a - b);

  function pick(right: "call" | "put", strike: number) {
    const row = byStrike.get(strike);
    const c = right === "call" ? row?.call : row?.put;
    if (!c) return;
    onPick({
      side: "buy",
      right,
      strike: c.strike,
      quantity: 1,
      expiry: chain!.expiry,
      bid: c.bid ?? undefined,
      ask: c.ask ?? undefined,
      iv: c.iv ?? 0.3,
    });
  }

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900">
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-800">
        <h3 className="text-sm font-semibold text-gray-200">
          Chain — {chain.underlying} @ {fmt(chain.spot)} <span className="text-gray-500">({chain.expiry})</span>
        </h3>
        <span className="text-xs text-gray-500">{strikes.length} strikes</span>
      </div>
      <div className="max-h-96 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-gray-900 text-gray-500 uppercase tracking-wide">
            <tr>
              <th className="text-right px-2 py-1.5 font-medium" colSpan={2}>Call</th>
              <th className="text-center px-2 py-1.5 font-medium">Strike</th>
              <th className="text-right px-2 py-1.5 font-medium" colSpan={2}>Put</th>
            </tr>
            <tr className="text-[10px]">
              <th className="text-right px-2 py-1 font-normal">Bid</th>
              <th className="text-right px-2 py-1 font-normal">Ask</th>
              <th className="text-center px-2 py-1 font-normal"></th>
              <th className="text-right px-2 py-1 font-normal">Bid</th>
              <th className="text-right px-2 py-1 font-normal">Ask</th>
            </tr>
          </thead>
          <tbody>
            {strikes.map((k) => {
              const row = byStrike.get(k)!;
              const isAtm = Math.abs(k - chain.spot) < 0.5;
              return (
                <tr key={k} className={`border-t border-gray-800 ${isAtm ? "bg-gray-800/50" : ""}`}>
                  <td
                    className="text-right px-2 py-1 tabular-nums cursor-pointer hover:bg-indigo-900/40"
                    onClick={() => row.call && pick("call", k)}
                  >
                    {fmt(row.call?.bid)}
                  </td>
                  <td
                    className="text-right px-2 py-1 tabular-nums cursor-pointer hover:bg-indigo-900/40"
                    onClick={() => row.call && pick("call", k)}
                  >
                    {fmt(row.call?.ask)}
                  </td>
                  <td className="text-center px-2 py-1 tabular-nums font-medium text-gray-300">{k}</td>
                  <td
                    className="text-right px-2 py-1 tabular-nums cursor-pointer hover:bg-indigo-900/40"
                    onClick={() => row.put && pick("put", k)}
                  >
                    {fmt(row.put?.bid)}
                  </td>
                  <td
                    className="text-right px-2 py-1 tabular-nums cursor-pointer hover:bg-indigo-900/40"
                    onClick={() => row.put && pick("put", k)}
                  >
                    {fmt(row.put?.ask)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
