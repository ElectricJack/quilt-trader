import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Trash2 } from "lucide-react";
import {
  useBacktestRuns,
  useBacktests,
  useAlgorithms,
  useDeleteBacktestRun,
} from "../api/hooks";
import { DataTable, ColumnDef } from "../components/DataTable";
import { StatusBadge } from "../components/StatusBadge";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useUIStore } from "../stores/ui";
import type { BacktestComparison } from "../types/index";
import type { BacktestRunRecord } from "../api/client";

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
  return new Date(val).toLocaleDateString();
}

function fmtPct(val: number | null): string {
  if (val === null || val === undefined) return "—";
  return (val * 100).toFixed(2) + "%";
}

// ─── Comparisons Columns ──────────────────────────────────────────────────────

const comparisonColumns: ColumnDef<BacktestComparison, unknown>[] = [
  {
    id: "id",
    accessorKey: "id",
    header: "ID",
    cell: ({ row }) => (
      <Link
        to={`/backtests/${row.original.id}`}
        className="text-indigo-400 hover:underline font-mono text-xs"
        onClick={(e) => e.stopPropagation()}
      >
        {row.original.id.slice(0, 8)}…
      </Link>
    ),
  },
  {
    id: "instance_id",
    accessorKey: "instance_id",
    header: "Instance",
    cell: ({ row }) => (
      <Link
        to={`/instances/${row.original.instance_id}`}
        className="text-indigo-400 hover:underline font-mono text-xs"
        onClick={(e) => e.stopPropagation()}
      >
        {row.original.instance_id.slice(0, 8)}…
      </Link>
    ),
  },
  {
    id: "time_range",
    header: "Time Range",
    cell: ({ row }) => {
      const bt = row.original;
      return (
        <span className="text-xs text-gray-400">
          {bt.time_range_start ? new Date(bt.time_range_start).toLocaleDateString() : "—"}
          {" – "}
          {bt.time_range_end ? new Date(bt.time_range_end).toLocaleDateString() : "—"}
        </span>
      );
    },
  },
  {
    id: "total_ticks",
    accessorKey: "total_ticks",
    header: "Total Ticks",
    cell: ({ row }) => (
      <span className="text-gray-300">{row.original.total_ticks.toLocaleString()}</span>
    ),
  },
  {
    id: "match_percentage",
    accessorKey: "match_percentage",
    header: "Match %",
    cell: ({ row }) => {
      const pct = row.original.match_percentage;
      return (
        <span className="inline-flex items-center gap-2">
          <span className="bg-gray-700 rounded-full h-2 w-24 inline-block overflow-hidden align-middle">
            <span
              className={`block h-full rounded-full ${matchBarColor(pct)}`}
              style={{ width: `${Math.min(pct, 100)}%` }}
            />
          </span>
          <span className={`text-xs font-medium ${matchTextColor(pct)}`}>
            {pct.toFixed(1)}%
          </span>
        </span>
      );
    },
  },
  {
    id: "created_at",
    accessorKey: "created_at",
    header: "Created",
    cell: ({ row }) => (
      <span className="text-xs text-gray-400">{fmtDate(row.original.created_at)}</span>
    ),
  },
];

// ─── Sub-components ───────────────────────────────────────────────────────────

interface RunsTabProps {
  algoById: Map<string, string>;
  navigate: ReturnType<typeof useNavigate>;
}

function RunsTab({ algoById, navigate }: RunsTabProps) {
  const { data: runs = [], isLoading } = useBacktestRuns();
  const del = useDeleteBacktestRun();
  const addAlert = useUIStore((s) => s.addAlert);
  const [deleteTarget, setDeleteTarget] = useState<BacktestRunRecord | null>(
    null,
  );

  async function handleDelete() {
    if (!deleteTarget) return;
    const target = deleteTarget;
    setDeleteTarget(null);
    try {
      await del.mutateAsync(target.id);
      addAlert({ message: "Deleted backtest run.", severity: "success" });
    } catch {
      addAlert({
        message: "Failed to delete backtest run.",
        severity: "error",
      });
    }
  }

  const runsColumns: ColumnDef<BacktestRunRecord, unknown>[] = [
    {
      id: "created_at",
      accessorKey: "created_at",
      header: "Created",
      cell: ({ row }) => (
        <span className="text-xs text-gray-400">{fmtDate(row.original.created_at)}</span>
      ),
    },
    {
      id: "algorithm",
      header: "Algorithm",
      cell: ({ row }) => {
        const name = algoById.get(row.original.algorithm_id);
        return (
          <span className="text-sm text-gray-300">
            {name ?? row.original.algorithm_id.slice(0, 8) + "…"}
          </span>
        );
      },
    },
    {
      id: "status",
      accessorKey: "status",
      header: "Status",
      cell: ({ row }) => <StatusBadge status={row.original.status} />,
    },
    {
      id: "date_range",
      header: "Date Range",
      cell: ({ row }) => {
        const r = row.original;
        return (
          <span className="text-xs text-gray-400">
            {fmtDate(r.date_range_start)}
            {" – "}
            {fmtDate(r.date_range_end)}
          </span>
        );
      },
    },
    {
      id: "total_return",
      accessorKey: "total_return",
      header: "Total Return",
      cell: ({ row }) => {
        const val = row.original.total_return;
        if (val === null || val === undefined) {
          return <span className="text-xs text-gray-500">—</span>;
        }
        const colorClass = val > 0 ? "text-green-400" : val < 0 ? "text-red-400" : "text-gray-400";
        return <span className={`text-sm font-medium ${colorClass}`}>{fmtPct(val)}</span>;
      },
    },
    {
      id: "sharpe_ratio",
      accessorKey: "sharpe_ratio",
      header: "Sharpe",
      cell: ({ row }) => {
        const val = row.original.sharpe_ratio;
        if (val === null || val === undefined) {
          return <span className="text-xs text-gray-500">—</span>;
        }
        return <span className="text-sm text-gray-300">{val.toFixed(2)}</span>;
      },
    },
    {
      id: "trade_count",
      accessorKey: "trade_count",
      header: "Trades",
      cell: ({ row }) => {
        const val = row.original.trade_count;
        return (
          <span className="text-sm text-gray-300">
            {val !== null && val !== undefined ? val.toLocaleString() : "—"}
          </span>
        );
      },
    },
    {
      id: "actions",
      header: "",
      cell: ({ row }) => (
        <button
          onClick={(e) => {
            e.stopPropagation();
            setDeleteTarget(row.original);
          }}
          aria-label="Delete backtest run"
          title="Delete backtest run"
          className="p-1 rounded text-gray-500 hover:text-red-400 hover:bg-gray-800 transition-colors"
        >
          <Trash2 className="w-4 h-4" />
        </button>
      ),
    },
  ];

  return (
    <>
      <div className="bg-gray-900 border border-gray-800 rounded overflow-hidden">
        <DataTable<BacktestRunRecord>
          data={runs}
          columns={runsColumns}
          isLoading={isLoading}
          emptyMessage="No backtest runs found."
          enableSorting
          onRowClick={(run) => navigate(`/backtest-runs/${run.id}`)}
        />
      </div>
      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete backtest run"
        message="Are you sure you want to delete this backtest run? This cannot be undone."
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </>
  );
}

interface ComparisonsTabProps {
  navigate: ReturnType<typeof useNavigate>;
}

function ComparisonsTab({ navigate }: ComparisonsTabProps) {
  const { data: backtests, isLoading } = useBacktests();

  return (
    <div className="bg-gray-900 border border-gray-800 rounded overflow-hidden">
      <DataTable<BacktestComparison>
        data={backtests ?? []}
        columns={comparisonColumns}
        isLoading={isLoading}
        emptyMessage="No backtests found."
        enableSorting
        onRowClick={(bt) => navigate(`/backtests/${bt.id}`)}
      />
    </div>
  );
}

// ─── Component ────────────────────────────────────────────────────────────────

export function Backtests() {
  const [tab, setTab] = useState<"runs" | "comparisons">("runs");
  const navigate = useNavigate();
  const { data: algos = [] } = useAlgorithms();
  const algoById = new Map(algos.map((a) => [a.id, a.name]));

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-white">Backtests</h1>
      <div className="flex gap-2 border-b border-gray-800">
        {(["runs", "comparisons"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-3 py-2 text-sm ${
              tab === t
                ? "border-b-2 border-indigo-500 text-white"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            {t === "runs" ? "Runs" : "Comparisons"}
          </button>
        ))}
      </div>
      {tab === "runs" && <RunsTab algoById={algoById} navigate={navigate} />}
      {tab === "comparisons" && <ComparisonsTab navigate={navigate} />}
    </div>
  );
}
