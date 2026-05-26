import { useMemo } from "react";

interface Contract {
  symbol: string;
  strike: number;
  option_type: string;
  expiration: string;
  bars: number;
}

interface Expiration {
  expiration: string;
  contracts: Contract[];
  count: number;
}

interface Props {
  underlying: string;
  provider: string;
  expirations: Expiration[];
  onContractClick?: (provider: string, symbol: string) => void;
}

function barColor(bars: number): string {
  if (bars === 0) return "bg-gray-800";
  if (bars < 5) return "bg-yellow-900/60";
  if (bars < 20) return "bg-emerald-900/60";
  if (bars < 50) return "bg-emerald-700/60";
  return "bg-emerald-500/60";
}

function barTooltip(c: Contract | undefined): string {
  if (!c) return "No data";
  if (c.bars === 0) return `${c.symbol} (no bars)`;
  return `${c.symbol}\n${c.bars} bars`;
}

export function OptionsChainMatrix({ underlying, provider, expirations, onContractClick }: Props) {
  const { strikes, expDates, lookup, maxBars } = useMemo(() => {
    const strikeSet = new Set<number>();
    const expSet = new Set<string>();
    const map = new Map<string, Contract>();
    let max = 0;

    for (const exp of expirations) {
      expSet.add(exp.expiration);
      for (const c of exp.contracts) {
        strikeSet.add(c.strike);
        map.set(`${c.strike}|${c.option_type}|${exp.expiration}`, c);
        if (c.bars > max) max = c.bars;
      }
    }

    return {
      strikes: Array.from(strikeSet).sort((a, b) => a - b),
      expDates: Array.from(expSet).sort(),
      lookup: map,
      maxBars: max,
    };
  }, [expirations]);

  if (strikes.length === 0 || expDates.length === 0) {
    return <div className="text-xs text-gray-500 px-3 py-2">No contract data available</div>;
  }

  const fmtExp = (exp: string) => {
    const d = new Date(exp + "T00:00:00");
    return `${(d.getMonth() + 1).toString().padStart(2, "0")}/${d.getDate().toString().padStart(2, "0")}/${d.getFullYear().toString().slice(2)}`;
  };

  return (
    <div className="overflow-x-auto max-h-[500px] overflow-y-auto">
      <table className="text-[10px] font-mono border-collapse">
        <thead className="sticky top-0 z-10">
          <tr>
            <th className="bg-gray-950 px-2 py-1 text-gray-400 text-right sticky left-0 z-20">Strike</th>
            {expDates.map((exp) => (
              <th key={exp} colSpan={2} className="bg-gray-950 px-1 py-1 text-gray-400 text-center border-l border-gray-800">
                {fmtExp(exp)}
              </th>
            ))}
          </tr>
          <tr>
            <th className="bg-gray-950 sticky left-0 z-20"></th>
            {expDates.map((exp) => (
              <Fragment key={`hdr-${exp}`}>
                <th className="bg-gray-950 px-1 text-blue-400/70 text-center w-5 border-l border-gray-800">C</th>
                <th className="bg-gray-950 px-1 text-red-400/70 text-center w-5">P</th>
              </Fragment>
            ))}
          </tr>
        </thead>
        <tbody>
          {strikes.map((strike) => (
            <tr key={strike} className="hover:bg-gray-800/30">
              <td className="px-2 py-0.5 text-gray-300 text-right sticky left-0 bg-gray-950 z-10 border-r border-gray-800">
                ${strike}
              </td>
              {expDates.map((exp) => {
                const call = lookup.get(`${strike}|call|${exp}`);
                const put = lookup.get(`${strike}|put|${exp}`);
                return (
                  <Fragment key={`${strike}-${exp}`}>
                    <td
                      className={`w-5 h-5 border-l border-gray-800/50 ${call ? barColor(call.bars) : "bg-gray-950"} ${call ? "cursor-pointer hover:ring-1 hover:ring-blue-400/50" : ""}`}
                      title={barTooltip(call)}
                      onClick={() => call && call.bars > 0 && onContractClick?.(provider, call.symbol)}
                    >
                      {call && call.bars > 0 && (
                        <div className="w-full h-full flex items-center justify-center text-[8px] text-gray-300/60">
                          {call.bars}
                        </div>
                      )}
                    </td>
                    <td
                      className={`w-5 h-5 ${put ? barColor(put.bars) : "bg-gray-950"} ${put ? "cursor-pointer hover:ring-1 hover:ring-red-400/50" : ""}`}
                      title={barTooltip(put)}
                      onClick={() => put && put.bars > 0 && onContractClick?.(provider, put.symbol)}
                    >
                      {put && put.bars > 0 && (
                        <div className="w-full h-full flex items-center justify-center text-[8px] text-gray-300/60">
                          {put.bars}
                        </div>
                      )}
                    </td>
                  </Fragment>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex items-center gap-3 px-3 py-2 text-[10px] text-gray-500">
        <span>Coverage:</span>
        <span className="inline-block w-3 h-3 bg-gray-800 rounded-sm"></span> None
        <span className="inline-block w-3 h-3 bg-yellow-900/60 rounded-sm"></span> 1-4 bars
        <span className="inline-block w-3 h-3 bg-emerald-900/60 rounded-sm"></span> 5-19
        <span className="inline-block w-3 h-3 bg-emerald-700/60 rounded-sm"></span> 20-49
        <span className="inline-block w-3 h-3 bg-emerald-500/60 rounded-sm"></span> 50+
        <span className="ml-4">{strikes.length} strikes x {expDates.length} expirations ({expirations.reduce((s, e) => s + e.count, 0)} contracts)</span>
      </div>
    </div>
  );
}

// Fragment polyfill for the JSX
import { Fragment } from "react";
