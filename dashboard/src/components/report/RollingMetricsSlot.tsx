import { useEffect, useRef, useState } from "react";
import { createChart, ColorType, type IChartApi, type ISeriesApi } from "lightweight-charts";
import type { BacktestReport, BacktestRollingPoint } from "../../types";
import { attachChartResize } from "./useChartResize";

type Series = "sharpe" | "sortino" | "vol" | "beta";
const SERIES_META: Record<Series, { color: string; label: string }> = {
  sharpe: { color: "#6366f1", label: "Rolling Sharpe" },
  sortino: { color: "#22c55e", label: "Rolling Sortino" },
  vol: { color: "#facc15", label: "Rolling Volatility" },
  beta: { color: "#ef4444", label: "Rolling Beta" },
};

interface Props { report: BacktestReport; }

export function RollingMetricsSlot({ report }: Props) {
  const [enabled, setEnabled] = useState<Record<Series, boolean>>({
    sharpe: true, sortino: false, vol: false, beta: false,
  });
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<Partial<Record<Series, ISeriesApi<"Line">>>>({});

  useEffect(() => {
    const el = ref.current;
    if (!el || !report.rolling_metrics) return;
    const chart = createChart(el, {
      width: el.clientWidth,
      height: el.clientHeight || 220,
      layout: { background: { type: ColorType.Solid, color: "#0f172a" }, textColor: "#9ca3af" },
      grid: { vertLines: { color: "#1f2937" }, horzLines: { color: "#1f2937" } },
    });
    chartRef.current = chart;
    const detach = attachChartResize(el, chart);
    chart.timeScale().fitContent();
    return () => { detach(); chart.remove(); chartRef.current = null; seriesRef.current = {}; };
  }, [report.rolling_metrics]);

  useEffect(() => {
    if (!chartRef.current || !report.rolling_metrics) return;
    const chart = chartRef.current;
    const points = report.rolling_metrics.points;
    (Object.keys(SERIES_META) as Series[]).forEach((k) => {
      const has = !!seriesRef.current[k];
      if (enabled[k] && !has) {
        const s = chart.addLineSeries({ color: SERIES_META[k].color, lineWidth: 2 });
        s.setData(points
          .filter((p: BacktestRollingPoint) => p[k] !== null && p[k] !== undefined)
          .map((p: BacktestRollingPoint) => ({
            time: (Date.parse(p.timestamp) / 1000) as any,
            value: p[k] as number,
          })));
        seriesRef.current[k] = s;
      } else if (!enabled[k] && has) {
        chart.removeSeries(seriesRef.current[k]!);
        delete seriesRef.current[k];
      }
    });
  }, [enabled, report.rolling_metrics]);

  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-3 flex flex-col h-full min-h-[280px]">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-gray-300">
          Rolling metrics ({report.rolling_metrics?.window_days ?? 90}d window)
        </h3>
        <div className="flex gap-1 text-xs">
          {(Object.keys(SERIES_META) as Series[]).map((k) => (
            <button
              key={k}
              onClick={() => setEnabled((e) => ({ ...e, [k]: !e[k] }))}
              className={`px-2 py-1 rounded ${enabled[k] ? "text-white" : "text-gray-400 bg-gray-800 hover:bg-gray-700"}`}
              style={enabled[k] ? { background: SERIES_META[k].color } : undefined}
            >{SERIES_META[k].label}</button>
          ))}
        </div>
      </div>
      <div ref={ref} className="w-full flex-1 min-h-[200px]" />
    </div>
  );
}
