import { useEffect, useMemo, useRef, useState } from "react";
import { createChart, ColorType, type IChartApi } from "lightweight-charts";
import { MonthlyHeatmap } from "./MonthlyHeatmap";
import { attachChartResize } from "./useChartResize";
import type { BacktestReport } from "../../types";

type View = "heatmap" | "eoy" | "histogram" | "scatter";

interface Props {
  report: BacktestReport;
}

export function ReturnsDistributionSlot({ report }: Props) {
  const [view, setView] = useState<View>("heatmap");
  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-3 flex flex-col h-full min-h-[280px]">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-gray-300">Returns distribution</h3>
        <div className="flex gap-1 text-xs">
          {(["heatmap", "eoy", "histogram", "scatter"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-2 py-1 rounded ${view === v ? "bg-indigo-600 text-white" : "bg-gray-800 text-gray-400 hover:bg-gray-700"}`}
            >{v}</button>
          ))}
        </div>
      </div>
      <div className="flex-1 min-h-0">
        {view === "heatmap" && <MonthlyHeatmap matrix={report.monthly_returns_matrix} />}
        {view === "eoy" && <EoyBar rows={report.eoy_returns} />}
        {view === "histogram" && <Histogram equity={report.equity_curve} />}
        {view === "scatter" && <Scatter equity={report.equity_curve} />}
      </div>
    </div>
  );
}

function dailyReturnsFromEquity(equity: { timestamp: string; portfolio_value: number }[] | null): { ts: string; ret: number }[] {
  if (!equity || equity.length < 2) return [];
  const out: { ts: string; ret: number }[] = [];
  for (let i = 1; i < equity.length; i++) {
    const prev = equity[i - 1].portfolio_value;
    if (prev === 0) continue;
    out.push({ ts: equity[i].timestamp, ret: equity[i].portfolio_value / prev - 1 });
  }
  return out;
}

function EoyBar({ rows }: { rows: BacktestReport["eoy_returns"] }) {
  if (!rows || rows.length === 0) return <div className="text-xs text-gray-500 p-4">No EOY data.</div>;
  return (
    <div className="space-y-1">
      {rows.map((r) => (
        <div key={r.year} className="flex items-center gap-2">
          <span className="w-12 text-xs text-gray-400">{r.year}</span>
          <div className="flex-1 h-4 bg-gray-800 rounded overflow-hidden flex">
            <div
              className="h-full bg-indigo-500"
              style={{ width: `${Math.min(50, Math.abs(r.strategy_pct))}%` }}
              title={`Strategy: ${r.strategy_pct.toFixed(2)}%`}
            />
            {r.benchmark_pct !== null && (
              <div
                className="h-full bg-gray-500 ml-1"
                style={{ width: `${Math.min(50, Math.abs(r.benchmark_pct))}%` }}
                title={`Benchmark: ${r.benchmark_pct.toFixed(2)}%`}
              />
            )}
          </div>
          <span className="w-16 text-right text-xs text-gray-300">{r.strategy_pct.toFixed(1)}%</span>
        </div>
      ))}
    </div>
  );
}

function Histogram({ equity }: { equity: BacktestReport["equity_curve"] }) {
  const data = useMemo(() => dailyReturnsFromEquity(equity).map((d) => d.ret), [equity]);
  const bins = useMemo(() => {
    const N = 30;
    if (data.length === 0) return [];
    const min = Math.min(...data); const max = Math.max(...data);
    const step = (max - min) / N || 1;
    const counts = new Array(N).fill(0);
    for (const v of data) {
      const idx = Math.min(N - 1, Math.max(0, Math.floor((v - min) / step)));
      counts[idx]++;
    }
    return counts.map((c, i) => ({ bin_start: min + i * step, count: c }));
  }, [data]);
  if (bins.length === 0) return <div className="text-xs text-gray-500 p-4">No data.</div>;
  const max = Math.max(...bins.map((b) => b.count));
  return (
    <div className="flex items-end gap-px w-full h-full min-h-[200px]">
      {bins.map((b, i) => (
        <div
          key={i}
          className="flex-1 bg-indigo-500"
          style={{ height: `${(b.count / max) * 100}%` }}
          title={`${(b.bin_start * 100).toFixed(2)}% — count ${b.count}`}
        />
      ))}
    </div>
  );
}

function Scatter({ equity }: { equity: BacktestReport["equity_curve"] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const chart: IChartApi = createChart(el, {
      width: el.clientWidth,
      height: el.clientHeight || 220,
      layout: { background: { type: ColorType.Solid, color: "#0f172a" }, textColor: "#9ca3af" },
      grid: { vertLines: { color: "#1f2937" }, horzLines: { color: "#1f2937" } },
    });
    const detach = attachChartResize(el, chart);
    const series = chart.addHistogramSeries({ color: "#6366f1" });
    const points = dailyReturnsFromEquity(equity).map((p) => ({
      time: (Date.parse(p.ts) / 1000) as any,
      value: p.ret * 100,
      color: p.ret >= 0 ? "#22c55e" : "#ef4444",
    }));
    series.setData(points);
    chart.timeScale().fitContent();
    return () => { detach(); chart.remove(); };
  }, [equity]);
  return <div ref={ref} className="w-full h-full min-h-[200px]" />;
}
