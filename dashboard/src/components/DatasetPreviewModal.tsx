import { useState, useMemo } from "react";
import { X } from "lucide-react";
import { DataTable } from "./DataTable";
import { usePagedDatasetRows } from "../hooks/usePagedDatasetRows";
import { useDatasetCoverageDetail } from "../hooks/useDatasetCoverage";
import type { ColumnDef } from "@tanstack/react-table";

interface Props {
  open: boolean;
  onClose: () => void;
  datasetName: string;
  symbolKeyed: boolean;
}

export function DatasetPreviewModal({ open, onClose, datasetName, symbolKeyed }: Props) {
  const [symbol, setSymbol] = useState<string | undefined>(undefined);
  const [asOf, setAsOf] = useState<string>(new Date().toISOString().slice(0, 10));
  const [start, setStart] = useState<string>("");
  const [end, setEnd] = useState<string>("");
  const [page, setPage] = useState(0);
  const pageSize = 50;

  const coverage = useDatasetCoverageDetail(datasetName, open);
  const rows = usePagedDatasetRows(open ? datasetName : null, {
    symbol, as_of: asOf, start: start || undefined, end: end || undefined, page, pageSize,
  });

  const columns = useMemo<ColumnDef<Record<string, unknown>>[]>(() => {
    const r0 = rows.data?.rows[0];
    if (!r0) return [];
    return Object.keys(r0).map(k => ({ header: k, accessorKey: k }));
  }, [rows.data]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/70"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Modal */}
      <div className="relative z-10 bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-6xl mx-auto flex flex-col max-h-[90vh]">

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800 shrink-0">
          <h2 className="text-xl font-bold text-white truncate">{datasetName}</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white ml-4 shrink-0 transition-colors"
            aria-label="Close"
          >
            <X size={20} />
          </button>
        </div>

        {/* Filter bar */}
        <div className="px-6 py-3 border-b border-gray-800 shrink-0 grid grid-cols-4 gap-3 text-sm">
          {symbolKeyed && (
            <select
              className="bg-gray-800 border border-gray-700 text-gray-200 rounded px-2 py-1"
              value={symbol || ""}
              onChange={e => { setSymbol(e.target.value || undefined); setPage(0); }}
            >
              <option value="">— select symbol —</option>
              {coverage.data?.symbols.map(s =>
                <option key={s.symbol} value={s.symbol!}>
                  {s.symbol} ({s.row_count.toLocaleString()})
                </option>
              )}
            </select>
          )}
          <label className="flex items-center gap-2 text-gray-400">
            <span className="shrink-0">as_of</span>
            <input
              type="date"
              value={asOf}
              onChange={e => { setAsOf(e.target.value); setPage(0); }}
              className="bg-gray-800 border border-gray-700 text-gray-200 rounded px-2 py-1 w-full"
            />
          </label>
          <label className="flex items-center gap-2 text-gray-400">
            <span className="shrink-0">start</span>
            <input
              type="date"
              value={start}
              onChange={e => { setStart(e.target.value); setPage(0); }}
              className="bg-gray-800 border border-gray-700 text-gray-200 rounded px-2 py-1 w-full"
            />
          </label>
          <label className="flex items-center gap-2 text-gray-400">
            <span className="shrink-0">end</span>
            <input
              type="date"
              value={end}
              onChange={e => { setEnd(e.target.value); setPage(0); }}
              className="bg-gray-800 border border-gray-700 text-gray-200 rounded px-2 py-1 w-full"
            />
          </label>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-auto px-6 py-4">
          {rows.isLoading && (
            <div className="flex items-center justify-center py-20">
              <span className="text-gray-400 text-sm animate-pulse">Loading…</span>
            </div>
          )}
          {rows.error && (
            <div className="text-red-400 text-sm py-4">Error loading rows</div>
          )}
          {rows.data && (
            <>
              <div className="bg-gray-950 border border-gray-800 rounded-lg overflow-hidden">
                <DataTable
                  data={rows.data.rows}
                  columns={columns}
                  enableSorting
                  emptyMessage={
                    symbolKeyed && !symbol
                      ? "Select a symbol to preview rows"
                      : "No rows match the current filters"
                  }
                />
              </div>
              <div className="flex items-center justify-between mt-3 text-sm text-gray-400">
                <span>{rows.data.total.toLocaleString()} total rows</span>
                <div className="flex items-center gap-2">
                  <button
                    disabled={page === 0}
                    onClick={() => setPage(page - 1)}
                    className="px-2 py-1 border border-gray-700 rounded disabled:opacity-40 hover:bg-gray-800 transition-colors"
                  >
                    Prev
                  </button>
                  <span>page {page + 1}</span>
                  <button
                    disabled={(page + 1) * pageSize >= rows.data.total}
                    onClick={() => setPage(page + 1)}
                    className="px-2 py-1 border border-gray-700 rounded disabled:opacity-40 hover:bg-gray-800 transition-colors"
                  >
                    Next
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
