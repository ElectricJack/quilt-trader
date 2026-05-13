import { useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { z } from "zod";
import { ChevronLeft } from "lucide-react";
import {
  useInstance,
  useAlgorithm,
  useAccount,
  useWorker,
  useRuns,
  useUpdateInstance,
  useDeleteInstance,
} from "../api/hooks";
import { wsManager } from "../api/websocket";
import { StatusBadge } from "../components/StatusBadge";
import { MetricsCard } from "../components/MetricsCard";
import { EquityCurve } from "../components/EquityCurve";
import { DataTable, ColumnDef } from "../components/DataTable";
import { FormModal } from "../components/FormModal";
import { FormField } from "../components/FormField";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useUIStore } from "../stores/ui";
import type { AlgorithmRun } from "../types";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmtDollar(val: number | null | undefined): string {
  if (val == null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  }).format(val);
}

function fmtDate(val: string | null | undefined): string {
  if (!val) return "—";
  return new Date(val).toLocaleString();
}

function humanizeKey(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

// ─── Edit Config schema ───────────────────────────────────────────────────────

const configSchema = z.object({
  config_values: z.string(),
});
type ConfigForm = z.infer<typeof configSchema>;

// ─── Runs table columns ───────────────────────────────────────────────────────

const runsColumns: ColumnDef<AlgorithmRun, unknown>[] = [
  {
    id: "run_number",
    accessorKey: "run_number",
    header: "Run #",
    cell: ({ row }) => (
      <span className="font-mono text-gray-200">#{row.original.run_number}</span>
    ),
  },
  {
    id: "status",
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <StatusBadge status={row.original.status} />,
  },
  {
    id: "net_pnl",
    accessorKey: "net_pnl",
    header: "P&L",
    cell: ({ row }) => {
      const pnl = row.original.net_pnl;
      const color =
        pnl == null ? "text-gray-400" : pnl >= 0 ? "text-green-400" : "text-red-400";
      return <span className={color}>{fmtDollar(pnl)}</span>;
    },
  },
  {
    id: "trade_count",
    accessorKey: "trade_count",
    header: "Trades",
    cell: ({ row }) => (
      <span className="text-gray-300">{row.original.trade_count}</span>
    ),
  },
  {
    id: "started_at",
    accessorKey: "started_at",
    header: "Started",
    cell: ({ row }) => (
      <span className="text-xs text-gray-400">{fmtDate(row.original.started_at)}</span>
    ),
  },
  {
    id: "stopped_at",
    accessorKey: "stopped_at",
    header: "Ended",
    cell: ({ row }) => (
      <span className="text-xs text-gray-400">{fmtDate(row.original.stopped_at)}</span>
    ),
  },
];

// ─── Component ────────────────────────────────────────────────────────────────

export function InstanceDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const addAlert = useUIStore((s) => s.addAlert);

  const { data: instance, isLoading: loadingInstance } = useInstance(id ?? "");
  const { data: algorithm } = useAlgorithm(instance?.algorithm_id ?? "");
  const { data: account } = useAccount(instance?.account_id ?? "");
  const { data: worker } = useWorker(instance?.worker_id ?? "");
  const { data: runs, isLoading: loadingRuns } = useRuns(id ?? "");

  const { mutateAsync: updateInstance, isPending: isUpdating } = useUpdateInstance();
  const { mutateAsync: deleteInstance } = useDeleteInstance();

  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  if (loadingInstance) {
    return <p className="text-gray-400 text-sm">Loading…</p>;
  }

  if (!instance) {
    return <p className="text-gray-400 text-sm">Instance not found.</p>;
  }

  // ── Equity curve from active run ──────────────────────────────────────────
  const activeRun =
    instance.active_run_id && runs
      ? runs.find((r) => r.id === instance.active_run_id) ?? null
      : null;

  const equityCurveData =
    activeRun?.equity_curve?.map((p) => ({ timestamp: p.date, equity: p.equity })) ?? null;

  // ── Lifetime metrics rendering ────────────────────────────────────────────
  const metrics = instance.lifetime_metrics ?? {};
  const knownMetricKeys = ["total_pnl", "win_rate", "sharpe_ratio", "max_drawdown", "trade_count"];
  const unknownEntries = Object.entries(metrics).filter(
    ([k]) => !knownMetricKeys.includes(k)
  );

  // ── Handlers ──────────────────────────────────────────────────────────────
  const canStart =
    instance.status === "stopped" || instance.status === "error";
  const isRunning = instance.status === "running";

  function handleStart() {
    wsManager.send({ type: "start_instance", instance_id: id });
  }

  function handleStop() {
    wsManager.send({ type: "stop_instance", instance_id: id });
  }

  async function handleEditConfig(data: ConfigForm) {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(data.config_values) as Record<string, unknown>;
    } catch {
      addAlert({ message: "Invalid JSON in config values.", severity: "error" });
      return;
    }
    await updateInstance({ id: id ?? "", body: { config_values: parsed } });
    addAlert({ message: "Config updated.", severity: "success" });
    setEditOpen(false);
  }

  async function handleDelete() {
    // instance is guaranteed non-null here (guarded above)
    // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
    const algoId = instance!.algorithm_id;
    await deleteInstance(id ?? "");
    navigate(`/algorithms/${algoId}`);
  }

  return (
    <div className="space-y-6">
      {/* ── Header ── */}
      <div className="flex items-center gap-4 flex-wrap">
        <Link
          to={`/algorithms/${instance.algorithm_id}`}
          className="flex items-center gap-1 text-sm text-gray-400 hover:text-gray-200 transition-colors"
        >
          <ChevronLeft size={16} />
          Algorithm
        </Link>

        <div className="flex items-center gap-3 flex-1">
          <h1 className="text-2xl font-bold text-white">Instance</h1>
          <StatusBadge status={instance.status} />
        </div>

        <div className="flex items-center gap-2">
          {canStart && (
            <button
              onClick={handleStart}
              className="px-3 py-1.5 rounded text-sm font-medium text-white bg-green-600 hover:bg-green-500 transition-colors"
            >
              Start
            </button>
          )}
          {isRunning && (
            <button
              onClick={handleStop}
              className="px-3 py-1.5 rounded text-sm font-medium text-white bg-red-600 hover:bg-red-500 transition-colors"
            >
              Stop
            </button>
          )}
          <button
            onClick={() => setEditOpen(true)}
            className="px-3 py-1.5 rounded text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 transition-colors"
          >
            Edit Config
          </button>
          <button
            onClick={() => setDeleteOpen(true)}
            className="px-3 py-1.5 rounded text-sm font-medium text-white bg-red-700 hover:bg-red-600 transition-colors"
          >
            Delete
          </button>
        </div>
      </div>

      {/* ── Details ── */}
      <div className="bg-gray-900 border border-gray-800 rounded p-4 space-y-3">
        <h2 className="text-lg font-semibold text-gray-200">Details</h2>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-xs text-gray-500 uppercase">Algorithm</span>
            <p className="text-gray-200 mt-0.5">
              {algorithm ? (
                <Link
                  to={`/algorithms/${instance.algorithm_id}`}
                  className="text-indigo-400 hover:underline"
                >
                  {algorithm.name}
                  {algorithm.version ? ` v${algorithm.version}` : ""}
                </Link>
              ) : (
                <span className="font-mono text-xs">{instance.algorithm_id}</span>
              )}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Account</span>
            <p className="text-gray-200 mt-0.5">
              {account ? (
                <Link
                  to={`/accounts/${instance.account_id}`}
                  className="text-indigo-400 hover:underline"
                >
                  {account.name}
                </Link>
              ) : (
                <span className="font-mono text-xs">{instance.account_id}</span>
              )}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Worker</span>
            <p className="text-gray-200 mt-0.5">
              {worker ? (
                <Link
                  to={`/workers/${instance.worker_id}`}
                  className="text-indigo-400 hover:underline"
                >
                  {worker.name}
                </Link>
              ) : (
                <span className="font-mono text-xs">{instance.worker_id}</span>
              )}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Active Run</span>
            <p className="text-gray-200 mt-0.5">
              {instance.active_run_id ? (
                <Link
                  to={`/runs/${instance.active_run_id}`}
                  className="text-indigo-400 hover:underline font-mono text-xs"
                >
                  {instance.active_run_id.slice(0, 8)}…
                </Link>
              ) : (
                "—"
              )}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">State Stale</span>
            <p className="text-gray-200 mt-0.5">{instance.state_stale ? "Yes" : "No"}</p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Created</span>
            <p className="text-gray-200 mt-0.5 text-xs">
              {new Date(instance.created_at).toLocaleString()}
            </p>
          </div>
        </div>
      </div>

      {/* ── Lifetime Metrics ── */}
      {Object.keys(metrics).length > 0 && (
        <section>
          <h2 className="text-lg font-semibold text-gray-200 mb-3">Lifetime Metrics</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {"total_pnl" in metrics && typeof metrics.total_pnl === "number" && (
              <MetricsCard
                label="Total P&L"
                value={fmtDollar(metrics.total_pnl)}
                change={metrics.total_pnl}
              />
            )}
            {"win_rate" in metrics && typeof metrics.win_rate === "number" && (
              <MetricsCard
                label="Win Rate"
                value={`${(metrics.win_rate * 100).toFixed(1)}%`}
              />
            )}
            {"sharpe_ratio" in metrics && typeof metrics.sharpe_ratio === "number" && (
              <MetricsCard
                label="Sharpe Ratio"
                value={metrics.sharpe_ratio.toFixed(2)}
              />
            )}
            {"max_drawdown" in metrics && typeof metrics.max_drawdown === "number" && (
              <MetricsCard
                label="Max Drawdown"
                value={`${(metrics.max_drawdown * 100).toFixed(1)}%`}
              />
            )}
            {"trade_count" in metrics && typeof metrics.trade_count === "number" && (
              <MetricsCard
                label="Trade Count"
                value={metrics.trade_count}
              />
            )}
            {unknownEntries.map(([key, val]) => (
              <MetricsCard
                key={key}
                label={humanizeKey(key)}
                value={typeof val === "number" ? val.toLocaleString() : String(val)}
              />
            ))}
          </div>
        </section>
      )}

      {/* ── Equity Curve ── */}
      {equityCurveData && equityCurveData.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold text-gray-200 mb-3">Equity Curve</h2>
          <div className="bg-gray-900 border border-gray-800 rounded p-4">
            <EquityCurve data={equityCurveData} height={280} />
          </div>
        </section>
      )}

      {/* ── Runs ── */}
      <section>
        <h2 className="text-lg font-semibold text-gray-200 mb-3">Runs</h2>
        <div className="bg-gray-900 border border-gray-800 rounded overflow-hidden">
          <DataTable<AlgorithmRun>
            data={runs ?? []}
            columns={runsColumns}
            isLoading={loadingRuns}
            emptyMessage="No runs found."
            enableSorting
            onRowClick={(run) => navigate(`/runs/${run.id}`)}
          />
        </div>
      </section>

      {/* ── Edit Config Modal ── */}
      <FormModal
        open={editOpen}
        onClose={() => setEditOpen(false)}
        title="Edit Config"
        schema={configSchema}
        defaultValues={{
          config_values: JSON.stringify(instance.config_values ?? {}, null, 2),
        }}
        onSubmit={handleEditConfig}
        submitLabel="Update Config"
        isSubmitting={isUpdating}
      >
        {(form) => (
          <FormField
            label="Config Values (JSON)"
            error={form.formState.errors.config_values?.message}
          >
            <textarea
              {...form.register("config_values")}
              rows={10}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-indigo-500 resize-y"
            />
          </FormField>
        )}
      </FormModal>

      {/* ── Delete Confirm ── */}
      <ConfirmDialog
        open={deleteOpen}
        title="Delete Instance"
        message="Are you sure you want to delete this instance? This action cannot be undone."
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setDeleteOpen(false)}
      />
    </div>
  );
}
