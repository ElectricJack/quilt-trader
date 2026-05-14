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
  type Time,
  type UTCTimestamp,
  type LogicalRange,
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

// Distinct line colors for up to ~8 datasets.
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
  value: number;
}

/** Convert API bars → sorted, deduped (time, close) tuples for a Line series. */
function barsToLineData(bars: MarketDataBar[] | undefined): NormalizedRow[] {
  if (!bars) return [];
  const rows: NormalizedRow[] = [];
  for (const b of bars) {
    const t = Math.floor(new Date(b.timestamp).getTime() / 1000);
    if (!Number.isFinite(t) || !Number.isFinite(b.close)) continue;
    rows.push({ time: t as UTCTimestamp, value: b.close });
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
      rows: barsToLineData(q.data?.data),
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
 * Returns a stable callback ref that the caller stores in their chart ref.
 */
function useChartViewportSync(chart: IChartApi | null) {
  const { vp, setVisibleLogicalRange } = useContext(ViewportCtx);
  const ignoreNextRef = useRef(false);

  // Restore range when chart appears.
  useEffect(() => {
    if (!chart) return;
    if (vp.logicalRange) {
      ignoreNextRef.current = true;
      try {
        chart.timeScale().setVisibleLogicalRange(vp.logicalRange);
      } catch {
        // Ignore if data not yet present
      }
    }
  }, [chart, vp.logicalRange]);

  // Subscribe to range changes → push to context.
  useEffect(() => {
    if (!chart) return;
    const handler = () => {
      if (ignoreNextRef.current) {
        ignoreNextRef.current = false;
        return;
      }
      const r = chart.timeScale().getVisibleLogicalRange();
      if (r) setVisibleLogicalRange(r);
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(handler);
    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler);
    };
  }, [chart, setVisibleLogicalRange]);
}

const CHART_OPTIONS = {
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
} as const;

// ─── OverlayChart ─────────────────────────────────────────────────────────────

interface SubChartProps {
  loaded: LoadedSeries[];
}

function OverlayChart({ loaded }: SubChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [chart, setChart] = useState<IChartApi | null>(null);
  const seriesRefs = useRef<ISeriesApi<"Line">[]>([]);

  useEffect(() => {
    if (!containerRef.current) return;
    const c = createChart(containerRef.current, {
      ...CHART_OPTIONS,
      width: containerRef.current.clientWidth,
      height: 420,
    });
    setChart(c);
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) c.applyOptions({ width: entry.contentRect.width });
    });
    observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      c.remove();
      setChart(null);
      seriesRefs.current = [];
    };
  }, []);

  useChartViewportSync(chart);

  // (Re)build series whenever `loaded` changes.
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

    loaded.forEach((l, i) => {
      const color = SERIES_COLORS[i % SERIES_COLORS.length];
      const s = chart.addLineSeries({
        color,
        lineWidth: 2,
        priceLineVisible: false,
        title: datasetLabel(l.dataset),
      });
      s.setData(
        l.rows.map(
          (r): LineData<Time> => ({ time: r.time as Time, value: r.value })
        )
      );
      seriesRefs.current.push(s);
    });
  }, [chart, loaded]);

  return (
    <div className="space-y-2">
      <Legend loaded={loaded} />
      <div
        ref={containerRef}
        className="w-full rounded-lg overflow-hidden border border-gray-800"
        style={{ height: 420 }}
      />
    </div>
  );
}

// ─── StackedCharts ────────────────────────────────────────────────────────────

function StackedCharts({ loaded }: SubChartProps) {
  return (
    <div className="space-y-3">
      {loaded.map((l, i) => (
        <StackedRow key={datasetLabel(l.dataset)} loaded={l} colorIdx={i} />
      ))}
    </div>
  );
}

function StackedRow({
  loaded,
  colorIdx,
}: {
  loaded: LoadedSeries;
  colorIdx: number;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [chart, setChart] = useState<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const c = createChart(containerRef.current, {
      ...CHART_OPTIONS,
      width: containerRef.current.clientWidth,
      height: 220,
    });
    setChart(c);
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) c.applyOptions({ width: entry.contentRect.width });
    });
    observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      c.remove();
      setChart(null);
      seriesRef.current = null;
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
    const color = SERIES_COLORS[colorIdx % SERIES_COLORS.length];
    const s = chart.addLineSeries({
      color,
      lineWidth: 2,
      priceLineVisible: false,
      title: datasetLabel(loaded.dataset),
    });
    s.setData(
      loaded.rows.map(
        (r): LineData<Time> => ({ time: r.time as Time, value: r.value })
      )
    );
    seriesRef.current = s;
  }, [chart, loaded, colorIdx]);

  const color = SERIES_COLORS[colorIdx % SERIES_COLORS.length];

  return (
    <div>
      <div className="flex items-center gap-2 mb-1 text-xs font-mono text-gray-300">
        <span
          className="inline-block w-3 h-0.5 rounded"
          style={{ backgroundColor: color }}
        />
        {datasetLabel(loaded.dataset)}
      </div>
      <div
        ref={containerRef}
        className="w-full rounded-lg overflow-hidden border border-gray-800"
        style={{ height: 220 }}
      />
    </div>
  );
}

// ─── DiffChart ────────────────────────────────────────────────────────────────

function DiffChart({ loaded }: SubChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [chart, setChart] = useState<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);

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
      out.push({ time: k as UTCTimestamp, value: r.value - va });
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
      width: containerRef.current.clientWidth,
      height: 360,
    });
    setChart(c);
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) c.applyOptions({ width: entry.contentRect.width });
    });
    observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      c.remove();
      setChart(null);
      seriesRef.current = null;
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
  }, [chart, diffRows]);

  const [a, b] = loaded;
  return (
    <div className="space-y-2">
      <div className="text-xs text-gray-400">
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
        className="w-full rounded-lg overflow-hidden border border-gray-800"
        style={{ height: 360 }}
      />
    </div>
  );
}

// ─── Shared legend ────────────────────────────────────────────────────────────

function Legend({ loaded }: { loaded: LoadedSeries[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
      {loaded.map((l, i) => {
        const color = SERIES_COLORS[i % SERIES_COLORS.length];
        return (
          <div
            key={datasetLabel(l.dataset)}
            className="flex items-center gap-2 font-mono text-gray-300"
          >
            <span
              className="inline-block w-3 h-0.5 rounded"
              style={{ backgroundColor: color }}
            />
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
      <div className="space-y-3">
        <ModeBar
          mode={mode}
          setMode={setMode}
          diffAvailable={datasets.length === 2}
        />
        {mode === "overlay" && <OverlayChart loaded={loaded} />}
        {mode === "stacked" && <StackedCharts loaded={loaded} />}
        {mode === "diff" && datasets.length === 2 && (
          <DiffChart loaded={loaded} />
        )}
      </div>
    </ViewportCtx.Provider>
  );
}

function ModeBar({
  mode,
  setMode,
  diffAvailable,
}: {
  mode: CompareMode;
  setMode: (m: CompareMode) => void;
  diffAvailable: boolean;
}): ReactNode {
  const modes: CompareMode[] = ["overlay", "stacked", "diff"];
  return (
    <div className="flex gap-2">
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
    </div>
  );
}
