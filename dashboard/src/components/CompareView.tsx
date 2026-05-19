import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  createChart,
  ColorType,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type CandlestickData,
  type Time,
  type UTCTimestamp,
  type LogicalRange,
  type Range,
  type MouseEventParams,
} from "lightweight-charts";
import { useMarketDataSource } from "../api/hooks";
import type { MarketDataBar } from "../types";

// ─── Types ────────────────────────────────────────────────────────────────────

export interface CompareDataset {
  /**
   * Source identifier (provider name like `polygon`, scraper source like
   * `theta`, or live source like `alpaca_live`). Doubles as the `source` query
   * param to `/api/data/market/{symbol}`.
   */
  provider: string;
  symbol: string;
  timeframe: string;
}

export type CompareMode = "overlay" | "stacked" | "diff";
export type ChartType = "candlestick" | "line";

interface Viewport {
  /** Logical (index-based) range — preserved across chart re-mounts. */
  logicalRange: LogicalRange | null;
}

interface ViewportCtxValue {
  vp: Viewport;
  setVisibleLogicalRange: (r: LogicalRange | null) => void;
}

const ViewportCtx = createContext<ViewportCtxValue>({
  vp: { logicalRange: null },
  setVisibleLogicalRange: () => {},
});

// Distinct line colors for up to ~8 datasets (used in line mode and legend).
const SERIES_COLORS = [
  "#6366f1", // indigo
  "#22c55e", // green
  "#f97316", // orange
  "#06b6d4", // cyan
  "#eab308", // yellow
  "#ec4899", // pink
  "#a855f7", // purple
  "#ef4444", // red
];

/**
 * Per-dataset up/down candle colors for candlestick mode.
 * Each dataset gets a visually distinct pair so overlapping candles are
 * distinguishable.
 */
const DATASET_COLORS = [
  { up: "#26a69a", down: "#ef5350" }, // green / red  (default)
  { up: "#2196f3", down: "#ff9800" }, // blue / orange
  { up: "#ab47bc", down: "#ffd54f" }, // purple / amber
  { up: "#00bcd4", down: "#e91e63" }, // cyan / pink
  { up: "#8bc34a", down: "#ff5722" }, // light-green / deep-orange
  { up: "#009688", down: "#f44336" }, // teal / red
  { up: "#3f51b5", down: "#ff8a65" }, // indigo / light-orange
  { up: "#4caf50", down: "#9c27b0" }, // green / purple
];

// ─── Helpers ──────────────────────────────────────────────────────────────────

/** Number of seconds covered by a bar of the given timeframe. */
function timeframeSeconds(tf: string): number {
  const m = /^(\d+)(min|hour|day)$/.exec(tf);
  if (!m) return 60;
  const n = parseInt(m[1], 10);
  switch (m[2]) {
    case "min":
      return n * 60;
    case "hour":
      return n * 3600;
    case "day":
      return n * 86400;
    default:
      return 60;
  }
}

interface NormalizedRow {
  time: UTCTimestamp;
  value: number; // close
  open: number;
  high: number;
  low: number;
}

/** Convert API bars → sorted, deduped OHLC rows. */
function barsToRows(bars: MarketDataBar[] | undefined): NormalizedRow[] {
  if (!bars) return [];
  const rows: NormalizedRow[] = [];
  for (const b of bars) {
    const t = Math.floor(new Date(b.timestamp).getTime() / 1000);
    if (!Number.isFinite(t) || !Number.isFinite(b.close)) continue;
    rows.push({
      time: t as UTCTimestamp,
      value: b.close,
      open: Number.isFinite(b.open) ? b.open : b.close,
      high: Number.isFinite(b.high) ? b.high : b.close,
      low: Number.isFinite(b.low) ? b.low : b.close,
    });
  }
  rows.sort((a, b) => (a.time as number) - (b.time as number));
  const seen = new Map<number, NormalizedRow>();
  for (const r of rows) seen.set(r.time as number, r);
  return Array.from(seen.values()).sort(
    (a, b) => (a.time as number) - (b.time as number)
  );
}

/** Pretty label for a dataset. */
function datasetLabel(d: CompareDataset): string {
  return `${d.provider}:${d.symbol}:${d.timeframe}`;
}

// ─── URL encoding helpers (also used from Data.tsx) ──────────────────────────

export function encodeCompareDataset(d: CompareDataset): string {
  return `${d.provider}:${d.symbol}:${d.timeframe}`;
}

export function decodeCompareDataset(s: string): CompareDataset | null {
  const parts = s.split(":");
  if (parts.length !== 3) return null;
  const [provider, symbol, timeframe] = parts;
  if (!provider || !symbol || !timeframe) return null;
  return { provider, symbol, timeframe };
}

// ─── Hook: load all series ────────────────────────────────────────────────────

interface LoadedSeries {
  dataset: CompareDataset;
  rows: NormalizedRow[];
  isLoading: boolean;
  error: unknown;
}

/**
 * Load up to 8 datasets in parallel. Calling useMarketDataSource in a fixed
 * loop violates the rules-of-hooks if the array length changes, so we use a
 * fixed cap and ignore slots beyond datasets.length.
 */
const MAX_DATASETS = 8;

function useLoadedSeries(datasets: CompareDataset[]): LoadedSeries[] {
  const padded: (CompareDataset | null)[] = [];
  for (let i = 0; i < MAX_DATASETS; i++) {
    padded.push(datasets[i] ?? null);
  }
  // Pre-allocate fixed hook calls (rules of hooks: must be unconditional).
  /* eslint-disable react-hooks/rules-of-hooks */
  const q0 = useMarketDataSource(
    padded[0]?.provider ?? null,
    padded[0]?.symbol ?? null,
    padded[0]?.timeframe ?? null
  );
  const q1 = useMarketDataSource(
    padded[1]?.provider ?? null,
    padded[1]?.symbol ?? null,
    padded[1]?.timeframe ?? null
  );
  const q2 = useMarketDataSource(
    padded[2]?.provider ?? null,
    padded[2]?.symbol ?? null,
    padded[2]?.timeframe ?? null
  );
  const q3 = useMarketDataSource(
    padded[3]?.provider ?? null,
    padded[3]?.symbol ?? null,
    padded[3]?.timeframe ?? null
  );
  const q4 = useMarketDataSource(
    padded[4]?.provider ?? null,
    padded[4]?.symbol ?? null,
    padded[4]?.timeframe ?? null
  );
  const q5 = useMarketDataSource(
    padded[5]?.provider ?? null,
    padded[5]?.symbol ?? null,
    padded[5]?.timeframe ?? null
  );
  const q6 = useMarketDataSource(
    padded[6]?.provider ?? null,
    padded[6]?.symbol ?? null,
    padded[6]?.timeframe ?? null
  );
  const q7 = useMarketDataSource(
    padded[7]?.provider ?? null,
    padded[7]?.symbol ?? null,
    padded[7]?.timeframe ?? null
  );
  /* eslint-enable react-hooks/rules-of-hooks */
  const queries = [q0, q1, q2, q3, q4, q5, q6, q7];

  return datasets.slice(0, MAX_DATASETS).map((d, i) => {
    const q = queries[i];
    return {
      dataset: d,
      rows: barsToRows(q.data?.data),
      isLoading: q.isLoading,
      error: q.error,
    };
  });
}

// ─── Shared chart wiring ──────────────────────────────────────────────────────

/**
 * Wire one IChartApi instance to the viewport context: on mount, restore the
 * shared logical range; on visible-range change, push back into the context.
 *
 * NOTE: This hook is used only by single-chart modes (overlay, diff). Stacked
 * mode uses direct cross-chart subscription via syncTimeScales() instead, so
 * that time axes stay in lock-step without going through React state.
 */
function useChartViewportSync(chart: IChartApi | null) {
  const { vp, setVisibleLogicalRange } = useContext(ViewportCtx);
  // True while we are programmatically applying a range to this chart so we
  // don't echo it back into the context and trigger another round-trip.
  const applyingRef = useRef(false);

  // Restore range when a NEW chart instance appears (not on every vp change —
  // doing so fights the user while they are actively panning).
  useEffect(() => {
    if (!chart) return;
    if (vp.logicalRange) {
      applyingRef.current = true;
      try {
        chart.timeScale().setVisibleLogicalRange(vp.logicalRange);
      } catch {
        // Ignore if data not yet present
      }
      applyingRef.current = false;
    }
    // Intentionally not including vp.logicalRange — we only restore on chart
    // mount (when the chart ref itself changes), not on every pan.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chart]);

  // Subscribe to range changes → push to context.
  useEffect(() => {
    if (!chart) return;
    const handler = () => {
      if (applyingRef.current) return;
      const r = chart.timeScale().getVisibleLogicalRange();
      if (r) setVisibleLogicalRange(r);
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(handler);
    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler);
    };
  }, [chart, setVisibleLogicalRange]);
}

/**
 * Wire an array of chart instances so that panning/zooming any one of them
 * immediately moves all the others to the same TIME range.  Returns a
 * cleanup function that unsubscribes everything.
 *
 * Uses `subscribeVisibleTimeRangeChange` (not logical-range) so that charts
 * with different bar densities — e.g. Alpaca (50 bars) vs Coinbase (500 bars)
 * over the same 12-hour window — show the same clock-time slice rather than
 * the same bar-index slice.
 *
 * Uses a shared `isSyncing` flag (not React state) to prevent infinite loops:
 * when chart A fires, we apply to B, C… but skip re-applying to A.
 */
function syncTimeScales(charts: IChartApi[]): () => void {
  if (charts.length < 2) return () => {};
  let isSyncing = false;
  const handlers: Array<{ chart: IChartApi; handler: (range: Range<Time> | null) => void }> = [];

  charts.forEach((src, i) => {
    const handler = (range: Range<Time> | null) => {
      if (isSyncing || !range) return;
      isSyncing = true;
      charts.forEach((dst, j) => {
        if (i === j) return;
        try {
          dst.timeScale().setVisibleRange(range);
        } catch {
          // chart may have no data in this time range yet
        }
      });
      isSyncing = false;
    };
    src.timeScale().subscribeVisibleTimeRangeChange(handler);
    handlers.push({ chart: src, handler });
  });

  return () => {
    for (const { chart, handler } of handlers) {
      try {
        chart.timeScale().unsubscribeVisibleTimeRangeChange(handler);
      } catch {
        // already removed
      }
    }
  };
}

/**
 * Wire crosshair movement between stacked chart instances so hovering over one
 * chart shows a synchronized vertical (time) crosshair on all other charts.
 *
 * Each `seriesRefs[j]` is the primary series for `charts[j]` — required by the
 * lightweight-charts `setCrosshairPosition` API to identify which chart panel
 * the crosshair belongs to.  We pass `NaN` as the price value so only the
 * vertical time line appears on the target charts (their Y axes are independent
 * anyway).
 *
 * Returns a cleanup function that unsubscribes all handlers.
 */
function syncCrosshairs(
  charts: IChartApi[],
  seriesRefs: (AnySeries | null)[],
): () => void {
  if (charts.length < 2) return () => {};
  let isSyncing = false;
  const cleanups: (() => void)[] = [];

  charts.forEach((src, i) => {
    const handler = (param: MouseEventParams) => {
      if (isSyncing) return;
      isSyncing = true;
      charts.forEach((dst, j) => {
        if (i === j) return;
        const series = seriesRefs[j];
        if (!series) return;
        if (param.time !== undefined) {
          try {
            dst.setCrosshairPosition(NaN, param.time, series);
          } catch {
            // Chart may not have data at this time — safe to ignore
          }
        } else {
          try {
            dst.clearCrosshairPosition();
          } catch {
            // ignore
          }
        }
      });
      isSyncing = false;
    };
    src.subscribeCrosshairMove(handler);
    cleanups.push(() => {
      try {
        src.unsubscribeCrosshairMove(handler);
      } catch {
        // already removed
      }
    });
  });

  return () => cleanups.forEach((fn) => fn());
}

/** Base chart options shared by all sub-charts. */
const CHART_OPTIONS = {
  autoSize: true,
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
    timeVisible: true,
    // Do NOT lock either edge so the user can pan freely.
    fixLeftEdge: false,
    fixRightEdge: false,
  },
  // Explicitly enable pan and zoom so they work regardless of platform defaults.
  handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
  handleScale: { mouseWheel: true, pinchToZoom: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
} as const;

// ─── Helpers to add a series of the right type ────────────────────────────────

type AnySeries = ISeriesApi<"Line"> | ISeriesApi<"Candlestick">;

function addSeries(
  chart: IChartApi,
  chartType: ChartType,
  colorIdx: number,
  title: string
): AnySeries {
  const dc = DATASET_COLORS[colorIdx % DATASET_COLORS.length];
  if (chartType === "candlestick") {
    return chart.addCandlestickSeries({
      upColor: dc.up,
      downColor: dc.down,
      wickUpColor: dc.up,
      wickDownColor: dc.down,
      borderVisible: false,
      title,
    });
  }
  return chart.addLineSeries({
    color: SERIES_COLORS[colorIdx % SERIES_COLORS.length],
    lineWidth: 2,
    priceLineVisible: false,
    title,
  });
}

function setSeriesData(series: AnySeries, rows: NormalizedRow[], chartType: ChartType) {
  if (chartType === "candlestick") {
    (series as ISeriesApi<"Candlestick">).setData(
      rows.map(
        (r): CandlestickData<Time> => ({
          time: r.time as Time,
          open: r.open,
          high: r.high,
          low: r.low,
          close: r.value,
        })
      )
    );
  } else {
    (series as ISeriesApi<"Line">).setData(
      rows.map(
        (r): LineData<Time> => ({ time: r.time as Time, value: r.value })
      )
    );
  }
}

// ─── OverlayChart ─────────────────────────────────────────────────────────────

interface SubChartProps {
  loaded: LoadedSeries[];
  chartType: ChartType;
}

function OverlayChart({ loaded, chartType }: SubChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [chart, setChart] = useState<IChartApi | null>(null);
  const seriesRefs = useRef<AnySeries[]>([]);
  // Track whether this is the first data load so we fitContent once.
  const didFitRef = useRef(false);

  useEffect(() => {
    if (!containerRef.current) return;
    const c = createChart(containerRef.current, {
      ...CHART_OPTIONS,
    });
    setChart(c);
    didFitRef.current = false;
    return () => {
      c.remove();
      setChart(null);
      seriesRefs.current = [];
      didFitRef.current = false;
    };
  }, []);

  useChartViewportSync(chart);

  // (Re)build series whenever `loaded` or `chartType` changes.
  useEffect(() => {
    if (!chart) return;
    // Wipe old series
    for (const s of seriesRefs.current) {
      try {
        chart.removeSeries(s);
      } catch {
        // ignore
      }
    }
    seriesRefs.current = [];

    const hasData = loaded.some((l) => l.rows.length > 0);

    loaded.forEach((l, i) => {
      const s = addSeries(chart, chartType, i, datasetLabel(l.dataset));
      setSeriesData(s, l.rows, chartType);
      seriesRefs.current.push(s);
    });

    // Only call fitContent once on the initial data load, not on every toggle.
    if (hasData && !didFitRef.current) {
      chart.timeScale().fitContent();
      didFitRef.current = true;
    }
  }, [chart, loaded, chartType]);

  return (
    <div className="flex flex-col flex-1 min-h-0 space-y-2">
      <Legend loaded={loaded} chartType={chartType} />
      <div
        ref={containerRef}
        className="flex-1 min-h-0 w-full rounded-lg border border-gray-800"
      />
    </div>
  );
}

// ─── StackedCharts ────────────────────────────────────────────────────────────

function StackedCharts({ loaded, chartType }: SubChartProps) {
  // Collect chart instances and their primary series from each row so we can
  // wire cross-chart time sync and crosshair sync.
  const chartsRef = useRef<(IChartApi | null)[]>([]);
  const seriesRef = useRef<(AnySeries | null)[]>([]);

  // Called by each StackedRow when its chart/series is created or destroyed.
  const onChartReady = useCallback(
    (index: number, chart: IChartApi | null, series: AnySeries | null) => {
      chartsRef.current[index] = chart;
      seriesRef.current[index] = series;
    },
    []
  );

  // Re-run sync wiring whenever the set of live charts changes.
  // We track changes via a version counter that rows increment on mount/unmount.
  const [syncVersion, setSyncVersion] = useState(0);
  const bumpSync = useCallback(() => setSyncVersion((v) => v + 1), []);

  useEffect(() => {
    const live = chartsRef.current.filter((c): c is IChartApi => c != null);
    if (live.length < 2) return;

    // Helper: align all live charts to the union of their data extents.
    const applyUnionRange = () => {
      const allTimes = loaded.flatMap((l) => l.rows.map((r) => r.time as number));
      if (allTimes.length > 0) {
        const minTime = Math.min(...allTimes) as UTCTimestamp;
        const maxTime = Math.max(...allTimes) as UTCTimestamp;
        for (const c of live) {
          try {
            c.timeScale().setVisibleRange({ from: minTime, to: maxTime });
          } catch {
            // ignore if chart has no data yet
          }
        }
      }
    };

    // Align immediately (catches cases where data is already present).
    applyUnionRange();

    // Re-align after a short delay to catch any late data loads — the series
    // data may arrive slightly after the chart instance is created, causing
    // the initial alignment to see empty rows for some charts.
    const timerId = setTimeout(applyUnionRange, 100);

    const liveSeries = seriesRef.current.filter((_s, idx) =>
      chartsRef.current[idx] != null
    );

    const cleanupTimeScales = syncTimeScales(live);
    const cleanupCrosshairs = syncCrosshairs(live, liveSeries);
    return () => {
      clearTimeout(timerId);
      cleanupTimeScales();
      cleanupCrosshairs();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [syncVersion, loaded]);

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-3">
      {loaded.map((l, i) => (
        <StackedRow
          key={datasetLabel(l.dataset)}
          loaded={l}
          colorIdx={i}
          chartType={chartType}
          onChartReady={(c, s) => {
            onChartReady(i, c, s);
            bumpSync();
          }}
        />
      ))}
    </div>
  );
}

function StackedRow({
  loaded,
  colorIdx,
  chartType,
  onChartReady,
}: {
  loaded: LoadedSeries;
  colorIdx: number;
  chartType: ChartType;
  onChartReady: (chart: IChartApi | null, series: AnySeries | null) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [chart, setChart] = useState<IChartApi | null>(null);
  const seriesRef = useRef<AnySeries | null>(null);
  const didFitRef = useRef(false);
  // Stable ref so the cleanup in useEffect can call the latest onChartReady.
  const onChartReadyRef = useRef(onChartReady);
  useEffect(() => { onChartReadyRef.current = onChartReady; });

  useEffect(() => {
    if (!containerRef.current) return;
    const c = createChart(containerRef.current, {
      ...CHART_OPTIONS,
    });
    setChart(c);
    didFitRef.current = false;
    onChartReadyRef.current(c, null);
    return () => {
      c.remove();
      setChart(null);
      seriesRef.current = null;
      didFitRef.current = false;
      onChartReadyRef.current(null, null);
    };
  }, []);

  // No viewport context sync here — StackedCharts handles cross-chart sync
  // directly via syncTimeScales() to avoid React render-cycle latency.

  useEffect(() => {
    if (!chart) return;
    if (seriesRef.current) {
      try {
        chart.removeSeries(seriesRef.current);
      } catch {
        // ignore
      }
      seriesRef.current = null;
    }
    const s = addSeries(chart, chartType, colorIdx, datasetLabel(loaded.dataset));
    setSeriesData(s, loaded.rows, chartType);
    seriesRef.current = s;
    // Notify parent so the crosshair sync has an up-to-date series ref.
    onChartReadyRef.current(chart, s);

    if (loaded.rows.length > 0 && !didFitRef.current) {
      chart.timeScale().fitContent();
      didFitRef.current = true;
    }
  }, [chart, loaded, colorIdx, chartType]);

  const dc = DATASET_COLORS[colorIdx % DATASET_COLORS.length];
  const lineColor = SERIES_COLORS[colorIdx % SERIES_COLORS.length];

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="flex items-center gap-2 mb-1 text-xs font-mono text-gray-300 shrink-0">
        {chartType === "candlestick" ? (
          <CandleDot upColor={dc.up} downColor={dc.down} />
        ) : (
          <span
            className="inline-block w-3 h-0.5 rounded"
            style={{ backgroundColor: lineColor }}
          />
        )}
        {datasetLabel(loaded.dataset)}
      </div>
      <div
        ref={containerRef}
        className="flex-1 min-h-0 w-full rounded-lg border border-gray-800"
      />
    </div>
  );
}

// ─── DiffChart ────────────────────────────────────────────────────────────────

function DiffChart({ loaded }: SubChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [chart, setChart] = useState<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const didFitRef = useRef(false);

  // Compute diff. Bin both series' bars by interval-rounded timestamp; for each
  // matched bucket, value_b - value_a. Unmatched bars are dropped (line series
  // gaps appear automatically when consecutive timestamps are missing).
  const diffRows = useMemo((): NormalizedRow[] => {
    if (loaded.length !== 2) return [];
    const [a, b] = loaded;
    const intervalA = timeframeSeconds(a.dataset.timeframe);
    const intervalB = timeframeSeconds(b.dataset.timeframe);
    const interval = Math.max(intervalA, intervalB);

    const bucketsA = new Map<number, number>();
    for (const r of a.rows) {
      const k = Math.floor((r.time as number) / interval) * interval;
      bucketsA.set(k, r.value);
    }
    const out: NormalizedRow[] = [];
    for (const r of b.rows) {
      const k = Math.floor((r.time as number) / interval) * interval;
      const va = bucketsA.get(k);
      if (va === undefined) continue;
      const diff = r.value - va;
      out.push({ time: k as UTCTimestamp, value: diff, open: diff, high: diff, low: diff });
    }
    // Dedupe & sort
    const seen = new Map<number, NormalizedRow>();
    for (const r of out) seen.set(r.time as number, r);
    return Array.from(seen.values()).sort(
      (x, y) => (x.time as number) - (y.time as number)
    );
  }, [loaded]);

  useEffect(() => {
    if (!containerRef.current) return;
    const c = createChart(containerRef.current, {
      ...CHART_OPTIONS,
    });
    setChart(c);
    didFitRef.current = false;
    return () => {
      c.remove();
      setChart(null);
      seriesRef.current = null;
      didFitRef.current = false;
    };
  }, []);

  useChartViewportSync(chart);

  useEffect(() => {
    if (!chart) return;
    if (seriesRef.current) {
      try {
        chart.removeSeries(seriesRef.current);
      } catch {
        // ignore
      }
      seriesRef.current = null;
    }
    const s = chart.addLineSeries({
      color: "#f97316",
      lineWidth: 2,
      priceLineVisible: false,
      title: "diff",
    });
    s.setData(
      diffRows.map(
        (r): LineData<Time> => ({ time: r.time as Time, value: r.value })
      )
    );
    // Zero reference line
    s.createPriceLine({
      price: 0,
      color: "#6b7280",
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: "0",
    });
    seriesRef.current = s;

    if (diffRows.length > 0 && !didFitRef.current) {
      chart.timeScale().fitContent();
      didFitRef.current = true;
    }
  }, [chart, diffRows]);

  const [a, b] = loaded;
  return (
    <div className="flex flex-col flex-1 min-h-0 space-y-2">
      <div className="text-xs text-gray-400 shrink-0">
        Diff: <span className="font-mono text-gray-200">{datasetLabel(b.dataset)}</span>
        <span className="mx-1">−</span>
        <span className="font-mono text-gray-200">{datasetLabel(a.dataset)}</span>
        {diffRows.length === 0 && (
          <span className="ml-2 text-amber-400">
            (no matched bars — check timeframes / time ranges)
          </span>
        )}
      </div>
      <div
        ref={containerRef}
        className="flex-1 min-h-0 w-full rounded-lg border border-gray-800"
      />
    </div>
  );
}

// ─── Shared legend ────────────────────────────────────────────────────────────

/** Small two-tone candle icon for candlestick legend entries. */
function CandleDot({ upColor, downColor }: { upColor: string; downColor: string }) {
  return (
    <span className="inline-flex items-center gap-0.5" aria-hidden>
      <span
        className="inline-block w-1.5 h-3 rounded-sm"
        style={{ backgroundColor: upColor }}
      />
      <span
        className="inline-block w-1.5 h-3 rounded-sm"
        style={{ backgroundColor: downColor }}
      />
    </span>
  );
}

function Legend({ loaded, chartType }: { loaded: LoadedSeries[]; chartType: ChartType }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
      {loaded.map((l, i) => {
        const dc = DATASET_COLORS[i % DATASET_COLORS.length];
        const lineColor = SERIES_COLORS[i % SERIES_COLORS.length];
        return (
          <div
            key={datasetLabel(l.dataset)}
            className="flex items-center gap-2 font-mono text-gray-300"
          >
            {chartType === "candlestick" ? (
              <CandleDot upColor={dc.up} downColor={dc.down} />
            ) : (
              <span
                className="inline-block w-3 h-0.5 rounded"
                style={{ backgroundColor: lineColor }}
              />
            )}
            <span>{datasetLabel(l.dataset)}</span>
            {l.isLoading && <span className="text-gray-500">(loading…)</span>}
            {l.error ? (
              <span
                className="text-red-400"
                title={String((l.error as Error).message ?? l.error)}
              >
                (error)
              </span>
            ) : null}
            {!l.isLoading && !l.error && (
              <span className="text-gray-500">{l.rows.length} bars</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─── Public component ────────────────────────────────────────────────────────

export interface CompareViewProps {
  datasets: CompareDataset[];
  mode?: CompareMode;
  onModeChange?: (m: CompareMode) => void;
}

export function CompareView({ datasets, mode: modeProp, onModeChange }: CompareViewProps) {
  const isControlled = modeProp !== undefined;
  const [internalMode, setInternalMode] = useState<CompareMode>("overlay");
  const mode = isControlled ? modeProp! : internalMode;
  const setMode = useCallback(
    (m: CompareMode) => {
      if (!isControlled) setInternalMode(m);
      onModeChange?.(m);
    },
    [isControlled, onModeChange]
  );

  const [chartType, setChartType] = useState<ChartType>("candlestick");

  // Auto-revert from diff if dataset count changes away from 2.
  useEffect(() => {
    if (mode === "diff" && datasets.length !== 2) {
      setMode("overlay");
    }
  }, [mode, datasets.length, setMode]);

  const [vp, setVp] = useState<Viewport>({ logicalRange: null });
  const setVisibleLogicalRange = useCallback((r: LogicalRange | null) => {
    setVp({ logicalRange: r });
  }, []);

  const loaded = useLoadedSeries(datasets);

  const ctxValue = useMemo(
    () => ({ vp, setVisibleLogicalRange }),
    [vp, setVisibleLogicalRange]
  );

  if (datasets.length === 0) {
    return (
      <p className="text-gray-500 text-sm">
        Select at least one dataset to compare.
      </p>
    );
  }

  return (
    <ViewportCtx.Provider value={ctxValue}>
      <div className="flex flex-col h-full gap-3">
        <ModeBar
          mode={mode}
          setMode={setMode}
          diffAvailable={datasets.length === 2}
          chartType={chartType}
          setChartType={setChartType}
        />
        {mode === "overlay" && <OverlayChart loaded={loaded} chartType={chartType} />}
        {mode === "stacked" && <StackedCharts loaded={loaded} chartType={chartType} />}
        {mode === "diff" && datasets.length === 2 && (
          <DiffChart loaded={loaded} chartType={chartType} />
        )}
      </div>
    </ViewportCtx.Provider>
  );
}

function ModeBar({
  mode,
  setMode,
  diffAvailable,
  chartType,
  setChartType,
}: {
  mode: CompareMode;
  setMode: (m: CompareMode) => void;
  diffAvailable: boolean;
  chartType: ChartType;
  setChartType: (t: ChartType) => void;
}): ReactNode {
  const modes: CompareMode[] = ["overlay", "stacked", "diff"];
  return (
    <div className="flex flex-wrap gap-2">
      {modes.map((m) => {
        const disabled = m === "diff" && !diffAvailable;
        return (
          <button
            key={m}
            disabled={disabled}
            onClick={() => setMode(m)}
            title={
              disabled
                ? "Diff requires exactly 2 datasets"
                : `View as ${m}`
            }
            className={`px-3 py-1.5 rounded text-sm transition-colors ${
              mode === m
                ? "bg-indigo-600 text-white"
                : "bg-gray-800 text-gray-300 hover:bg-gray-700"
            } disabled:opacity-40 disabled:cursor-not-allowed`}
          >
            {m}
          </button>
        );
      })}

      {/* Separator */}
      <span className="w-px bg-gray-700 self-stretch mx-1" />

      {/* Chart type toggle */}
      <button
        onClick={() => setChartType("candlestick")}
        title="Candlestick bars"
        className={`px-3 py-1.5 rounded text-sm transition-colors ${
          chartType === "candlestick"
            ? "bg-indigo-600 text-white"
            : "bg-gray-800 text-gray-300 hover:bg-gray-700"
        }`}
      >
        bars
      </button>
      <button
        onClick={() => setChartType("line")}
        title="Line chart"
        className={`px-3 py-1.5 rounded text-sm transition-colors ${
          chartType === "line"
            ? "bg-indigo-600 text-white"
            : "bg-gray-800 text-gray-300 hover:bg-gray-700"
        }`}
      >
        line
      </button>
    </div>
  );
}
