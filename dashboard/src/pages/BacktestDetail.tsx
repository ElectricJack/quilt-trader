import { useParams, Link } from "react-router-dom";
import { ChevronLeft } from "lucide-react";
import { useBacktest } from "../api/hooks";
import { MetricsCard } from "../components/MetricsCard";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function matchBarColor(pct: number): string {
  if (pct >= 90) return "bg-green-500";
  if (pct >= 70) return "bg-yellow-500";
  return "bg-red-500";
}

function matchTextColor(pct: number): string {
  if (pct >= 90) return "text-green-400";
  if (pct >= 70) return "text-yellow-400";
  return "text-red-400";
}

function fmtDate(val: string | null): string {
  if (!val) return "—";
  return new Date(val).toLocaleString();
}

function fmtShortDate(val: string | null): string {
  if (!val) return "—";
  return new Date(val).toLocaleDateString();
}

// ─── Divergence Item ──────────────────────────────────────────────────────────

function DivergenceItem({
  div,
  index,
}: {
  div: Record<string, unknown>;
  index: number;
}) {
  const timestamp =
    typeof div.timestamp === "string" ? div.timestamp : null;
  const reason =
    typeof div.reason === "string"
      ? div.reason
      : typeof div.type === "string"
      ? div.type
      : null;
  const liveSignal = div.live_signal;
  const backtestSignal = div.backtest_signal;

  // Collect remaining fields (exclude already-rendered ones)
  const knownKeys = new Set(["timestamp", "reason", "type", "live_signal", "backtest_signal"]);
  const remainingEntries = Object.entries(div).filter(([k]) => !knownKeys.has(k));

  return (
    <div className="border-l-2 border-gray-700 pl-4 py-2 relative">
      {/* Timeline dot */}
      <span className="absolute -left-[5px] top-3 w-2 h-2 rounded-full bg-gray-600 inline-block" />

      {/* Timestamp header */}
      {timestamp && (
        <p className="text-xs text-gray-500 mb-1 font-mono">
          {new Date(timestamp).toLocaleString()}
        </p>
      )}

      {/* Reason / type */}
      {reason && (
        <p className="text-sm font-semibold text-gray-200 mb-1">{reason}</p>
      )}

      {/* Fallback header when there's nothing else */}
      {!timestamp && !reason && (
        <p className="text-xs text-gray-500 mb-1">Divergence #{index + 1}</p>
      )}

      {/* Live vs backtest signals */}
      {(liveSignal !== undefined || backtestSignal !== undefined) && (
        <div className="flex gap-4 mt-1 mb-2">
          <div className="flex-1 bg-gray-800 rounded p-2">
            <span className="text-xs text-gray-500 uppercase block mb-0.5">Live Signal</span>
            <span className="text-xs text-gray-300 font-mono">
              {liveSignal !== null && liveSignal !== undefined
                ? typeof liveSignal === "object"
                  ? JSON.stringify(liveSignal)
                  : String(liveSignal)
                : "—"}
            </span>
          </div>
          <div className="flex-1 bg-gray-800 rounded p-2">
            <span className="text-xs text-gray-500 uppercase block mb-0.5">Backtest Signal</span>
            <span className="text-xs text-gray-300 font-mono">
              {backtestSignal !== null && backtestSignal !== undefined
                ? typeof backtestSignal === "object"
                  ? JSON.stringify(backtestSignal)
                  : String(backtestSignal)
                : "—"}
            </span>
          </div>
        </div>
      )}

      {/* Remaining fields as formatted JSON */}
      {remainingEntries.length > 0 && (
        <pre className="text-xs text-gray-400 overflow-x-auto whitespace-pre-wrap bg-gray-800/50 rounded p-2 mt-1">
          {JSON.stringify(Object.fromEntries(remainingEntries), null, 2)}
        </pre>
      )}
    </div>
  );
}

// ─── Component ────────────────────────────────────────────────────────────────

export function BacktestDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: bt, isLoading } = useBacktest(id ?? "");

  if (isLoading) {
    return <p className="text-gray-400 text-sm">Loading…</p>;
  }

  if (!bt) {
    return <p className="text-gray-400 text-sm">Backtest not found.</p>;
  }

  const pct = bt.match_percentage;
  const textColor = matchTextColor(pct);
  const barColor = matchBarColor(pct);

  return (
    <div className="space-y-6">
      {/* ── Header ── */}
      <div className="flex items-center gap-3">
        <Link
          to="/backtests"
          className="flex items-center gap-1 text-sm text-gray-400 hover:text-gray-200 transition-colors"
        >
          <ChevronLeft size={16} />
          Backtests
        </Link>
        <h1 className="text-2xl font-bold text-white">Backtest Comparison</h1>
      </div>

      {/* ── Summary card ── */}
      {bt.summary && (
        <div className="bg-indigo-950 border border-indigo-800 rounded-lg p-4">
          <p className="text-sm text-indigo-200">{bt.summary}</p>
        </div>
      )}

      {/* ── Match percentage visualization ── */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 space-y-3">
        <div className="flex items-baseline gap-3">
          <span className={`text-3xl font-bold ${textColor}`}>
            {pct.toFixed(2)}%
          </span>
          <span className="text-sm text-gray-400">match rate</span>
        </div>
        <div className="bg-gray-700 rounded-full h-4 w-full overflow-hidden">
          <div
            className={`h-full rounded-full ${barColor} transition-all`}
            style={{ width: `${Math.min(pct, 100)}%` }}
          />
        </div>
        <p className="text-sm text-gray-400">
          {bt.matching_ticks.toLocaleString()} of{" "}
          {bt.total_ticks.toLocaleString()} ticks matched
        </p>
      </div>

      {/* ── Metrics cards ── */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <MetricsCard label="Total Ticks" value={bt.total_ticks.toLocaleString()} />
        <MetricsCard label="Matching Ticks" value={bt.matching_ticks.toLocaleString()} />
        <MetricsCard label="Match %" value={`${pct.toFixed(2)}%`} />
        <MetricsCard label="Time Range Start" value={fmtShortDate(bt.time_range_start)} />
        <MetricsCard label="Time Range End" value={fmtShortDate(bt.time_range_end)} />
        <MetricsCard label="Created At" value={fmtDate(bt.created_at)} />
      </div>

      {/* ── Details grid ── */}
      <div className="bg-gray-900 border border-gray-800 rounded p-4 space-y-3">
        <h2 className="text-lg font-semibold text-gray-200">Details</h2>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-xs text-gray-500 uppercase">Instance</span>
            <p className="text-gray-200 mt-0.5">
              <Link
                to={`/instances/${bt.instance_id}`}
                className="text-indigo-400 hover:underline font-mono text-xs"
              >
                {bt.instance_id.slice(0, 8)}…
              </Link>
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Algorithm</span>
            <p className="text-gray-200 mt-0.5">
              <Link
                to={`/algorithms/${bt.algorithm_id}`}
                className="text-indigo-400 hover:underline font-mono text-xs"
              >
                {bt.algorithm_id.slice(0, 8)}…
              </Link>
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Time Range Start</span>
            <p className="text-gray-200 mt-0.5 text-xs">{fmtDate(bt.time_range_start)}</p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Time Range End</span>
            <p className="text-gray-200 mt-0.5 text-xs">{fmtDate(bt.time_range_end)}</p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Created</span>
            <p className="text-gray-200 mt-0.5 text-xs">{fmtDate(bt.created_at)}</p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Backtest ID</span>
            <p className="text-gray-200 mt-0.5 font-mono text-xs">{bt.id}</p>
          </div>
        </div>
      </div>

      {/* ── Divergences ── */}
      <section>
        <h2 className="text-lg font-semibold text-gray-200 mb-3">
          Divergences{" "}
          {bt.divergences && bt.divergences.length > 0 && (
            <span className="text-gray-400 text-base font-normal">
              ({bt.divergences.length})
            </span>
          )}
        </h2>

        {!bt.divergences || bt.divergences.length === 0 ? (
          <p className="text-sm text-gray-400 italic">
            No divergences — live and backtest decisions matched perfectly.
          </p>
        ) : (
          <div className="space-y-1">
            {bt.divergences.map((div, i) => (
              <DivergenceItem key={i} div={div} index={i} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
