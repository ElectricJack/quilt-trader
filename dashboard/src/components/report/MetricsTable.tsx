import { Fragment } from "react";
import type { BacktestKeyMetrics } from "../../types";

type Fmt = "pct" | "num" | "int";

interface Row {
  label: string;
  key: keyof BacktestKeyMetrics | string;
  fmt: Fmt;
  strategyOnly?: boolean;
}

interface Group {
  title: string;
  rows: Row[];
}

const GROUPS: Group[] = [
  { title: "Returns", rows: [
    { label: "Total Return", key: "total_return", fmt: "pct" },
    { label: "CAGR (Annual)", key: "cagr", fmt: "pct" },
    { label: "Volatility (ann.)", key: "volatility", fmt: "pct" },
  ]},
  { title: "Risk-adjusted", rows: [
    { label: "Sharpe", key: "sharpe_ratio", fmt: "num" },
    { label: "Sortino", key: "sortino_ratio", fmt: "num" },
    { label: "Omega", key: "omega", fmt: "num" },
  ]},
  { title: "Drawdown", rows: [
    { label: "Max Drawdown", key: "max_drawdown", fmt: "pct" },
    { label: "Longest DD Days", key: "longest_drawdown_days", fmt: "int" },
    { label: "Avg Drawdown", key: "avg_drawdown", fmt: "pct" },
    { label: "Avg DD Days", key: "avg_drawdown_days", fmt: "int" },
    { label: "Ulcer Index", key: "ulcer_index", fmt: "num" },
  ]},
  { title: "Tail risk", rows: [
    { label: "Daily VaR (95%)", key: "daily_var", fmt: "pct" },
    { label: "Daily cVaR", key: "daily_cvar", fmt: "pct" },
    { label: "Skew", key: "skew", fmt: "num" },
    { label: "Kurtosis", key: "kurtosis", fmt: "num" },
  ]},
  { title: "Period returns", rows: [
    { label: "YTD", key: "ytd", fmt: "pct" },
    { label: "1Y", key: "1y", fmt: "pct" },
    { label: "3Y (annualized)", key: "3y", fmt: "pct" },
  ]},
  { title: "Distribution", rows: [
    { label: "Best Day", key: "best_day", fmt: "pct" },
    { label: "Worst Day", key: "worst_day", fmt: "pct" },
    { label: "Best Month", key: "best_month", fmt: "pct" },
    { label: "Worst Month", key: "worst_month", fmt: "pct" },
  ]},
  { title: "Win rates", rows: [
    { label: "Time in Market", key: "time_in_market", fmt: "pct" },
    { label: "Win Days %", key: "win_days", fmt: "pct" },
    { label: "Win Month %", key: "win_month", fmt: "pct" },
  ]},
  { title: "vs Benchmark", rows: [
    { label: "Beta", key: "beta", fmt: "num", strategyOnly: true },
    { label: "Alpha", key: "alpha", fmt: "num", strategyOnly: true },
    { label: "Correlation", key: "correlation", fmt: "num", strategyOnly: true },
  ]},
];

function fmtValue(v: number | undefined | null, fmt: Fmt): string {
  if (v === undefined || v === null || Number.isNaN(v)) return "—";
  if (fmt === "pct") return `${(v * 100).toFixed(2)}%`;
  if (fmt === "int") return Math.round(v).toString();
  return v.toFixed(2);
}

interface Props {
  strategy: BacktestKeyMetrics | undefined;
  benchmark: BacktestKeyMetrics | undefined;
}

export function MetricsTable({ strategy, benchmark }: Props) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded">
      <div className="px-3 py-2 border-b border-gray-800 text-sm font-semibold text-gray-300">
        Key Performance Metrics
      </div>
      <table className="w-full text-sm">
        <thead className="bg-gray-800 text-xs uppercase text-gray-400">
          <tr>
            <th className="text-left p-2">Metric</th>
            <th className="text-right p-2">Strategy</th>
            <th className="text-right p-2">Benchmark</th>
          </tr>
        </thead>
        <tbody>
          {GROUPS.map((g) => (
            <Fragment key={g.title}>
              <tr className="bg-gray-800/40">
                <td colSpan={3} className="px-2 py-1 text-[10px] uppercase text-gray-500">
                  {g.title}
                </td>
              </tr>
              {g.rows.map((row) => {
                const sv = strategy ? strategy[row.key as keyof BacktestKeyMetrics] : undefined;
                const bv = benchmark ? benchmark[row.key as keyof BacktestKeyMetrics] : undefined;
                return (
                  <tr key={row.label} className="border-t border-gray-800">
                    <td className="p-2 text-gray-300">{row.label}</td>
                    <td className="p-2 text-right text-gray-200">{fmtValue(sv, row.fmt)}</td>
                    <td className="p-2 text-right text-gray-400">
                      {row.strategyOnly ? "—" : fmtValue(bv, row.fmt)}
                    </td>
                  </tr>
                );
              })}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}
