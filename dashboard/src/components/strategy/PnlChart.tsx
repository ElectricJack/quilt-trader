// dashboard/src/components/strategy/PnlChart.tsx
// Renders the P&L of the current strategy across a range of spot prices.
// Two series are drawn:
//   - "At expiry" (faint, dashed) — pure intrinsic-value payoff.
//   - "At date"   (solid) — Black-Scholes mark using `scrubMs`.
//
// Breakeven markers (zero-crossings of the expiry curve) are surfaced as
// price-axis labels. The x-axis encodes spot price using lightweight-charts'
// numeric "time" type — clean visually and lets us reuse the chart's
// interaction model.

import { useEffect, useRef, useMemo } from "react";
import {
  createChart,
  ColorType,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type Time,
} from "lightweight-charts";
import type { OptionLeg } from "../../lib/options";
import { pnlCurve, strategyPnl } from "../../lib/options";

interface PnlChartProps {
  legs: OptionLeg[];
  spot: number | undefined;
  scrubMs: number;
  height?: number;
}

function findBreakevens(curve: { x: number; y: number }[]): number[] {
  const out: number[] = [];
  for (let i = 1; i < curve.length; i++) {
    const a = curve[i - 1];
    const b = curve[i];
    if ((a.y <= 0 && b.y >= 0) || (a.y >= 0 && b.y <= 0)) {
      if (b.y === a.y) {
        out.push(a.x);
      } else {
        // Linear interpolate the zero crossing
        const t = -a.y / (b.y - a.y);
        out.push(a.x + t * (b.x - a.x));
      }
    }
  }
  return out;
}

export function PnlChart({ legs, spot, scrubMs, height = 320 }: PnlChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const expirySeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const dateSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);

  // Compute spot range from the legs' strikes (or fall back to spot ± 20%).
  const { rangeLo, rangeHi } = useMemo(() => {
    if (legs.length === 0) {
      const s = spot ?? 100;
      return { rangeLo: s * 0.8, rangeHi: s * 1.2 };
    }
    const strikes = legs.map((l) => l.strike);
    const sLo = Math.min(...strikes);
    const sHi = Math.max(...strikes);
    const pad = Math.max((sHi - sLo) * 1.0, sHi * 0.1);
    const center = spot ?? (sLo + sHi) / 2;
    const lo = Math.min(sLo - pad, center * 0.85);
    const hi = Math.max(sHi + pad, center * 1.15);
    return { rangeLo: Math.max(0.01, lo), rangeHi: hi };
  }, [legs, spot]);

  const { expiryData, dateData, breakevens, currentPnl } = useMemo(() => {
    if (legs.length === 0) {
      return { expiryData: [], dateData: [], breakevens: [], currentPnl: 0 };
    }
    const e = pnlCurve(legs, [rangeLo, rangeHi], "expiry", 200);
    const d = pnlCurve(legs, [rangeLo, rangeHi], scrubMs, 200);
    const bes = findBreakevens(e);
    const cur = spot != null ? strategyPnl(legs, spot, scrubMs) : 0;
    return { expiryData: e, dateData: d, breakevens: bes, currentPnl: cur };
  }, [legs, rangeLo, rangeHi, scrubMs, spot]);

  // Create the chart on mount.
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
      timeScale: {
        borderColor: "#374151",
        timeVisible: false,
        secondsVisible: false,
        tickMarkFormatter: (t: number) => t.toFixed(2),
      },
      localization: {
        priceFormatter: (p: number) =>
          p >= 0 ? `+${p.toFixed(2)}` : p.toFixed(2),
      },
    });

    const expirySeries = chart.addLineSeries({
      color: "#6b7280",
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      title: "At expiry",
    });
    const dateSeries = chart.addLineSeries({
      color: "#6366f1",
      lineWidth: 2,
      priceLineVisible: false,
      title: "At date",
    });

    chartRef.current = chart;
    expirySeriesRef.current = expirySeries;
    dateSeriesRef.current = dateSeries;

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
      expirySeriesRef.current = null;
      dateSeriesRef.current = null;
    };
  }, [height]);

  // Update data on every change.
  useEffect(() => {
    const chart = chartRef.current;
    const eSeries = expirySeriesRef.current;
    const dSeries = dateSeriesRef.current;
    if (!chart || !eSeries || !dSeries) return;

    // lightweight-charts wants strictly-ascending unique integer "time" values.
    // We map spot price → integer ticks scaled by 1000 so sub-cent spots stay unique.
    function toLineData(rows: { x: number; y: number }[]): LineData<Time>[] {
      const seen = new Set<number>();
      const out: LineData<Time>[] = [];
      for (const { x, y } of rows) {
        const t = Math.round(x * 1000);
        if (seen.has(t)) continue;
        seen.add(t);
        out.push({ time: t as unknown as Time, value: y });
      }
      out.sort(
        (a, b) =>
          (a.time as unknown as number) - (b.time as unknown as number)
      );
      return out;
    }

    eSeries.setData(toLineData(expiryData));
    dSeries.setData(toLineData(dateData));

    // Clear and redraw breakeven + spot reference lines on the *date* series
    // (lightweight-charts has no clearPriceLines, but recreating the series
    //  is heavy — instead we put the price lines on a single series).
    // We re-create the date series cleanly to drop old price-lines.
    // (Cheap because there's only one.)
    // Skip if there's nothing to show.

    // Use the chart's time scale to fit content.
    chart.timeScale().fitContent();
  }, [expiryData, dateData]);

  // Render breakeven labels + current P&L as plain DOM since lightweight-charts
  // price-lines persist; this keeps the chart effect idempotent.
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900">
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-800">
        <h3 className="text-sm font-semibold text-gray-200">P&amp;L</h3>
        <div className="flex items-center gap-3 text-xs">
          <span className="text-gray-500">
            <span className="inline-block w-3 h-px bg-gray-500 align-middle mr-1" /> At expiry
          </span>
          <span className="text-gray-300">
            <span className="inline-block w-3 h-0.5 bg-indigo-500 align-middle mr-1" /> At date
          </span>
        </div>
      </div>
      {legs.length === 0 ? (
        <div className="px-3 py-12 text-center text-sm text-gray-500">
          Add legs to see the P&amp;L diagram.
        </div>
      ) : (
        <>
          <div ref={containerRef} className="w-full" style={{ height }} />
          <div className="flex flex-wrap items-center gap-4 px-3 py-2 border-t border-gray-800 text-xs text-gray-400">
            <span>
              Now P&amp;L:{" "}
              <span
                className={
                  currentPnl > 0
                    ? "text-emerald-400 font-medium tabular-nums"
                    : currentPnl < 0
                      ? "text-red-400 font-medium tabular-nums"
                      : "text-gray-300 tabular-nums"
                }
              >
                {currentPnl >= 0 ? "+" : ""}
                {currentPnl.toFixed(2)}
              </span>
            </span>
            {breakevens.length > 0 && (
              <span>
                Breakeven{breakevens.length === 1 ? "" : "s"}:{" "}
                <span className="text-gray-200 tabular-nums">
                  {breakevens.map((b) => b.toFixed(2)).join(", ")}
                </span>
              </span>
            )}
            <span>
              Range:{" "}
              <span className="text-gray-300 tabular-nums">
                {rangeLo.toFixed(2)} – {rangeHi.toFixed(2)}
              </span>
            </span>
          </div>
        </>
      )}
    </div>
  );
}
