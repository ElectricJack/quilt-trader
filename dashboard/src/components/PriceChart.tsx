import { useEffect, useRef } from "react";
import {
  createChart,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type Time,
} from "lightweight-charts";
import type { MarketDataBar } from "../types";

interface PriceChartProps {
  bars: MarketDataBar[];
  height?: number;
}

export function PriceChart({ bars, height = 280 }: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);

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
      timeScale: { borderColor: "#374151", timeVisible: true },
    });

    const series = chart.addLineSeries({
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
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [height]);

  // Update series data when bars prop changes
  useEffect(() => {
    if (!seriesRef.current) return;

    // Convert, filter bad values, sort, deduplicate consecutive same-time entries
    const raw: LineData<Time>[] = bars
      .map((bar) => ({
        time: Math.floor(new Date(bar.timestamp).getTime() / 1000) as Time,
        value: bar.close,
      }))
      .filter(
        (d) =>
          Number.isFinite(d.time as number) &&
          typeof d.value === "number" &&
          Number.isFinite(d.value)
      )
      .sort((a, b) => (a.time as number) - (b.time as number));

    // Deduplicate: keep last bar when multiple bars share the same timestamp
    const seen = new Map<number, LineData<Time>>();
    for (const d of raw) {
      seen.set(d.time as number, d);
    }
    const seriesData = Array.from(seen.values()).sort(
      (a, b) => (a.time as number) - (b.time as number)
    );

    seriesRef.current.setData(seriesData);

    if (seriesData.length > 0 && chartRef.current) {
      chartRef.current.timeScale().fitContent();
    }
  }, [bars]);

  return (
    <div
      ref={containerRef}
      className="w-full rounded-lg overflow-hidden border border-gray-800"
      style={{ height }}
    />
  );
}
