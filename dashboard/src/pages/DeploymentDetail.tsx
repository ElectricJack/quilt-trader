import { useState } from "react";
import { useParams, Link, useSearchParams, useNavigate } from "react-router-dom";
import { ChevronLeft, Trash2 } from "lucide-react";
import {
  useDeployment,
  useDeploymentReport,
  useDeploymentRuns,
  useDeploymentTrades,
  useStartDeployment,
  useStopDeployment,
  useDeleteDeployment,
  useRedeployDeployment,
} from "../api/hooks";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { StatusBadge } from "../components/StatusBadge";
import { useUIStore } from "../stores/ui";
import { KpiCard } from "../components/report/KpiCard";
import { EquitySlot } from "../components/report/EquitySlot";
import { DrawdownSlot } from "../components/report/DrawdownSlot";
import { ReturnsDistributionSlot } from "../components/report/ReturnsDistributionSlot";
import { RollingMetricsSlot } from "../components/report/RollingMetricsSlot";
import { ParametersTable } from "../components/report/ParametersTable";
import { EoyTable } from "../components/report/EoyTable";
import { DrawdownsTable } from "../components/report/DrawdownsTable";
import { MetricsTable } from "../components/report/MetricsTable";
import { DataTable, type ColumnDef } from "../components/DataTable";
import { fmtPct, fmtInt, fmtNum } from "../lib/formatNumbers";
import type { AlgorithmRun } from "../types";
import { ActivityPanel } from "../components/ActivityPanel";

// ── Runs table column definitions ────────────────────────────────────────────

const runsColumns: ColumnDef<AlgorithmRun, unknown>[] = [
  {
    id: "run_number",
    accessorKey: "run_number",
    header: "Run #",
    cell: ({ row }) => `#${row.original.run_number}`,
  },
  {
    id: "status",
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <StatusBadge status={row.original.status} />,
  },
  {
    id: "started_at",
    accessorKey: "started_at",
    header: "Started",
    cell: ({ row }) =>
      row.original.started_at
        ? new Date(row.original.started_at).toLocaleString()
        : "—",
  },
  {
    id: "stopped_at",
    accessorKey: "stopped_at",
    header: "Ended",
    cell: ({ row }) =>
      row.original.stopped_at
        ? new Date(row.original.stopped_at).toLocaleString()
        : "—",
  },
  {
    id: "duration",
    header: "Duration",
    cell: ({ row }) => {
      if (!row.original.started_at) return "—";
      const end = row.original.stopped_at
        ? new Date(row.original.stopped_at)
        : new Date();
      const secs = Math.round(
        (end.getTime() - new Date(row.original.started_at).getTime()) / 1000,
      );
      if (secs < 60) return `${secs}s`;
      if (secs < 3600) return `${Math.round(secs / 60)}m`;
      return `${Math.round(secs / 3600)}h`;
    },
  },
  {
    id: "net_pnl",
    accessorKey: "net_pnl",
    header: "Net P&L",
    cell: ({ row }) =>
      row.original.net_pnl == null
        ? "—"
        : row.original.net_pnl.toLocaleString("en-US", {
            style: "currency",
            currency: "USD",
          }),
  },
  {
    id: "trade_count",
    accessorKey: "trade_count",
    header: "Trades",
    cell: ({ row }) => row.original.trade_count,
  },
];

// ── Component ─────────────────────────────────────────────────────────────────

export function DeploymentDetail() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const runFilter = searchParams.get("run") ?? "";

  const { data: dep, isLoading } = useDeployment(id);
  const start = useStartDeployment();
  const stop = useStopDeployment();
  const del = useDeleteDeployment();
  const redeploy = useRedeployDeployment();
  const addAlert = useUIStore((s) => s.addAlert);
  const [deleteOpen, setDeleteOpen] = useState(false);

  // Compute liveness defensively so hooks can run unconditionally on first render
  // (before `dep` has loaded). Rules-of-hooks: every render must call the same
  // hooks in the same order, so we cannot early-return between these.
  const isLive =
    dep?.status === "running" ||
    dep?.status === "starting" ||
    dep?.status === "stopping";

  const { data: report } = useDeploymentReport(id, { refetchInterval: isLive ? 2000 : false });
  const { data: runs } = useDeploymentRuns(id);

  // TODO(M6.4-known-limitation): The backend report endpoint (/api/deployments/:id/report)
  // does not currently accept a run_id filter param. The run filter dropdown therefore only
  // filters the trades table, not the report charts / KPI / side tables / metrics panel.
  // When the backend supports per-run reports, pass runFilter here too.
  const { data: tradesData } = useDeploymentTrades(id, {
    refetchInterval: isLive ? 2000 : false,
    run_id: runFilter || undefined,
  });

  if (isLoading) return <p className="text-gray-400 text-sm">Loading…</p>;
  if (!dep) return <p className="text-gray-400 text-sm">Deployment not found.</p>;

  const canStart = dep.status === "stopped" || dep.status === "error";
  const isRunning = dep.status === "running" || dep.status === "starting";
  const trades = tradesData?.items ?? [];
  const km = report?.key_metrics?.strategy;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <Link
          to={`/algorithms/${dep.algorithm_id}`}
          className="flex items-center gap-1 text-sm text-gray-400 hover:text-gray-200 transition-colors"
        >
          <ChevronLeft size={16} />
          {dep.algorithm_name}
        </Link>

        <div className="flex items-center gap-3 flex-1 min-w-0">
          <h1 className="text-2xl font-bold text-white truncate">
            {dep.algorithm_name}
          </h1>
          <StatusBadge status={dep.status} />
        </div>

        <div className="text-sm text-gray-400">
          <Link
            to={`/accounts/${dep.account_id}`}
            className="hover:text-gray-200"
          >
            {dep.account_name}
          </Link>
          {" · "}
          <Link
            to={`/workers/${dep.worker_id}`}
            className="hover:text-gray-200"
          >
            {dep.worker_name}
          </Link>
        </div>

        <div className="flex gap-2 ml-auto">
          {canStart && (
            <button
              onClick={() =>
                start.mutate(id, {
                  onError: () =>
                    addAlert({
                      message: "Failed to start deployment.",
                      severity: "error",
                    }),
                })
              }
              disabled={start.isPending}
              className="bg-green-600 hover:bg-green-500 disabled:opacity-50 text-white text-sm px-3 py-1.5 rounded"
            >
              Start
            </button>
          )}
          {isRunning && (
            <button
              onClick={() =>
                stop.mutate(id, {
                  onError: () =>
                    addAlert({
                      message: "Failed to stop deployment.",
                      severity: "error",
                    }),
                })
              }
              disabled={stop.isPending}
              className="bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white text-sm px-3 py-1.5 rounded"
            >
              Stop
            </button>
          )}
          <button
            onClick={() =>
              redeploy.mutate(id, {
                onSuccess: (data) => {
                  addAlert({
                    message: `Redeployed with commit ${data.commit_hash_short}${data.restarted ? " — instance restarted" : ""}`,
                    severity: "success",
                  });
                },
                onError: (err) => {
                  addAlert({
                    message: `Redeploy failed: ${(err as Error).message}`,
                    severity: "error",
                  });
                },
              })
            }
            disabled={redeploy.isPending}
            className="px-4 py-2 rounded text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 transition-colors"
          >
            {redeploy.isPending ? "Redeploying…" : "Redeploy"}
          </button>
          <button
            onClick={() => setDeleteOpen(true)}
            disabled={isRunning}
            title={isRunning ? "Stop the deployment first" : "Delete deployment"}
            className="inline-flex items-center gap-1.5 bg-red-800 hover:bg-red-700 disabled:opacity-40 disabled:cursor-not-allowed text-red-100 text-sm px-3 py-1.5 rounded"
          >
            <Trash2 size={14} />
            Delete
          </button>
        </div>
      </div>

      <ConfirmDialog
        open={deleteOpen}
        title="Delete deployment"
        message={`Permanently delete this deployment of "${dep.algorithm_name}" on ${dep.worker_name}? Run history will also be removed. This cannot be undone.`}
        confirmLabel="Delete"
        onConfirm={() => {
          del.mutate(id, {
            onSuccess: () => {
              addAlert({ message: "Deployment deleted.", severity: "success" });
              navigate(`/algorithms/${dep.algorithm_id}`);
            },
            onError: () => {
              addAlert({ message: "Failed to delete deployment.", severity: "error" });
              setDeleteOpen(false);
            },
          });
        }}
        onCancel={() => setDeleteOpen(false)}
      />

      {/* Run filter dropdown — placed ABOVE the KPI row.
          NOTE: currently only filters the trades table; see TODO above. */}
      <div className="flex justify-end">
        <select
          value={runFilter}
          onChange={(e) => {
            const next = e.target.value;
            const sp = new URLSearchParams(searchParams);
            if (next) sp.set("run", next);
            else sp.delete("run");
            setSearchParams(sp);
          }}
          className="bg-gray-800 border border-gray-700 text-gray-200 rounded px-2 py-1 text-xs"
        >
          <option value="">All runs (lifetime)</option>
          {(runs ?? []).map((r) => (
            <option key={r.id} value={r.id}>
              {`Run #${r.run_number}`}
              {r.status === "running" ? " (current)" : ""}
              {r.started_at
                ? ` — ${new Date(r.started_at).toLocaleDateString()}`
                : ""}
              {r.stopped_at
                ? ` to ${new Date(r.stopped_at).toLocaleDateString()}`
                : ""}
            </option>
          ))}
        </select>
      </div>

      {report ? (
        <>
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

          {/* Chart grid */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
            <EquitySlot report={report as any} trades={[]} runsIndex={report.runs_index ?? undefined} />
            {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
            <DrawdownSlot report={report as any} />
            {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
            <ReturnsDistributionSlot report={report as any} />
            {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
            <RollingMetricsSlot report={report as any} />
          </div>

          {/* Side tables — 3-col at md+ */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <ParametersTable params={dep.config_values} />
            {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
            <EoyTable rows={(report?.eoy_returns ?? null) as any} />
            {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
            <DrawdownsTable rows={(report?.drawdown_periods ?? null) as any} />
          </div>

          {/* Metrics + Trades — 2-col at lg+ */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 items-start">
            <MetricsTable
              /* eslint-disable-next-line @typescript-eslint/no-explicit-any */
              strategy={(report?.key_metrics?.strategy ?? undefined) as any}
              /* eslint-disable-next-line @typescript-eslint/no-explicit-any */
              benchmark={(report?.key_metrics?.benchmark ?? undefined) as any}
            />
            <div className="bg-gray-900 border border-gray-800 rounded">
              <div className="px-3 py-2 border-b border-gray-800 text-sm font-semibold text-gray-300">
                Trades ({trades.length})
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
                    {trades.map((t) => (
                      <tr key={t.id} className="border-t border-gray-800">
                        <td className="p-2 text-xs text-gray-400">
                          {new Date(t.timestamp).toLocaleString()}
                        </td>
                        <td className="p-2 font-mono">{t.symbol}</td>
                        <td
                          className={`p-2 ${t.side === "buy" ? "text-green-400" : "text-red-400"}`}
                        >
                          {t.side}
                        </td>
                        <td className="p-2 text-right">{fmtNum(t.quantity, 4)}</td>
                        <td className="p-2 text-right font-semibold">
                          {t.fill_price === null
                            ? "—"
                            : t.fill_price.toLocaleString("en-US", {
                                style: "currency",
                                currency: "USD",
                              })}
                        </td>
                        <td
                          className={`p-2 text-right ${
                            t.realized_pnl == null
                              ? "text-gray-500"
                              : t.realized_pnl > 0
                                ? "text-green-400"
                                : "text-red-400"
                          }`}
                        >
                          {t.realized_pnl == null
                            ? "—"
                            : t.realized_pnl.toLocaleString("en-US", {
                                style: "currency",
                                currency: "USD",
                              })}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </>
      ) : (
        <p className="text-gray-500 text-sm">
          No samples yet — start the deployment to begin recording.
        </p>
      )}

      {/* Runs list */}
      <section>
        <h2 className="text-sm font-semibold text-gray-400 uppercase mb-3">Runs</h2>
        <DataTable<AlgorithmRun>
          data={(runs ?? []).slice().sort((a, b) => b.run_number - a.run_number)}
          columns={runsColumns}
          enableSorting
          emptyMessage="No runs yet."
          onRowClick={(r) => {
            const sp = new URLSearchParams(searchParams);
            sp.set("run", r.id);
            setSearchParams(sp);
          }}
        />
      </section>

      <details className="bg-gray-900 border border-gray-800 rounded">
        <summary className="cursor-pointer text-sm font-semibold text-gray-300 px-4 py-2 hover:bg-gray-800">
          Activity
        </summary>
        <div className="p-4">
          <ActivityPanel target={`deployment:${id}` as const} />
        </div>
      </details>
    </div>
  );
}
