// dashboard/src/components/strategy/StrategyChainMatrix.tsx
// Strike × expiry matrix for the strategy builder. Each cell shows
// availability and liquidity for one call/put contract.
//
// Click semantics depend on the active template:
//   - "custom"  → clicking adds the specific contract as a leg.
//   - anything else → clicking re-anchors the template at the clicked
//                     strike + expiry; legs update automatically.
//
// Cells currently selected by the leg list get a colored ring (long =
// emerald, short = amber) plus a small leg-index badge.
import { useEffect, useMemo, useRef, useState, Fragment } from "react";
import type { OptionChainMatrixResponse, OptionChainContract } from "../../api/client";
import type { OptionLeg } from "../../lib/options";

interface Props {
  matrix: OptionChainMatrixResponse | undefined;
  isLoading?: boolean;
  template: string;
  legs: OptionLeg[];
  onPick: (leg: OptionLeg) => void;
  onAnchor: (strike: number, expiry: string, right: "call" | "put") => void;
}

type Liquidity = "none" | "wide" | "ok" | "good" | "tight";

function liquidity(c: OptionChainContract | undefined): Liquidity {
  if (!c || c.bid == null || c.ask == null) return "none";
  if (c.bid <= 0 || c.ask <= 0) return "none";
  const mid = (c.bid + c.ask) / 2;
  if (mid <= 0) return "none";
  const spreadPct = (c.ask - c.bid) / mid;
  // Cheap proxies — for a real liquidity score we'd weight by volume + OI
  if (spreadPct < 0.05) return "tight";
  if (spreadPct < 0.10) return "good";
  if (spreadPct < 0.25) return "ok";
  return "wide";
}

function cellColor(right: "call" | "put", liq: Liquidity): string {
  if (liq === "none") return "bg-gray-950";
  // Calls = blue family, puts = red family. Brighter = tighter spread.
  if (right === "call") {
    if (liq === "tight") return "bg-blue-400/70";
    if (liq === "good") return "bg-blue-600/60";
    if (liq === "ok") return "bg-blue-800/50";
    return "bg-blue-900/40";
  } else {
    if (liq === "tight") return "bg-red-400/70";
    if (liq === "good") return "bg-red-600/60";
    if (liq === "ok") return "bg-red-800/50";
    return "bg-red-900/40";
  }
}

function fmtMoney(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(2);
}

function fmtExp(exp: string): string {
  const d = new Date(exp + "T00:00:00");
  return `${(d.getMonth() + 1).toString().padStart(2, "0")}/${d.getDate().toString().padStart(2, "0")}/${d.getFullYear().toString().slice(2)}`;
}

export function StrategyChainMatrix({
  matrix,
  isLoading,
  template,
  legs,
  onPick,
  onAnchor,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const atmRef = useRef<HTMLTableRowElement>(null);
  const lastAnchored = useRef<string | null>(null);
  const [hover, setHover] = useState<{
    strike: number;
    expiry: string;
    right: "call" | "put";
    contract: OptionChainContract;
  } | null>(null);

  // Map (expiry|right|strike) → { idx, side } so we can highlight cells
  // that match current legs and surface the leg-list ordering.
  const legByKey = useMemo(() => {
    const m = new Map<string, { idx: number; side: "buy" | "sell" }>();
    legs.forEach((l, i) => {
      m.set(`${l.expiry}|${l.right}|${l.strike}`, { idx: i, side: l.side });
    });
    return m;
  }, [legs]);

  const isCustom = template === "custom";

  const { strikes, expDates, lookup } = useMemo(() => {
    const strikeSet = new Set<number>();
    const expSet: string[] = [];
    const map = new Map<string, OptionChainContract>();
    if (matrix) {
      for (const e of matrix.expiries) {
        expSet.push(e.expiry);
        for (const c of e.contracts) {
          strikeSet.add(c.strike);
          map.set(`${c.strike}|${c.right}|${e.expiry}`, c);
        }
      }
    }
    return {
      strikes: Array.from(strikeSet).sort((a, b) => a - b),
      expDates: expSet,
      lookup: map,
    };
  }, [matrix]);

  const spot = matrix?.spot ?? 0;
  // Strike closest to spot — anchors the initial scroll position only.
  const atmStrike = strikes.length
    ? strikes.reduce(
        (best, k) => (Math.abs(k - spot) < Math.abs(best - spot) ? k : best),
        strikes[0]
      )
    : null;

  // Scroll the ATM row into the *scroll container only* — never the
  // document. Fires once per underlying so it doesn't snap back when
  // other state (hover, date slider, leg add) triggers a re-render.
  // NOTE: hook must run before the early returns below to satisfy
  // React's rules of hooks.
  useEffect(() => {
    if (atmStrike == null) return;
    const anchor = `${matrix?.underlying ?? ""}|${atmStrike}`;
    if (!atmRef.current || !scrollRef.current) return;
    if (lastAnchored.current === anchor) return;
    const container = scrollRef.current;
    const row = atmRef.current;
    const target = row.offsetTop - container.clientHeight / 2 + row.clientHeight / 2;
    container.scrollTop = Math.max(0, target);
    lastAnchored.current = anchor;
  }, [matrix?.underlying, atmStrike]);

  if (isLoading) {
    return (
      <div className="rounded-lg border border-gray-800 bg-gray-900 px-3 py-6 text-center text-sm text-gray-500">
        Loading chain…
      </div>
    );
  }
  if (!matrix || expDates.length === 0 || strikes.length === 0 || atmStrike == null) {
    return (
      <div className="rounded-lg border border-gray-800 bg-gray-900 px-3 py-6 text-center text-sm text-gray-500">
        Pick an underlying to view the chain.
      </div>
    );
  }

  function handleCellClick(strike: number, expiry: string, right: "call" | "put") {
    const c = lookup.get(`${strike}|${right}|${expiry}`);
    if (!c) return;
    if (isCustom) {
      onPick({
        side: "buy",
        right,
        strike: c.strike,
        quantity: 1,
        expiry,
        bid: c.bid ?? undefined,
        ask: c.ask ?? undefined,
        iv: c.iv ?? 0.3,
      });
    } else {
      onAnchor(strike, expiry, right);
    }
  }

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900">
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-800">
        <h3 className="text-sm font-semibold text-gray-200">
          Chain — {matrix.underlying} <span className="text-gray-400">@ {fmtMoney(spot)}</span>
        </h3>
        <span className="text-xs text-gray-500">
          {strikes.length} strikes × {expDates.length} expiries
        </span>
      </div>
      <div className="relative">
        <div ref={scrollRef} className="overflow-x-auto max-h-[500px] overflow-y-auto">
          <table className="text-[10px] font-mono border-collapse">
            <thead className="sticky top-0 z-10">
              <tr>
                <th className="bg-gray-950 px-2 py-1 text-gray-400 text-right sticky left-0 z-20 border-b border-gray-800">
                  Strike
                </th>
                {expDates.map((exp) => (
                  <th
                    key={exp}
                    colSpan={2}
                    className="bg-gray-950 px-1 py-1 text-gray-300 text-center border-l border-gray-800 border-b"
                  >
                    {fmtExp(exp)}
                  </th>
                ))}
              </tr>
              <tr>
                <th className="bg-gray-950 sticky left-0 z-20 border-b border-gray-800"></th>
                {expDates.map((exp) => (
                  <Fragment key={`hdr-${exp}`}>
                    <th className="bg-gray-950 px-1 text-blue-400/70 text-center w-7 border-l border-gray-800 border-b">
                      C
                    </th>
                    <th className="bg-gray-950 px-1 text-red-400/70 text-center w-7 border-b border-gray-800">
                      P
                    </th>
                  </Fragment>
                ))}
              </tr>
            </thead>
            <tbody>
              {strikes.map((strike) => {
                const isAtm = strike === atmStrike;
                return (
                  <tr
                    key={strike}
                    ref={isAtm ? atmRef : undefined}
                    className={isAtm ? "bg-indigo-900/20" : ""}
                  >
                    <td
                      className={`px-2 py-0.5 text-right sticky left-0 z-10 border-r border-gray-800 tabular-nums ${
                        isAtm ? "text-indigo-300 font-semibold bg-indigo-900/40" : "text-gray-300 bg-gray-950"
                      }`}
                    >
                      ${strike}
                    </td>
                    {expDates.map((exp) => {
                      const call = lookup.get(`${strike}|call|${exp}`);
                      const put = lookup.get(`${strike}|put|${exp}`);
                      const callLiq = liquidity(call);
                      const putLiq = liquidity(put);
                      const callSel = legByKey.get(`${exp}|call|${strike}`);
                      const putSel = legByKey.get(`${exp}|put|${strike}`);
                      return (
                        <Fragment key={`${strike}-${exp}`}>
                          <td
                            className={`relative w-7 h-5 border-l border-gray-800/50 ${cellColor("call", callLiq)} ${
                              call && callLiq !== "none"
                                ? "cursor-pointer hover:ring-1 hover:ring-blue-300"
                                : ""
                            } ${
                              callSel
                                ? callSel.side === "buy"
                                  ? "ring-2 ring-emerald-400 ring-inset"
                                  : "ring-2 ring-amber-400 ring-inset"
                                : ""
                            }`}
                            onMouseEnter={() =>
                              call && setHover({ strike, expiry: exp, right: "call", contract: call })
                            }
                            onMouseLeave={() => setHover(null)}
                            onClick={() => callLiq !== "none" && handleCellClick(strike, exp, "call")}
                          >
                            {callSel && (
                              <span
                                className={`absolute top-[1px] left-[1px] text-[8px] leading-none px-0.5 rounded font-bold ${
                                  callSel.side === "buy"
                                    ? "text-emerald-200 bg-emerald-900/80"
                                    : "text-amber-200 bg-amber-900/80"
                                }`}
                              >
                                {callSel.idx + 1}
                              </span>
                            )}
                          </td>
                          <td
                            className={`relative w-7 h-5 ${cellColor("put", putLiq)} ${
                              put && putLiq !== "none"
                                ? "cursor-pointer hover:ring-1 hover:ring-red-300"
                                : ""
                            } ${
                              putSel
                                ? putSel.side === "buy"
                                  ? "ring-2 ring-emerald-400 ring-inset"
                                  : "ring-2 ring-amber-400 ring-inset"
                                : ""
                            }`}
                            onMouseEnter={() =>
                              put && setHover({ strike, expiry: exp, right: "put", contract: put })
                            }
                            onMouseLeave={() => setHover(null)}
                            onClick={() => putLiq !== "none" && handleCellClick(strike, exp, "put")}
                          >
                            {putSel && (
                              <span
                                className={`absolute top-[1px] left-[1px] text-[8px] leading-none px-0.5 rounded font-bold ${
                                  putSel.side === "buy"
                                    ? "text-emerald-200 bg-emerald-900/80"
                                    : "text-amber-200 bg-amber-900/80"
                                }`}
                              >
                                {putSel.idx + 1}
                              </span>
                            )}
                          </td>
                        </Fragment>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {hover && (
          <div className="absolute top-2 right-2 z-30 rounded border border-gray-700 bg-gray-950 px-3 py-2 text-xs shadow-lg pointer-events-none">
            <div className="text-gray-400">
              {hover.right.toUpperCase()} · ${hover.strike} · {fmtExp(hover.expiry)}
            </div>
            <div className="mt-1 flex gap-4 tabular-nums">
              <div>
                <span className="text-gray-500">Bid </span>
                <span className="text-emerald-400">{fmtMoney(hover.contract.bid)}</span>
              </div>
              <div>
                <span className="text-gray-500">Ask </span>
                <span className="text-rose-400">{fmtMoney(hover.contract.ask)}</span>
              </div>
              {hover.contract.iv != null && (
                <div>
                  <span className="text-gray-500">IV </span>
                  <span className="text-gray-300">{(hover.contract.iv * 100).toFixed(1)}%</span>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
      <div className="flex items-center gap-2 px-3 py-1.5 text-[10px] text-gray-500 border-t border-gray-800">
        <span>Liquidity:</span>
        <span className="inline-block w-3 h-3 bg-blue-400/70 rounded-sm"></span>tight
        <span className="inline-block w-3 h-3 bg-blue-600/60 rounded-sm"></span>good
        <span className="inline-block w-3 h-3 bg-blue-800/50 rounded-sm"></span>ok
        <span className="inline-block w-3 h-3 bg-blue-900/40 rounded-sm"></span>wide
        <span className="inline-block w-3 h-3 bg-gray-950 border border-gray-700 rounded-sm ml-1"></span>none
        <span className="ml-3 inline-block w-3 h-3 rounded-sm ring-2 ring-emerald-400 ring-inset"></span>long
        <span className="inline-block w-3 h-3 rounded-sm ring-2 ring-amber-400 ring-inset"></span>short
        <span className="ml-3 text-gray-600">
          {isCustom ? "Click to add a leg" : "Click to re-anchor the template"}
        </span>
      </div>
    </div>
  );
}
