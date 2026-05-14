import { useMemo } from "react";
import { useCustomData } from "../api/hooks";
import { DataTable, type ColumnDef } from "./DataTable";

interface Props {
  open: boolean;
  onClose: () => void;
  sourceName: string | null;
  description?: string | null;
  lastUpdated?: string | null;
}

export function CustomDataPreviewModal({
  open,
  onClose,
  sourceName,
  description,
  lastUpdated,
}: Props) {
  const { data, isLoading, error } = useCustomData(open ? sourceName : null);
  const rows = useMemo(() => data?.data ?? [], [data]);

  const columns: ColumnDef<Record<string, unknown>, unknown>[] = useMemo(() => {
    if (rows.length === 0) return [];
    const keys = Object.keys(rows[0]);
    return keys.map((k) => ({
      id: k,
      header: k,
      accessorFn: (row) => row[k],
      cell: ({ row }) => {
        const v = row.original[k];
        if (v == null) return <span className="text-gray-600">—</span>;
        if (typeof v === "object") return <span className="text-xs text-gray-400">{JSON.stringify(v)}</span>;
        return <span className="text-gray-200">{String(v)}</span>;
      },
    }));
  }, [rows]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70" onClick={onClose} aria-hidden="true" />

      <div className="relative z-10 bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-5xl mx-auto flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800 shrink-0">
          <div className="min-w-0">
            <h2 className="text-xl font-bold text-white truncate">{sourceName ?? "—"}</h2>
            {description && (
              <p className="text-xs text-gray-400 mt-0.5 truncate">{description}</p>
            )}
            {lastUpdated && (
              <p className="text-[11px] text-gray-500 mt-0.5">
                Last updated: {new Date(lastUpdated).toLocaleString()}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white ml-4 shrink-0 transition-colors"
            aria-label="Close"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="overflow-y-auto flex-1 px-6 py-4 space-y-3">
          {isLoading ? (
            <div className="flex items-center justify-center py-20">
              <span className="text-gray-400 text-sm animate-pulse">Loading…</span>
            </div>
          ) : error ? (
            <p className="text-red-400 text-sm">Couldn't load data: {(error as Error).message}</p>
          ) : rows.length === 0 ? (
            <p className="text-gray-500 text-sm text-center py-10">No rows in this dataset.</p>
          ) : (
            <>
              <div className="text-xs text-gray-400">
                {rows.length} row{rows.length === 1 ? "" : "s"} ·{" "}
                {columns.length} column{columns.length === 1 ? "" : "s"}
              </div>
              <div className="bg-gray-950 border border-gray-800 rounded-lg overflow-hidden">
                <DataTable
                  data={rows}
                  columns={columns}
                  enablePagination
                  pageSize={25}
                  emptyMessage="No rows."
                />
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
