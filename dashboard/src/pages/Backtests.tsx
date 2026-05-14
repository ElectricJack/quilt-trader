import { Link, useNavigate } from "react-router-dom";
import { useBacktests } from "../api/hooks";
import { DataTable, ColumnDef } from "../components/DataTable";
import type { BacktestComparison } from "../types/index";

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

// ─── Columns ──────────────────────────────────────────────────────────────────

const columns: ColumnDef<BacktestComparison, unknown>[] = [
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

// ─── Component ────────────────────────────────────────────────────────────────

export function Backtests() {
  const { data: backtests, isLoading } = useBacktests();
  const navigate = useNavigate();

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-white">
        Backtests{" "}
        {!isLoading && (
          <span className="text-gray-400 text-base font-normal">
            ({backtests?.length ?? 0})
          </span>
        )}
      </h1>

      <div className="bg-gray-900 border border-gray-800 rounded overflow-hidden">
        <DataTable<BacktestComparison>
          data={backtests ?? []}
          columns={columns}
          isLoading={isLoading}
          emptyMessage="No backtests found."
          enableSorting
          onRowClick={(bt) => navigate(`/backtests/${bt.id}`)}
        />
      </div>
    </div>
  );
}
