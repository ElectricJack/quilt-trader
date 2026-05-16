import { useEffect, useRef, useState } from "react";
import { createChart, ColorType, type IChartApi } from "lightweight-charts";
import type { BacktestReport } from "../../types";
import { attachChartResize } from "./useChartResize";

interface Props { report: BacktestReport; }

export function DrawdownSlot({ report }: Props) {
  const [view, setView] = useState<"underwater" | "topN">("underwater");
  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-3 flex flex-col h-full min-h-[280px]">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-gray-300">Drawdown</h3>
        <div className="flex gap-1 text-xs">
          {(["underwater", "topN"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-2 py-1 rounded ${view === v ? "bg-indigo-600 text-white" : "bg-gray-800 text-gray-400 hover:bg-gray-700"}`}
            >{v === "underwater" ? "Underwater" : "Top periods"}</button>
          ))}
        </div>
      </div>
      <div className="flex-1 min-h-0">
        {view === "underwater" ? <Underwater curve={report.drawdown_curve} /> : <TopN periods={report.drawdown_periods} />}
      </div>
    </div>
  );
}

function Underwater({ curve }: { curve: BacktestReport["drawdown_curve"] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || !curve) return;
    const chart: IChartApi = createChart(el, {
      width: el.clientWidth,
      height: el.clientHeight || 220,
      layout: { background: { type: ColorType.Solid, color: "#0f172a" }, textColor: "#9ca3af" },
      grid: { vertLines: { color: "#1f2937" }, horzLines: { color: "#1f2937" } },
    });
    const detach = attachChartResize(el, chart);
    const series = chart.addAreaSeries({
      lineColor: "#ef4444", topColor: "rgba(239,68,68,0.3)", bottomColor: "rgba(239,68,68,0.0)",
    });
    series.setData(curve.map((p) => ({
      time: (Date.parse(p.timestamp) / 1000) as any,
      value: p.drawdown_pct * 100,
    })));
    chart.timeScale().fitContent();
    return () => { detach(); chart.remove(); };
  }, [curve]);
  return <div ref={ref} className="w-full h-full min-h-[200px]" />;
}

function TopN({ periods }: { periods: BacktestReport["drawdown_periods"] }) {
  if (!periods || periods.length === 0) return <div className="text-xs text-gray-500 p-4">No drawdown periods.</div>;
  const max = Math.max(...periods.map((p) => p.depth));
  return (
    <div className="space-y-1">
      {periods.map((p, i) => (
        <div key={i} className="flex items-center gap-2 text-xs">
          <span className="w-32 text-gray-400 font-mono truncate">
            {p.start.split("T")[0]} → {p.recovered ? p.recovered.split("T")[0] : "ongoing"}
          </span>
          <div className="flex-1 h-3 bg-gray-800 rounded overflow-hidden">
            <div
              className="h-full bg-red-500"
              style={{ width: `${(p.depth / max) * 100}%` }}
            />
          </div>
          <span className="w-16 text-right text-red-400">{(p.depth * 100).toFixed(1)}%</span>
          <span className="w-12 text-right text-gray-400">{p.days}d</span>
        </div>
      ))}
    </div>
  );
}
