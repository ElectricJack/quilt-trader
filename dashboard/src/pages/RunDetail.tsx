import { useParams, Link } from "react-router-dom";
import { ChevronLeft } from "lucide-react";
import { useRun } from "../api/hooks";
import { StatusBadge } from "../components/StatusBadge";
import { MetricsCard } from "../components/MetricsCard";
import { EquityCurve } from "../components/EquityCurve";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmtDollar(val: number | null | undefined): string {
  if (val == null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  }).format(val);
}

function humanizeKey(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

// ─── Component ────────────────────────────────────────────────────────────────

export function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: run, isLoading } = useRun(id ?? "");

  if (isLoading) {
    return <p className="text-gray-400 text-sm">Loading…</p>;
  }

  if (!run) {
    return <p className="text-gray-400 text-sm">Run not found.</p>;
  }

  // ── Duration ──────────────────────────────────────────────────────────────
  const durationSec =
    run.started_at && run.stopped_at
      ? Math.round(
          (new Date(run.stopped_at).getTime() - new Date(run.started_at).getTime()) / 1000
        )
      : null;

  // ── Equity curve ──────────────────────────────────────────────────────────
  const equityCurveData =
    run.equity_curve?.map((p) => ({ timestamp: p.date, equity: p.equity })) ?? null;

  return (
    <div className="space-y-6">
      {/* ── Header ── */}
      <div className="flex items-center gap-4 flex-wrap">
        <Link
          to={`/instances/${run.instance_id}`}
          className="flex items-center gap-1 text-sm text-gray-400 hover:text-gray-200 transition-colors"
        >
          <ChevronLeft size={16} />
          Instance
        </Link>

        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-white">Run #{run.run_number}</h1>
          <StatusBadge status={run.status} />
        </div>
      </div>

      {/* ── Financial Summary ── */}
      <section>
        <h2 className="text-lg font-semibold text-gray-200 mb-3">Financial Summary</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <MetricsCard label="Starting Equity" value={fmtDollar(run.starting_equity)} />
          <MetricsCard label="Ending Equity" value={fmtDollar(run.ending_equity)} />
          <MetricsCard
            label="Net P&L"
            value={fmtDollar(run.net_pnl)}
            change={run.net_pnl ?? undefined}
          />
          <MetricsCard label="Unrealized P&L" value={fmtDollar(run.unrealized_pnl)} />
          <MetricsCard label="Total Fees" value={fmtDollar(run.total_fees)} />
          <MetricsCard label="Total Slippage" value={fmtDollar(run.total_slippage)} />
          <MetricsCard label="Trade Count" value={run.trade_count} />
        </div>
      </section>

      {/* ── Equity Curve ── */}
      {equityCurveData && equityCurveData.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold text-gray-200 mb-3">Equity Curve</h2>
          <div className="bg-gray-900 border border-gray-800 rounded p-4">
            <EquityCurve data={equityCurveData} height={280} />
          </div>
        </section>
      )}

      {/* ── Timing ── */}
      <div className="bg-gray-900 border border-gray-800 rounded p-4 space-y-3">
        <h2 className="text-lg font-semibold text-gray-200">Timing</h2>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-xs text-gray-500 uppercase">Started At</span>
            <p className="text-gray-200 mt-0.5 text-xs">
              {run.started_at ? new Date(run.started_at).toLocaleString() : "—"}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Stopped At</span>
            <p className="text-gray-200 mt-0.5 text-xs">
              {run.stopped_at ? new Date(run.stopped_at).toLocaleString() : "—"}
            </p>
          </div>
          {durationSec !== null && (
            <div>
              <span className="text-xs text-gray-500 uppercase">Duration</span>
              <p className="text-gray-200 mt-0.5 text-xs">{durationSec}s</p>
            </div>
          )}
        </div>

        {run.status === "error" && (
          <div className="bg-red-950 border border-red-800 rounded p-3 mt-2">
            <p className="text-xs text-red-400 font-mono">Run ended with error status</p>
          </div>
        )}
      </div>

      {/* ── Performance Metrics ── */}
      {run.metrics && Object.keys(run.metrics).length > 0 && (
        <section>
          <h2 className="text-lg font-semibold text-gray-200 mb-3">Performance Metrics</h2>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {Object.entries(run.metrics).map(([key, val]) => (
              <MetricsCard
                key={key}
                label={humanizeKey(key)}
                value={
                  typeof val === "number"
                    ? val.toLocaleString()
                    : String(val)
                }
              />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
