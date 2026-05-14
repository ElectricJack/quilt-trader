import { useEffect, useRef } from "react";
import {
  createChart,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type CandlestickData,
  type LogicalRange,
  type Time,
} from "lightweight-charts";
import type { MarketDataBar } from "../types";

export type ChartType = "line" | "bars";

interface PriceChartProps {
  bars: MarketDataBar[];
  height?: number;
  chartType?: ChartType;
}

export function PriceChart({ bars, height = 280, chartType = "bars" }: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | ISeriesApi<"Candlestick"> | null>(null);
  // Carries the visible logical range across chart re-creations (chartType
  // switch, height change) so the user keeps their pan/zoom position.
  const savedRangeRef = useRef<LogicalRange | null>(null);
  const restorePendingRef = useRef(false);

  // Create chart + series. Recreates when chartType changes so the right
  // series implementation is mounted.
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
      timeScale: { borderColor: "#374151", timeVisible: true },
    });

    // Traditional OHLC candlesticks: a thin wick from low to high with a filled
    // body for the open→close range, green when close > open and red otherwise.
    const series =
      chartType === "bars"
        ? chart.addCandlestickSeries({
            upColor: "#22c55e",
            downColor: "#ef4444",
            borderUpColor: "#22c55e",
            borderDownColor: "#ef4444",
            wickUpColor: "#22c55e",
            wickDownColor: "#ef4444",
          })
        : chart.addLineSeries({
            color: "#6366f1",
            lineWidth: 2,
          });

    chartRef.current = chart;
    seriesRef.current = series;

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry && chartRef.current) {
        chartRef.current.applyOptions({
          width: entry.contentRect.width,
        });
      }
    });
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      // Snapshot the visible range before tearing down so the next chart can
      // restore it (preserves pan/zoom across chartType switches).
      const range = chart.timeScale().getVisibleLogicalRange();
      if (range) {
        savedRangeRef.current = range;
        restorePendingRef.current = true;
      }
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [height, chartType]);

  // Update series data when bars prop or chart type changes.
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;

    // Build (time, OHLC) tuples, drop invalid rows, sort, dedupe by timestamp
    type Row = { time: number; open: number; high: number; low: number; close: number };
    const rows: Row[] = [];
    for (const bar of bars) {
      const time = Math.floor(new Date(bar.timestamp).getTime() / 1000);
      if (
        !Number.isFinite(time) ||
        !Number.isFinite(bar.open) ||
        !Number.isFinite(bar.high) ||
        !Number.isFinite(bar.low) ||
        !Number.isFinite(bar.close)
      ) {
        continue;
      }
      rows.push({ time, open: bar.open, high: bar.high, low: bar.low, close: bar.close });
    }
    rows.sort((a, b) => a.time - b.time);

    const seen = new Map<number, Row>();
    for (const r of rows) seen.set(r.time, r);
    const deduped = Array.from(seen.values()).sort((a, b) => a.time - b.time);

    if (chartType === "bars") {
      const data: CandlestickData<Time>[] = deduped.map((r) => ({
        time: r.time as Time,
        open: r.open,
        high: r.high,
        low: r.low,
        close: r.close,
      }));
      (series as ISeriesApi<"Candlestick">).setData(data);
    } else {
      const data: LineData<Time>[] = deduped.map((r) => ({
        time: r.time as Time,
        value: r.close,
      }));
      (series as ISeriesApi<"Line">).setData(data);
    }

    if (deduped.length > 0 && chartRef.current) {
      if (restorePendingRef.current && savedRangeRef.current) {
        chartRef.current.timeScale().setVisibleLogicalRange(savedRangeRef.current);
        restorePendingRef.current = false;
      } else {
        chartRef.current.timeScale().fitContent();
      }
    }
  }, [bars, chartType]);

  return (
    <div
      ref={containerRef}
      className="w-full rounded-lg overflow-hidden border border-gray-800"
      style={{ height }}
    />
  );
}
