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
import { usePagedMarketData, type ParsedBar } from "../hooks/usePagedMarketData";

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

export type CompareMode = "overlay" | "stacked";
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

/** Convert ParsedBar array → NormalizedRow array (already sorted + deduped by the hook). */
function parsedBarsToRows(bars: ParsedBar[]): NormalizedRow[] {
  return bars.map((b) => ({
    time: b.time as UTCTimestamp,
    value: b.close,
    open: b.open,
    high: b.high,
    low: b.low,
  }));
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
  fetchingMore: boolean;
  loadEarlier: (beforeTimestamp: string) => void;
  error: unknown;
}

/**
 * Load up to 8 datasets in parallel using the paged market-data hook.
 * Calling hooks in a fixed loop violates rules-of-hooks if the array length
 * changes, so we use a fixed cap (MAX_DATASETS) and null-pad shorter arrays.
 */
const MAX_DATASETS = 8;

function useLoadedSeries(datasets: CompareDataset[]): LoadedSeries[] {
  const padded: (CompareDataset | null)[] = [];
  for (let i = 0; i < MAX_DATASETS; i++) {
    padded.push(datasets[i] ?? null);
  }
  // Pre-allocate fixed hook calls (rules of hooks: must be unconditional).
  /* eslint-disable react-hooks/rules-of-hooks */
  const q0 = usePagedMarketData(padded[0]?.provider ?? null, padded[0]?.symbol ?? null, padded[0]?.timeframe ?? null);
  const q1 = usePagedMarketData(padded[1]?.provider ?? null, padded[1]?.symbol ?? null, padded[1]?.timeframe ?? null);
  const q2 = usePagedMarketData(padded[2]?.provider ?? null, padded[2]?.symbol ?? null, padded[2]?.timeframe ?? null);
  const q3 = usePagedMarketData(padded[3]?.provider ?? null, padded[3]?.symbol ?? null, padded[3]?.timeframe ?? null);
  const q4 = usePagedMarketData(padded[4]?.provider ?? null, padded[4]?.symbol ?? null, padded[4]?.timeframe ?? null);
  const q5 = usePagedMarketData(padded[5]?.provider ?? null, padded[5]?.symbol ?? null, padded[5]?.timeframe ?? null);
  const q6 = usePagedMarketData(padded[6]?.provider ?? null, padded[6]?.symbol ?? null, padded[6]?.timeframe ?? null);
  const q7 = usePagedMarketData(padded[7]?.provider ?? null, padded[7]?.symbol ?? null, padded[7]?.timeframe ?? null);
  /* eslint-enable react-hooks/rules-of-hooks */
  const queries = [q0, q1, q2, q3, q4, q5, q6, q7];

  return datasets.slice(0, MAX_DATASETS).map((d, i) => {
    const q = queries[i];
    return {
      dataset: d,
      rows: parsedBarsToRows(q.bars),
      isLoading: q.loading,
      fetchingMore: q.fetchingMore,
      loadEarlier: q.loadEarlier,
      error: null,
    };
  });
}

// ─── Shared chart wiring ──────────────────────────────────────────────────────

/**
 * Wire one IChartApi instance to the viewport context: on mount, restore the
 * shared logical range; on visible-range change, push back into the context.
 *
 * NOTE: This hook is used only by overlay mode. Stacked + diff sync modes use
 * direct cross-chart subscription via syncTimeScales() instead, so that time
 * axes stay in lock-step without going through React state.
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

interface OverlayChartProps extends SubChartProps {
  onChartReady?: (chart: IChartApi | null) => void;
}

function OverlayChart({ loaded, chartType, onChartReady }: OverlayChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [chart, setChart] = useState<IChartApi | null>(null);
  const seriesRefs = useRef<AnySeries[]>([]);
  // Track whether this is the first data load so we fitContent once.
  const didFitRef = useRef(false);
  const onChartReadyRef = useRef(onChartReady);
  useEffect(() => { onChartReadyRef.current = onChartReady; });

  useEffect(() => {
    if (!containerRef.current) return;
    const c = createChart(containerRef.current, {
      ...CHART_OPTIONS,
    });
    setChart(c);
    didFitRef.current = false;
    onChartReadyRef.current?.(c);
    return () => {
      c.remove();
      setChart(null);
      seriesRefs.current = [];
      didFitRef.current = false;
      onChartReadyRef.current?.(null);
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

    // Preserve viewport when re-rendering due to a paging merge.
    const currentRange = didFitRef.current
      ? chart.timeScale().getVisibleRange() ?? null
      : null;

    loaded.forEach((l, i) => {
      const s = addSeries(chart, chartType, i, datasetLabel(l.dataset));
      setSeriesData(s, l.rows, chartType);
      seriesRefs.current.push(s);
    });

    if (hasData) {
      if (currentRange) {
        try {
          chart.timeScale().setVisibleRange(currentRange);
        } catch {
          // fall through
        }
      } else if (!didFitRef.current) {
        // Only call fitContent once on the initial data load.
        chart.timeScale().fitContent();
        didFitRef.current = true;
      }
    }
  }, [chart, loaded, chartType]);

  // Edge-detection for all datasets: when the viewport approaches the left
  // edge of any dataset's loaded bars, trigger that dataset's loadEarlier.
  useEffect(() => {
    if (!chart || loaded.length === 0) return;

    const handler = (range: Range<Time> | null) => {
      if (!range) return;
      const from = range.from as number;
      for (const l of loaded) {
        if (l.rows.length === 0) continue;
        const firstBarTime = l.rows[0].time as number;
        const lastBarTime = l.rows[l.rows.length - 1].time as number;
        const span = lastBarTime - firstBarTime;
        const threshold = firstBarTime + span * 0.1;
        if (from <= threshold) {
          l.loadEarlier(new Date(firstBarTime * 1000).toISOString());
        }
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
  }, [chart, loaded]);

  const anyFetchingMore = loaded.some((l) => l.fetchingMore);

  return (
    <div className="flex flex-col h-full space-y-1">
      <Legend loaded={loaded} chartType={chartType} />
      <div className="relative flex-1">
        {anyFetchingMore && (
          <div className="absolute top-1 left-2 z-10 flex items-center gap-1.5 px-2 py-0.5 rounded bg-gray-900/80 border border-gray-700 text-[10px] text-gray-400 pointer-events-none">
            <span className="inline-block w-2 h-2 border border-gray-400 border-t-transparent rounded-full animate-spin" />
            Loading older data…
          </div>
        )}
        <div
          ref={containerRef}
          className="absolute inset-0 rounded-lg border border-gray-800"
        />
      </div>
    </div>
  );
}

// ─── StackedCharts ────────────────────────────────────────────────────────────

interface StackedChartsProps extends SubChartProps {
  onChartsReady?: (charts: (IChartApi | null)[], series: (AnySeries | null)[]) => void;
}

function StackedCharts({ loaded, chartType, onChartsReady }: StackedChartsProps) {
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

  // Notify parent (for cross-component sync with diff chart).
  const onChartsReadyRef = useRef(onChartsReady);
  useEffect(() => { onChartsReadyRef.current = onChartsReady; });

  useEffect(() => {
    const live = chartsRef.current.filter((c): c is IChartApi => c != null);
    if (live.length < 2) return;

    // Helper: align all live charts to the union of their data extents.
    const applyUnionRange = () => {
      const allTimes = loaded.flatMap((l) => l.rows.map((r) => r.time as number));
      if (allTimes.length > 0) {
        // Use a loop instead of Math.min/max spread to avoid stack overflow on
        // large arrays (100k+ elements exceed the JS call stack limit).
        let minTime = Infinity, maxTime = -Infinity;
        for (const t of allTimes) {
          if (t < minTime) minTime = t;
          if (t > maxTime) maxTime = t;
        }
        for (const c of live) {
          try {
            c.timeScale().setVisibleRange({
              from: minTime as UTCTimestamp,
              to: maxTime as UTCTimestamp,
            });
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

    // Propagate current chart refs to parent.
    onChartsReadyRef.current?.([...chartsRef.current], [...seriesRef.current]);

    return () => {
      clearTimeout(timerId);
      cleanupTimeScales();
      cleanupCrosshairs();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [syncVersion, loaded]);

  return (
    <div className="flex flex-col h-full gap-1">
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

    // Preserve the viewport when rows are updated (paging merge); only
    // fitContent on the very first load.
    const currentRange = didFitRef.current
      ? chart.timeScale().getVisibleRange() ?? null
      : null;

    setSeriesData(s, loaded.rows, chartType);
    seriesRef.current = s;
    // Notify parent so the crosshair sync has an up-to-date series ref.
    onChartReadyRef.current(chart, s);

    if (loaded.rows.length > 0) {
      if (currentRange) {
        try {
          chart.timeScale().setVisibleRange(currentRange);
        } catch {
          // fall through — new data may not cover saved range yet
        }
      } else if (!didFitRef.current) {
        chart.timeScale().fitContent();
        didFitRef.current = true;
      }
    }
  }, [chart, loaded, colorIdx, chartType]);

  // Edge-detection: fire loadEarlier when the user pans near the left edge.
  useEffect(() => {
    if (!chart || loaded.rows.length === 0) return;
    const { loadEarlier } = loaded;
    const firstRow = loaded.rows[0];
    const lastRow = loaded.rows[loaded.rows.length - 1];
    const firstBarTime = firstRow.time as number;
    const lastBarTime = lastRow.time as number;
    const loadedSpan = lastBarTime - firstBarTime;

    const handler = (range: Range<Time> | null) => {
      if (!range) return;
      const from = range.from as number;
      const threshold = firstBarTime + loadedSpan * 0.1;
      if (from <= threshold) {
        // Convert unix seconds back to ISO for the hook.
        loadEarlier(new Date(firstBarTime * 1000).toISOString());
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
  }, [chart, loaded]);

  const dc = DATASET_COLORS[colorIdx % DATASET_COLORS.length];
  const lineColor = SERIES_COLORS[colorIdx % SERIES_COLORS.length];

  return (
    <div className="flex flex-col flex-1" style={{ minHeight: 0 }}>
      <div className="relative flex items-center gap-2 mb-1 text-xs font-mono text-gray-300 shrink-0">
        {chartType === "candlestick" ? (
          <CandleDot upColor={dc.up} downColor={dc.down} />
        ) : (
          <span
            className="inline-block w-3 h-0.5 rounded"
            style={{ backgroundColor: lineColor }}
          />
        )}
        {datasetLabel(loaded.dataset)}
        {loaded.fetchingMore && (
          <span className="ml-2 inline-flex items-center gap-1 text-gray-500">
            <span className="inline-block w-2 h-2 border border-gray-500 border-t-transparent rounded-full animate-spin" />
            loading older data…
          </span>
        )}
      </div>
      <div className="relative flex-1" style={{ minHeight: "150px" }}>
        <div
          ref={containerRef}
          className="absolute inset-0 rounded-lg border border-gray-800"
        />
      </div>
    </div>
  );
}

// ─── DiffChart ────────────────────────────────────────────────────────────────

interface DiffChartProps extends SubChartProps {
  onChartReady?: (chart: IChartApi | null, series: AnySeries | null) => void;
}

function DiffChart({ loaded, onChartReady }: DiffChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [chart, setChart] = useState<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const didFitRef = useRef(false);
  const onChartReadyRef = useRef(onChartReady);
  useEffect(() => { onChartReadyRef.current = onChartReady; });

  // Compute diff. Bin both series' bars by interval-rounded timestamp; for each
  // matched bucket, value_b - value_a. Unmatched bars are dropped (line series
  // gaps appear automatically when consecutive timestamps are missing).
  // If more than 2 datasets are selected, uses dataset[0] and dataset[1].
  const diffRows = useMemo((): NormalizedRow[] => {
    if (loaded.length < 2) return [];
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
    onChartReadyRef.current?.(c, null);
    return () => {
      c.remove();
      setChart(null);
      seriesRef.current = null;
      didFitRef.current = false;
      onChartReadyRef.current?.(null, null);
    };
  }, []);

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
    onChartReadyRef.current?.(chart, s);

    if (diffRows.length > 0 && !didFitRef.current) {
      chart.timeScale().fitContent();
      didFitRef.current = true;
    }
  }, [chart, diffRows]);

  // Edge-detection: fire loadEarlier for all datasets when the user pans near
  // the left edge of the diff chart.
  useEffect(() => {
    if (!chart || loaded.length < 2) return;

    const handler = (range: Range<Time> | null) => {
      if (!range) return;
      const from = range.from as number;
      for (const l of loaded) {
        if (l.rows.length === 0) continue;
        const firstBarTime = l.rows[0].time as number;
        const lastBarTime = l.rows[l.rows.length - 1].time as number;
        const span = lastBarTime - firstBarTime;
        const threshold = firstBarTime + span * 0.1;
        if (from <= threshold) {
          l.loadEarlier(new Date(firstBarTime * 1000).toISOString());
        }
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
  }, [chart, loaded]);

  const [a, b] = loaded;
  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="text-xs text-gray-400 shrink-0 px-1 pt-2 pb-1">
        Diff: <span className="font-mono text-gray-200">{b ? datasetLabel(b.dataset) : "?"}</span>
        <span className="mx-1">−</span>
        <span className="font-mono text-gray-200">{a ? datasetLabel(a.dataset) : "?"}</span>
        {diffRows.length === 0 && (
          <span className="ml-2 text-amber-400">
            (no matched bars — check timeframes / time ranges)
          </span>
        )}
      </div>
      <div className="relative flex-1" style={{ minHeight: "120px" }}>
        <div
          ref={containerRef}
          className="absolute inset-0 rounded-lg border border-gray-800"
        />
      </div>
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
            {l.fetchingMore && (
              <span className="inline-flex items-center gap-1 text-gray-500">
                <span className="inline-block w-2 h-2 border border-gray-500 border-t-transparent rounded-full animate-spin" />
                +more
              </span>
            )}
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

  const [showDiff, setShowDiff] = useState(false);
  const [chartType, setChartType] = useState<ChartType>("candlestick");

  const [vp, setVp] = useState<Viewport>({ logicalRange: null });
  const setVisibleLogicalRange = useCallback((r: LogicalRange | null) => {
    setVp({ logicalRange: r });
  }, []);

  const loaded = useLoadedSeries(datasets);

  const ctxValue = useMemo(
    () => ({ vp, setVisibleLogicalRange }),
    [vp, setVisibleLogicalRange]
  );

  // ── Cross-component time sync for diff panel ──────────────────────────────
  // We collect one "main" chart ref (from overlay or stacked) and the diff
  // chart ref, then wire them together with syncTimeScales + syncCrosshairs.

  // For overlay mode: the single overlay chart instance.
  const overlayChartRef = useRef<IChartApi | null>(null);

  // For stacked mode: all stacked chart instances + their series.
  const stackedChartsRef = useRef<(IChartApi | null)[]>([]);
  const stackedSeriesRef = useRef<(AnySeries | null)[]>([]);

  // Diff chart instance + series.
  const diffChartRef = useRef<IChartApi | null>(null);
  const diffSeriesRef = useRef<AnySeries | null>(null);

  // Version counter bumped whenever any chart ref changes so the sync effect
  // re-runs and re-wires all charts including the new diff chart.
  const [diffSyncVersion, setDiffSyncVersion] = useState(0);
  const bumpDiffSync = useCallback(() => setDiffSyncVersion((v) => v + 1), []);

  // Wire diff chart to main charts whenever any chart ref changes or
  // showDiff toggles on/off.
  useEffect(() => {
    if (!showDiff || !diffChartRef.current) return;

    const mainCharts: IChartApi[] = [];
    const mainSeries: (AnySeries | null)[] = [];

    if (mode === "overlay" && overlayChartRef.current) {
      mainCharts.push(overlayChartRef.current);
      mainSeries.push(null); // overlay has no single primary series for crosshair
    } else if (mode === "stacked") {
      for (let i = 0; i < stackedChartsRef.current.length; i++) {
        const c = stackedChartsRef.current[i];
        if (c) {
          mainCharts.push(c);
          mainSeries.push(stackedSeriesRef.current[i] ?? null);
        }
      }
    }

    const allCharts = [...mainCharts, diffChartRef.current];
    const allSeries: (AnySeries | null)[] = [...mainSeries, diffSeriesRef.current];

    const cleanupTimeScales = syncTimeScales(allCharts);
    const cleanupCrosshairs = syncCrosshairs(allCharts, allSeries);

    return () => {
      cleanupTimeScales();
      cleanupCrosshairs();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showDiff, mode, diffSyncVersion]);

  if (datasets.length === 0) {
    return (
      <p className="text-gray-500 text-sm">
        Select at least one dataset to compare.
      </p>
    );
  }

  const diffAvailable = datasets.length >= 2;

  return (
    <ViewportCtx.Provider value={ctxValue}>
      <div className="flex flex-col h-full gap-3">
        <ModeBar
          mode={mode}
          setMode={setMode}
          showDiff={showDiff}
          setShowDiff={(d) => {
            setShowDiff(d);
            bumpDiffSync();
          }}
          diffAvailable={diffAvailable}
          chartType={chartType}
          setChartType={setChartType}
        />

        {/* Chart area: flex column, main view + optional diff panel below.
            Use calc-based explicit heights to avoid flex-chain collapse. */}
        <div className="flex flex-col" style={{ height: "calc(100vh - 180px)" }}>
          {/* Main view */}
          <div style={{ height: showDiff && diffAvailable ? "70%" : "100%" }}>
            {mode === "overlay" ? (
              <OverlayChart
                loaded={loaded}
                chartType={chartType}
                onChartReady={(chart) => {
                  overlayChartRef.current = chart;
                  bumpDiffSync();
                }}
              />
            ) : (
              <StackedCharts
                loaded={loaded}
                chartType={chartType}
                onChartsReady={(charts, series) => {
                  stackedChartsRef.current = charts;
                  stackedSeriesRef.current = series;
                  bumpDiffSync();
                }}
              />
            )}
          </div>

          {/* Diff panel — 30% height, shown when showDiff is on */}
          {showDiff && diffAvailable && (
            <div style={{ height: "30%" }} className="border-t border-gray-700">
              <DiffChart
                loaded={loaded}
                chartType={chartType}
                onChartReady={(chart, series) => {
                  diffChartRef.current = chart;
                  diffSeriesRef.current = series;
                  bumpDiffSync();
                }}
              />
            </div>
          )}
        </div>
      </div>
    </ViewportCtx.Provider>
  );
}

function ModeBar({
  mode,
  setMode,
  showDiff,
  setShowDiff,
  diffAvailable,
  chartType,
  setChartType,
}: {
  mode: CompareMode;
  setMode: (m: CompareMode) => void;
  showDiff: boolean;
  setShowDiff: (d: boolean) => void;
  diffAvailable: boolean;
  chartType: ChartType;
  setChartType: (t: ChartType) => void;
}): ReactNode {
  return (
    <div className="flex flex-wrap gap-2 items-center">
      {/* View mode buttons */}
      <div className="flex items-center gap-1">
        {(["overlay", "stacked"] as CompareMode[]).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            title={`View as ${m}`}
            className={`px-3 py-1.5 rounded text-sm transition-colors ${
              mode === m
                ? "bg-indigo-600 text-white"
                : "bg-gray-800 text-gray-300 hover:bg-gray-700"
            }`}
          >
            {m}
          </button>
        ))}
      </div>

      {/* Divider */}
      <div className="w-px h-5 bg-gray-700 mx-1" />

      {/* Diff toggle */}
      <button
        onClick={() => setShowDiff(!showDiff)}
        disabled={!diffAvailable}
        title={
          !diffAvailable
            ? "Diff requires at least 2 datasets"
            : showDiff
            ? "Hide diff chart"
            : "Show diff chart below"
        }
        className={`px-3 py-1 rounded text-xs font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
          showDiff
            ? "bg-amber-600 text-white"
            : "bg-gray-800 text-gray-400 hover:text-white hover:bg-gray-700"
        }`}
      >
        diff
      </button>

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
