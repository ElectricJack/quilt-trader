import { useMemo, useState } from "react";
import type { SeriesMarker, Time } from "lightweight-charts";
import { BacktestChart, type BacktestEquityPoint, type BacktestBenchmarkPoint, type BacktestTradeMarker } from "../BacktestChart";
import { MaximizableCard } from "./MaximizableCard";
import type { BacktestReport } from "../../types";
import { useBacktestEquityWindow } from "../../api/hooks";

interface RunEntry {
  run_id: string;
  run_number: number;
  started_at: string | null;
  stopped_at: string | null;
  status: string;
}

interface Props {
  report: BacktestReport;
  trades: { timestamp: string; symbol: string; side: string; quantity: number; fill_price: number | null }[];
  runsIndex?: RunEntry[];
}

export function buildRunMarkers(
  runsIndex: RunEntry[] | null | undefined,
): SeriesMarker<Time>[] {
  if (!runsIndex) return [];
  const out: SeriesMarker<Time>[] = [];
  for (const r of runsIndex) {
    if (r.started_at) {
      out.push({
        time: (Date.parse(r.started_at) / 1000) as Time,
        position: "aboveBar",
        color: "#22c55e",
        shape: "arrowUp",
        text: `Run #${r.run_number} start`,
      });
    }
    if (r.stopped_at) {
      out.push({
        time: (Date.parse(r.stopped_at) / 1000) as Time,
        position: "belowBar",
        color: "#6b7280",
        shape: "arrowDown",
        text: `Run #${r.run_number} stop`,
      });
    }
  }
  return out;
}

interface VisibleRange { from: string | null; to: string | null; }

export function EquitySlot({ report, trades, runsIndex }: Props) {
  const [logScale, setLogScale] = useState(false);
  const [showVolMatched, setShowVolMatched] = useState(false);
  const [visible, setVisible] = useState<VisibleRange>({ from: null, to: null });

  // Decide what resolution to fetch based on the visible range.
  // Use "auto" so the server picks the highest-available resolution and
  // gracefully falls back to 1day when 1min/1hour parquet files don't
  // exist (current pipeline only writes 1day; the others are a v2 add-on).
  const zoomParams = useMemo(() => {
    if (!visible.from || !visible.to) return null;
    const days = (Date.parse(visible.to) - Date.parse(visible.from)) / 86_400_000;
    if (days > 60) return null;  // daily already in report
    return { from: visible.from, to: visible.to, resolution: "auto" as const };
  }, [visible]);

  const { data: zoomedEquity } = useBacktestEquityWindow(report.id, zoomParams);

  const baseEquity: BacktestEquityPoint[] = (report.equity_curve ?? []).map((p) => ({
    timestamp: p.timestamp,
    portfolio_value: p.portfolio_value,
    cash: p.cash,
  }));

  const equityPoints: BacktestEquityPoint[] = useMemo(() => {
    if (!zoomedEquity || !zoomParams) return baseEquity;
    // Splice the zoomed range into the base series
    const before = baseEquity.filter((p) => p.timestamp < zoomParams.from);
    const after = baseEquity.filter((p) => p.timestamp > zoomParams.to);
    const inside = zoomedEquity.items.map((it) => ({
      timestamp: it.ts, portfolio_value: it.portfolio_value, cash: it.cash,
    }));
    return [...before, ...inside, ...after];
  }, [baseEquity, zoomedEquity, zoomParams]);

  const benchmarkPoints: BacktestBenchmarkPoint[] = useMemo(() => {
    const raw = (report.benchmark_equity_curve ?? []).map((p) => ({
      timestamp: p.timestamp, value: p.value,
    }));
    if (!showVolMatched || raw.length < 2 || baseEquity.length < 2) return raw;
    // Vol-match: scale benchmark daily returns by (strategy_std / benchmark_std), recompound
    const stratRets: number[] = [];
    for (let i = 1; i < baseEquity.length; i++) {
      const prev = baseEquity[i - 1].portfolio_value;
      if (prev > 0) stratRets.push(baseEquity[i].portfolio_value / prev - 1);
    }
    const benchRets: number[] = [];
    for (let i = 1; i < raw.length; i++) {
      const prev = raw[i - 1].value;
      if (prev > 0) benchRets.push(raw[i].value / prev - 1);
    }
    const std = (xs: number[]) => {
      if (xs.length < 2) return 0;
      const m = xs.reduce((a, b) => a + b, 0) / xs.length;
      return Math.sqrt(xs.reduce((a, b) => a + (b - m) ** 2, 0) / (xs.length - 1));
    };
    const sStd = std(stratRets);
    const bStd = std(benchRets);
    if (bStd === 0) return raw;
    const scale = sStd / bStd;
    const out: BacktestBenchmarkPoint[] = [{ timestamp: raw[0].timestamp, value: raw[0].value }];
    let cum = raw[0].value;
    for (const r of benchRets) {
      cum = cum * (1 + r * scale);
      out.push({ timestamp: raw[out.length].timestamp, value: cum });
    }
    return out;
  }, [report.benchmark_equity_curve, baseEquity, showVolMatched]);

  const tradeMarkers: BacktestTradeMarker[] = trades
    .filter((t) => t.fill_price !== null && (t.side === "buy" || t.side === "sell"))
    .map((t) => ({
      timestamp: t.timestamp,
      side: t.side as "buy" | "sell",
      symbol: t.symbol,
      quantity: t.quantity,
      fill_price: t.fill_price as number,
    }));

  const runMarkers = useMemo(() => buildRunMarkers(runsIndex), [runsIndex]);

  const toolbar = (
    <div className="flex gap-2 text-xs">
      <label className="flex items-center gap-1 text-gray-400">
        <input type="checkbox" checked={logScale} onChange={(e) => setLogScale(e.target.checked)} />
        Log scale
      </label>
      <label className="flex items-center gap-1 text-gray-400">
        <input type="checkbox" checked={showVolMatched} onChange={(e) => setShowVolMatched(e.target.checked)} />
        Vol-matched
      </label>
    </div>
  );

  return (
    <MaximizableCard title="Equity" toolbar={toolbar}>
      {(isMax) => (
        <BacktestChart
          equity={equityPoints}
          benchmark={benchmarkPoints}
          trades={tradeMarkers}
          runMarkers={runMarkers}
          benchmarkLabel={
            report.benchmark_symbol
              ? `Benchmark${showVolMatched ? " (vol-matched)" : ""} (${report.benchmark_symbol})`
              : "Benchmark"
          }
          height={isMax ? Math.max(500, window.innerHeight - 200) : 300}
          logScale={logScale}
          onVisibleRangeChange={(from, to) => setVisible({ from, to })}
        />
      )}
    </MaximizableCard>
  );
}
