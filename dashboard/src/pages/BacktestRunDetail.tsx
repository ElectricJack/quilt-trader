// ── Spec D U2: backtest run detail ──
import { useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { ChevronLeft, Trash2 } from "lucide-react";
import {
  useBacktestReport,
  useBacktestTrades,
  useDeleteBacktestRun,
} from "../api/hooks";
import { StatusBadge } from "../components/StatusBadge";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useUIStore } from "../stores/ui";
import { KpiCard } from "../components/report/KpiCard";
import { ParametersTable } from "../components/report/ParametersTable";
import { EoyTable } from "../components/report/EoyTable";
import { DrawdownsTable } from "../components/report/DrawdownsTable";
import { MetricsTable } from "../components/report/MetricsTable";
import { EquitySlot } from "../components/report/EquitySlot";
import { DrawdownSlot } from "../components/report/DrawdownSlot";
import { ReturnsDistributionSlot } from "../components/report/ReturnsDistributionSlot";
import { RollingMetricsSlot } from "../components/report/RollingMetricsSlot";

const INFLIGHT_STATUSES = ["queued", "downloading_data", "running"];
function inflight(status: string | undefined | null): boolean {
  return !!status && INFLIGHT_STATUSES.includes(status);
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(2)}%`;
}

function fmtInt(v: number | null | undefined): string {
  if (v == null) return "—";
  return Math.round(v).toString();
}

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null) return "—";
  return v.toLocaleString("en-US", { maximumFractionDigits: digits });
}

interface BacktestTradeRow {
  timestamp: string; symbol: string; side: string; quantity: number;
  requested_price: number | null; fill_price: number | null;
  slippage_dollars: number | null; fees: number | null; realized_pnl: number | null;
}

export function BacktestRunDetail() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const addAlert = useUIStore((s) => s.addAlert);
  const { data: report } = useBacktestReport(id, { refetchInterval: 2000 });
  const isRunInflight = inflight(report?.status);
  const liveRefetch = isRunInflight ? 2000 : undefined;
  const { data: tradesData } = useBacktestTrades(id, 500, 0, { refetchInterval: liveRefetch });
  const del = useDeleteBacktestRun();
  const [deleteOpen, setDeleteOpen] = useState(false);

  async function handleDelete() {
    try {
      await del.mutateAsync(id);
      addAlert({ message: "Deleted backtest run.", severity: "success" });
      navigate("/backtests");
    } catch {
      addAlert({ message: "Failed to delete backtest run.", severity: "error" });
      setDeleteOpen(false);
    }
  }

  if (!report) {
    return <div className="p-4 text-gray-400">Loading…</div>;
  }

  const trades = ((tradesData?.items ?? []) as BacktestTradeRow[]) ?? [];
  const totalTrades = (tradesData as { total?: number } | undefined)?.total ?? trades.length;
  const km = report.key_metrics?.strategy;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/backtests" className="text-gray-400 hover:text-white">
            <ChevronLeft size={20} />
          </Link>
          <h1 className="text-xl font-bold">Backtest Run</h1>
          <StatusBadge status={report.status} />
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setDeleteOpen(true)}
            disabled={del.isPending}
            className="flex items-center gap-1 px-3 py-1.5 rounded text-sm text-red-300 bg-red-900/40 border border-red-800 hover:bg-red-900/60 disabled:opacity-50"
          >
            <Trash2 size={14} /> Delete
          </button>
        </div>
      </div>

      {/* In-flight progress */}
      {isRunInflight && (
        <div className="bg-gray-900 border border-gray-800 rounded p-3">
          <div className="text-sm text-gray-300 mb-2">{report.progress_message ?? report.status}</div>
          <div className="bg-gray-700 rounded-full h-2 overflow-hidden">
            <div
              className="bg-indigo-600 h-2 transition-[width] ease-linear duration-[2000ms]"
              style={{ width: `${(report.progress_pct ?? 0) * 100}%` }}
            />
          </div>
        </div>
      )}

      {/* Incomplete-data banner for legacy rows */}
      {report.status === "completed" && !report.key_metrics && (
        <div className="bg-yellow-900/30 border border-yellow-800 rounded p-3 text-sm text-yellow-200">
          This backtest pre-dates the report system. Re-run it to populate the new metrics.
        </div>
      )}

      {/* KPI row */}
      {km && (
        <div className="grid grid-cols-1 md:grid-cols-7 gap-3">
          <KpiCard variant="hero" label="Annual Return" value={fmtPct(km.cagr)} hint="CAGR" />
          <KpiCard label="Total Return" value={fmtPct(km.total_return)} />
          <KpiCard label="Max Drawdown" value={fmtPct(km.max_drawdown)} />
          <KpiCard label="RoMaD" value={fmtNum(km.romad)} hint="CAGR / Max Drawdown" />
          <KpiCard label="Sharpe" value={fmtNum(km.sharpe_ratio)} />
          <KpiCard label="Sortino" value={fmtNum(km.sortino_ratio)} />
          <KpiCard label="Longest DD Days" value={fmtInt(km.longest_drawdown_days)} />
        </div>
      )}

      {/* 4 chart slots — 2x2 grid at wide widths */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <EquitySlot report={report} trades={trades} />
        <DrawdownSlot report={report} />
        <ReturnsDistributionSlot report={report} />
        <RollingMetricsSlot report={report} />
      </div>

      {/* Side tables — 3-col at wide widths */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <ParametersTable params={report.config_overrides} />
        <EoyTable rows={report.eoy_returns} />
        <DrawdownsTable rows={report.drawdown_periods} />
      </div>

      {/* Metrics + Trades side-by-side at wide widths */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 items-start">
        <MetricsTable
          strategy={report.key_metrics?.strategy}
          benchmark={report.key_metrics?.benchmark}
        />
        <div className="bg-gray-900 border border-gray-800 rounded">
          <div className="px-3 py-2 border-b border-gray-800 text-sm font-semibold text-gray-300">
            Trades ({totalTrades})
          </div>
          <div className="overflow-auto max-h-[800px]">
            <table className="w-full text-sm">
              <thead className="bg-gray-800 text-xs uppercase text-gray-400 sticky top-0">
                <tr>
                  <th className="text-left p-2">Timestamp</th>
                  <th className="text-left p-2">Symbol</th>
                  <th className="text-left p-2">Side</th>
                  <th className="text-right p-2">Qty</th>
                  <th className="text-right p-2">Fill</th>
                  <th className="text-right p-2">Realized P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => (
                  <tr key={i} className="border-t border-gray-800">
                    <td className="p-2 text-xs text-gray-400">{new Date(t.timestamp).toLocaleString()}</td>
                    <td className="p-2 font-mono">{t.symbol}</td>
                    <td className={`p-2 ${t.side === "buy" ? "text-green-400" : "text-red-400"}`}>{t.side}</td>
                    <td className="p-2 text-right">{fmtNum(t.quantity, 4)}</td>
                    <td className="p-2 text-right font-semibold">
                      {t.fill_price === null ? "—" : t.fill_price.toLocaleString("en-US", { style: "currency", currency: "USD" })}
                    </td>
                    <td className={`p-2 text-right ${
                      t.realized_pnl == null ? "text-gray-500" : t.realized_pnl > 0 ? "text-green-400" : "text-red-400"
                    }`}>
                      {t.realized_pnl == null ? "—" :
                        t.realized_pnl.toLocaleString("en-US", { style: "currency", currency: "USD" })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={deleteOpen}
        title="Delete backtest run"
        message="Are you sure you want to delete this backtest run? This cannot be undone."
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setDeleteOpen(false)}
      />
    </div>
  );
}
