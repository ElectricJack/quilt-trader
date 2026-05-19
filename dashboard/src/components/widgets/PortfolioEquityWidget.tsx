import { useState } from "react";
import { Widget } from "../Widget";
import { StackedAreaChart, type StackedAreaBand } from "../StackedAreaChart";
import { usePortfolioEquity } from "../../api/hooks";
import { useOverviewFilter } from "../../stores/overviewFilter";

type Range = "1d" | "1w" | "1m" | "all";

const RANGES: Range[] = ["1d", "1w", "1m", "all"];
const COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#a855f7", "#ec4899"];

function formatDollar(v: number): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(v);
}

export function PortfolioEquityWidget() {
  const [range, setRange] = useState<Range>("1m");
  const { data, isLoading } = usePortfolioEquity(range);
  const { selectedIds } = useOverviewFilter();

  const filteredAccounts = (data?.accounts ?? []).filter(
    (a) => selectedIds.size === 0 || selectedIds.has(a.account_id)
  );

  const bands: StackedAreaBand[] = filteredAccounts.map((a, i) => ({
    key: a.account_id,
    name: a.account_name,
    color: COLORS[i % COLORS.length],
    points: a.points,
  }));

  const latestTotal = bands.reduce((sum, b) => {
    const last = b.points[b.points.length - 1];
    return sum + (last?.value ?? 0);
  }, 0);
  const firstTotal = bands.reduce((sum, b) => sum + (b.points[0]?.value ?? 0), 0);
  const delta = latestTotal - firstTotal;
  const deltaPct = firstTotal > 0 ? (delta / firstTotal) * 100 : 0;

  return (
    <Widget title="Portfolio Equity" isLoading={isLoading} bodyClass="">
      <div className="flex items-start justify-between px-3 py-3 pb-1.5">
        <div>
          <div className="text-2xl font-bold text-white leading-tight">{formatDollar(latestTotal)}</div>
          <div className={`text-xs ${delta >= 0 ? "text-emerald-400" : "text-red-400"}`}>
            {delta >= 0 ? "+" : ""}{formatDollar(delta)} ({deltaPct.toFixed(1)}%) {range}
          </div>
        </div>
        <div className="flex gap-1">
          {RANGES.map((r) => (
            <button
              key={r}
              onClick={() => setRange(r)}
              className={`px-2 py-0.5 rounded text-[10px] uppercase ${
                r === range ? "bg-indigo-500 text-white" : "bg-gray-800 text-gray-400"
              }`}
            >
              {r}
            </button>
          ))}
        </div>
      </div>
      {bands.length > 0 && (
        <div className="flex flex-wrap gap-3 px-3 pb-1 text-[10px] text-gray-400">
          {bands.map((b) => (
            <span key={b.key} className="inline-flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-sm" style={{ background: b.color }} />
              {b.name}
            </span>
          ))}
        </div>
      )}
      <StackedAreaChart bands={bands} height={200} />
    </Widget>
  );
}
