import { useEffect, useRef } from "react";
import {
  createChart,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type CandlestickData,
  type LogicalRange,
  type Range,
  type Time,
} from "lightweight-charts";
import type { MarketDataBar } from "../types";
import { wsManager } from "../api/websocket";

export type ChartType = "line" | "bars";

interface LiveTick {
  type: string;
  broker: string;
  symbol: string;
  price: number;
  size: number;
  timestamp: string;
}

interface PriceChartProps {
  bars: MarketDataBar[];
  height?: number;
  chartType?: ChartType;
  /** When set, the chart subscribes to live_data:<broker>:<symbol> and updates
   * the current candle in real-time as ticks arrive. Strip the "_live" suffix
   * from the provider string before passing (e.g. "coinbase", not "coinbase_live"). */
  liveBroker?: string;
  liveSymbol?: string;
  /**
   * Called when the user pans close to the left edge of loaded data.
   * Receives the ISO-8601 timestamp of the earliest loaded bar.
   * Use this to trigger a fetch for older bars.
   */
  loadEarlier?: (beforeTimestamp: string) => void;
  /** When true, shows a subtle "loading older data…" indicator at the top of
   * the chart. */
  fetchingMore?: boolean;
}

export function PriceChart({ bars, height = 280, chartType = "bars", liveBroker, liveSymbol, loadEarlier, fetchingMore = false }: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | ISeriesApi<"Candlestick"> | null>(null);
  // Carries the visible logical range across chart re-creations (chartType
  // switch, height change) so the user keeps their pan/zoom position.
  const savedRangeRef = useRef<LogicalRange | null>(null);
  const restorePendingRef = useRef(false);
  // Tracks the live candle state for real-time updates.
  const liveCandleRef = useRef<{ time: number; open: number; high: number; low: number; close: number } | null>(null);

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

  // Track whether we have ever fitted the chart for this bars dataset.
  // We only fitContent on the very first load; subsequent bar merges (paging)
  // preserve the user's current pan position.
  const didFitRef = useRef(false);

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

    // Save the current visible time range so we can restore it after setData
    // (setData resets the viewport). Only save if we've already fitted once —
    // i.e. the user may have panned.
    const chart = chartRef.current;
    const currentRange = didFitRef.current
      ? chart?.timeScale().getVisibleRange() ?? null
      : null;

    // If a live candle is ahead of the historical data, keep it in the
    // series after setData so it doesn't disappear when the parent re-renders
    // (the Data page polls active downloads every 2s, which recreates the
    // bars array reference and re-runs this effect). Without this, the live
    // bar gets removed by setData and re-added by the next tick — a 2-second
    // add/remove flicker.
    const liveAhead =
      liveCandleRef.current &&
      (deduped.length === 0 ||
        liveCandleRef.current.time > deduped[deduped.length - 1].time)
        ? liveCandleRef.current
        : null;

    if (chartType === "bars") {
      const data: CandlestickData<Time>[] = deduped.map((r) => ({
        time: r.time as Time,
        open: r.open,
        high: r.high,
        low: r.low,
        close: r.close,
      }));
      (series as ISeriesApi<"Candlestick">).setData(data);
      if (liveAhead) {
        (series as ISeriesApi<"Candlestick">).update({
          time: liveAhead.time as Time,
          open: liveAhead.open,
          high: liveAhead.high,
          low: liveAhead.low,
          close: liveAhead.close,
        });
      }
    } else {
      const data: LineData<Time>[] = deduped.map((r) => ({
        time: r.time as Time,
        value: r.close,
      }));
      (series as ISeriesApi<"Line">).setData(data);
      if (liveAhead) {
        (series as ISeriesApi<"Line">).update({
          time: liveAhead.time as Time,
          value: liveAhead.close,
        });
      }
    }

    if (deduped.length > 0 && chart) {
      if (restorePendingRef.current && savedRangeRef.current) {
        // Restoring after a chartType switch — use logical range.
        chart.timeScale().setVisibleLogicalRange(savedRangeRef.current);
        restorePendingRef.current = false;
        didFitRef.current = true;
      } else if (currentRange) {
        // Preserve the user's pan position after a page merge.
        try {
          chart.timeScale().setVisibleRange(currentRange);
        } catch {
          // If the new data doesn't cover the saved range, fall through.
        }
      } else if (!didFitRef.current) {
        // First load — fit everything into view.
        chart.timeScale().fitContent();
        didFitRef.current = true;
      }
    }

    // Seed the live candle from the last historical bar — but only if we
    // don't already have a live candle that's ahead of historical data.
    // Otherwise the parent's 2s polling re-runs this effect and resets the
    // live bar back to the prior closed minute on every render.
    if (deduped.length > 0) {
      const last = deduped[deduped.length - 1];
      if (!liveCandleRef.current || liveCandleRef.current.time <= last.time) {
        liveCandleRef.current = { ...last };
      }
    }
  }, [bars, chartType]);

  // Reset the fit flag when the chart instance is recreated (chartType switch)
  // so that the first data load after a switch calls fitContent.
  useEffect(() => {
    didFitRef.current = false;
  }, [chartType]);

  // ── Edge-detection: trigger loadEarlier when nearing the left edge ────────
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !loadEarlier || bars.length === 0) return;

    const firstBarTime = Math.floor(
      new Date(bars[0].timestamp).getTime() / 1000
    );
    const lastBarTime = Math.floor(
      new Date(bars[bars.length - 1].timestamp).getTime() / 1000
    );
    const loadedSpan = lastBarTime - firstBarTime;

    const handler = (range: Range<Time> | null) => {
      if (!range) return;
      const from = range.from as number;
      // Trigger when the left edge of the viewport is within 10% of the
      // loaded span from the earliest bar.
      const threshold = firstBarTime + loadedSpan * 0.1;
      if (from <= threshold) {
        loadEarlier(bars[0].timestamp);
      }
    };

    chart.timeScale().subscribeVisibleTimeRangeChange(handler);
    return () => {
      try {
        chart.timeScale().unsubscribeVisibleTimeRangeChange(handler);
      } catch {
        // ignore if chart already removed
      }
    };
  }, [bars, loadEarlier]);

  // Subscribe to live ticks when liveBroker + liveSymbol are provided.
  useEffect(() => {
    if (!liveBroker || !liveSymbol) return;

    const target = `live_data:${liveBroker}:${liveSymbol}`;
    wsManager.send({ type: "subscribe", target });

    const unsub = wsManager.subscribe("live_tick", (data: unknown) => {
      const tick = data as LiveTick;
      if (tick.broker !== liveBroker || tick.symbol !== liveSymbol) return;

      const price = Number(tick.price);
      if (!Number.isFinite(price)) return;

      // Compute which 1-minute bucket this tick belongs to (seconds-since-epoch).
      const tickMs = new Date(tick.timestamp).getTime();
      if (!Number.isFinite(tickMs)) return;
      const tickMinute = Math.floor(tickMs / 60000) * 60; // seconds

      const series = seriesRef.current;
      if (!series) return;

      const current = liveCandleRef.current;

      // Skip late-arriving ticks for already-closed minutes. Coinbase and
      // other crypto feeds routinely deliver trades out of timestamp order;
      // calling series.update() with an older time collapses that bar's OHLC
      // to a single-price spike, which looks like bars appearing and
      // disappearing on every tick.
      if (current && tickMinute < current.time) {
        return;
      }

      if (current && current.time === tickMinute) {
        // Same minute — update existing candle.
        current.high = Math.max(current.high, price);
        current.low = Math.min(current.low, price);
        current.close = price;
      } else {
        // New minute — start a fresh candle.
        const newCandle = {
          time: tickMinute,
          open: price,
          high: price,
          low: price,
          close: price,
        };
        liveCandleRef.current = newCandle;
      }

      const candle = liveCandleRef.current!;
      if (chartType === "bars") {
        (series as ISeriesApi<"Candlestick">).update({
          time: candle.time as Time,
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
        });
      } else {
        (series as ISeriesApi<"Line">).update({
          time: candle.time as Time,
          value: candle.close,
        });
      }
    });

    return () => {
      wsManager.send({ type: "unsubscribe", target });
      unsub();
    };
  }, [liveBroker, liveSymbol, chartType]);

  return (
    <div className="relative w-full">
      {fetchingMore && (
        <div className="absolute top-1 left-2 z-10 flex items-center gap-1.5 px-2 py-0.5 rounded bg-gray-900/80 border border-gray-700 text-[10px] text-gray-400 pointer-events-none">
          <span className="inline-block w-2 h-2 border border-gray-400 border-t-transparent rounded-full animate-spin" />
          Loading older data…
        </div>
      )}
      <div
        ref={containerRef}
        className="w-full rounded-lg overflow-hidden border border-gray-800"
        style={{ height }}
      />
    </div>
  );
}
