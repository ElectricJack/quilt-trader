import { useEffect, useMemo, useRef, useState } from "react";
import {
  createChart,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";

export interface BacktestEquityPoint {
  timestamp: string;
  portfolio_value: number;
  cash?: number;
}

export interface BacktestBenchmarkPoint {
  timestamp: string;
  value: number;
}

export interface BacktestTradeMarker {
  timestamp: string;
  side: "buy" | "sell";
  symbol: string;
  quantity: number;
  fill_price: number;
}

interface BacktestChartProps {
  equity: BacktestEquityPoint[];
  benchmark?: BacktestBenchmarkPoint[];
  trades?: BacktestTradeMarker[];
  benchmarkLabel?: string;
  height?: number;
}

type SeriesKey = "portfolio" | "cash" | "benchmark" | "trades";

const SERIES_DEFS: Array<{ key: SeriesKey; label: string; color: string }> = [
  { key: "portfolio", label: "Portfolio value", color: "#6366f1" },
  { key: "cash", label: "Cash", color: "#fbbf24" },
  { key: "benchmark", label: "Benchmark", color: "#34d399" },
  { key: "trades", label: "Trade markers", color: "#f87171" },
];

function toUnix(ts: string): number {
  return Math.floor(new Date(ts).getTime() / 1000);
}

function dedupeSortedByTime<T extends { time: Time }>(rows: T[]): T[] {
  // lightweight-charts requires strictly increasing time. The engine writes
  // an equity row at each bar's close time; two rows can land on the same
  // second when bars are clocked at fine granularity. Keep the LAST entry.
  const byTime = new Map<number, T>();
  for (const r of rows) byTime.set(r.time as number, r);
  return Array.from(byTime.values()).sort(
    (a, b) => (a.time as number) - (b.time as number),
  );
}

export function BacktestChart({
  equity,
  benchmark = [],
  trades = [],
  benchmarkLabel = "Benchmark",
  height = 360,
}: BacktestChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const portfolioSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const cashSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const benchmarkSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);

  const [enabled, setEnabled] = useState<Record<SeriesKey, boolean>>({
    portfolio: true,
    cash: false,
    benchmark: true,
    trades: true,
  });

  // Build series data once per data prop change so re-renders triggered by
  // toggling visibility don't re-parse the arrays.
  const portfolioData = useMemo<LineData<Time>[]>(
    () =>
      dedupeSortedByTime(
        equity
          .map((p) => ({
            time: toUnix(p.timestamp) as Time,
            value: p.portfolio_value,
          }))
          .filter(
            (d) =>
              Number.isFinite(d.time as number) &&
              typeof d.value === "number" &&
              Number.isFinite(d.value),
          ),
      ),
    [equity],
  );

  const cashData = useMemo<LineData<Time>[]>(
    () =>
      dedupeSortedByTime(
        equity
          .filter((p) => p.cash !== undefined)
          .map((p) => ({
            time: toUnix(p.timestamp) as Time,
            value: p.cash as number,
          }))
          .filter(
            (d) =>
              Number.isFinite(d.time as number) &&
              typeof d.value === "number" &&
              Number.isFinite(d.value),
          ),
      ),
    [equity],
  );

  const benchmarkData = useMemo<LineData<Time>[]>(
    () =>
      dedupeSortedByTime(
        benchmark
          .map((p) => ({
            time: toUnix(p.timestamp) as Time,
            value: p.value,
          }))
          .filter(
            (d) =>
              Number.isFinite(d.time as number) &&
              typeof d.value === "number" &&
              Number.isFinite(d.value),
          ),
      ),
    [benchmark],
  );

  // Create chart on mount
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: "#111827" },
        textColor: "#9ca3af",
      },
      grid: {
        vertLines: { color: "#1f2937" },
        horzLines: { color: "#1f2937" },
      },
      crosshair: {
        vertLine: { color: "#6366f1" },
        horzLine: { color: "#6366f1" },
      },
      rightPriceScale: { borderColor: "#374151" },
      timeScale: { borderColor: "#374151", timeVisible: true, secondsVisible: false },
    });

    portfolioSeriesRef.current = chart.addLineSeries({
      color: "#6366f1",
      lineWidth: 2,
      title: "Portfolio value",
    });
    cashSeriesRef.current = chart.addLineSeries({
      color: "#fbbf24",
      lineWidth: 1,
      lineStyle: 2, // dashed
      title: "Cash",
    });
    benchmarkSeriesRef.current = chart.addLineSeries({
      color: "#34d399",
      lineWidth: 1,
      title: "Benchmark",
    });

    chartRef.current = chart;

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry && chartRef.current) {
        chartRef.current.applyOptions({ width: entry.contentRect.width });
      }
    });
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      portfolioSeriesRef.current = null;
      cashSeriesRef.current = null;
      benchmarkSeriesRef.current = null;
    };
  }, [height]);

  // Push data into series
  useEffect(() => {
    if (!portfolioSeriesRef.current) return;
    portfolioSeriesRef.current.setData(portfolioData);
    if (portfolioData.length > 0 && chartRef.current) {
      chartRef.current.timeScale().fitContent();
    }
  }, [portfolioData]);

  useEffect(() => {
    if (!cashSeriesRef.current) return;
    cashSeriesRef.current.setData(cashData);
  }, [cashData]);

  useEffect(() => {
    if (!benchmarkSeriesRef.current) return;
    benchmarkSeriesRef.current.setData(benchmarkData);
  }, [benchmarkData]);

  // Toggle series visibility
  useEffect(() => {
    portfolioSeriesRef.current?.applyOptions({ visible: enabled.portfolio });
  }, [enabled.portfolio]);
  useEffect(() => {
    cashSeriesRef.current?.applyOptions({ visible: enabled.cash });
  }, [enabled.cash]);
  useEffect(() => {
    benchmarkSeriesRef.current?.applyOptions({ visible: enabled.benchmark });
  }, [enabled.benchmark]);

  // Trade markers anchored to the portfolio series
  useEffect(() => {
    if (!portfolioSeriesRef.current) return;
    if (!enabled.trades || trades.length === 0) {
      portfolioSeriesRef.current.setMarkers([]);
      return;
    }
    // Build markers; lightweight-charts requires markers sorted by time.
    const markers: SeriesMarker<Time>[] = trades
      .map((t) => {
        const time = toUnix(t.timestamp) as Time;
        const isBuy = t.side === "buy";
        return {
          time,
          position: isBuy ? ("belowBar" as const) : ("aboveBar" as const),
          color: isBuy ? "#34d399" : "#f87171",
          shape: isBuy ? ("arrowUp" as const) : ("arrowDown" as const),
          text: `${t.side.toUpperCase()} ${t.quantity} ${t.symbol} @ ${t.fill_price.toFixed(2)}`,
        };
      })
      .filter((m) => Number.isFinite(m.time as number))
      .sort((a, b) => (a.time as number) - (b.time as number));
    portfolioSeriesRef.current.setMarkers(markers);
  }, [trades, enabled.trades]);

  return (
    <div className="space-y-2">
      {/* Toggle bar */}
      <div className="flex flex-wrap gap-2">
        {SERIES_DEFS.map((s) => {
          const isOn = enabled[s.key];
          const hasData =
            s.key === "portfolio"
              ? portfolioData.length > 0
              : s.key === "cash"
              ? cashData.length > 0
              : s.key === "benchmark"
              ? benchmarkData.length > 0
              : trades.length > 0;
          const disabled = !hasData;
          const label = s.key === "benchmark" ? benchmarkLabel : s.label;
          return (
            <button
              key={s.key}
              type="button"
              disabled={disabled}
              onClick={() =>
                setEnabled((cur) => ({ ...cur, [s.key]: !cur[s.key] }))
              }
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-xs border transition-colors ${
                disabled
                  ? "border-gray-800 text-gray-600 cursor-not-allowed"
                  : isOn
                  ? "border-gray-700 bg-gray-800 text-gray-100"
                  : "border-gray-800 text-gray-500 hover:text-gray-300"
              }`}
              title={
                disabled
                  ? `${label} — no data available for this run`
                  : `Toggle ${label}`
              }
            >
              <span
                className="w-2 h-2 rounded-full"
                style={{ backgroundColor: disabled ? "#374151" : s.color }}
              />
              {label}
              {!disabled && (
                <span className="text-[10px] text-gray-500">
                  {isOn ? "on" : "off"}
                </span>
              )}
            </button>
          );
        })}
      </div>
      <div
        ref={containerRef}
        className="w-full rounded-lg overflow-hidden border border-gray-800"
        style={{ height }}
      />
    </div>
  );
}
