import { useEffect, useRef } from "react";
import {
  createChart,
  ColorType,
  type IChartApi,
  type AreaData,
  type Time,
} from "lightweight-charts";

export interface StackedAreaBand {
  key: string;
  name: string;
  color: string;
  points: { timestamp: string; value: number }[];
}

interface StackedAreaChartProps {
  bands: StackedAreaBand[];
  height?: number;
}

const BAND_COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#a855f7", "#ec4899"];

export function StackedAreaChart({ bands, height = 200 }: StackedAreaChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

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
      rightPriceScale: { borderColor: "#374151" },
      timeScale: { borderColor: "#374151", timeVisible: true },
    });
    chartRef.current = chart;

    const observer = new ResizeObserver((entries) => {
      const e = entries[0];
      if (e && chartRef.current) {
        chartRef.current.applyOptions({ width: e.contentRect.width });
      }
    });
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [height]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || bands.length === 0) return;

    // Build a unified timeline
    const allTimestamps = new Set<number>();
    bands.forEach((b) =>
      b.points.forEach((p) =>
        allTimestamps.add(new Date(p.timestamp).getTime() / 1000)
      )
    );
    const timeline = [...allTimestamps].sort((a, b) => a - b);

    // Per band: align to timeline (last-known carry-forward), then accumulate vertically.
    const bandValuesAtTime: Map<number, number[]> = new Map();
    bands.forEach((band, bi) => {
      const sorted = [...band.points].sort(
        (a, b) =>
          new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
      );
      let cursor = 0;
      let last = 0;
      timeline.forEach((t) => {
        while (
          cursor < sorted.length &&
          new Date(sorted[cursor].timestamp).getTime() / 1000 <= t
        ) {
          last = sorted[cursor].value;
          cursor++;
        }
        const arr = bandValuesAtTime.get(t) ?? [];
        arr[bi] = last;
        bandValuesAtTime.set(t, arr);
      });
    });

    bands.forEach((band, bi) => {
      const color = band.color || BAND_COLORS[bi % BAND_COLORS.length];
      const series = chart.addAreaSeries({
        lineColor: color,
        topColor: color + "cc",
        bottomColor: color + "33",
        lineWidth: 1,
      });
      const data: AreaData<Time>[] = timeline.map((t) => {
        const values = bandValuesAtTime.get(t) ?? [];
        const cumulative = values.slice(0, bi + 1).reduce((s, v) => s + (v || 0), 0);
        return { time: t as Time, value: cumulative };
      });
      series.setData(data);
    });

    chart.timeScale().fitContent();
  }, [bands]);

  return (
    <div
      ref={containerRef}
      style={{ width: "100%", height, display: "block" }}
    />
  );
}
