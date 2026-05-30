import { useState, useMemo, useEffect } from "react";
import { X, Search, RotateCcw } from "lucide-react";
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

function toIsoDay(value: string | null): string {
  if (!value) return "";
  // Inputs may arrive as "2024-01-15", "2024-01-15 18:06:25", or ISO with tz.
  // Trim to the first 10 chars to normalize to YYYY-MM-DD for <input type=date>.
  return value.slice(0, 10);
}

export function DatasetPreviewModal({ open, onClose, datasetName, symbolKeyed }: Props) {
  const [symbol, setSymbol] = useState<string | undefined>(undefined);
  const [start, setStart] = useState<string>("");
  const [end, setEnd] = useState<string>("");
  const [q, setQ] = useState<string>("");
  const [page, setPage] = useState(0);
  const pageSize = 50;

  const coverage = useDatasetCoverageDetail(datasetName, open);

  // Pre-populate start/end from the dataset's actual coverage on first
  // open (or first symbol selection for symbol-keyed datasets). Users can
  // then narrow with the date controls or clear via Reset.
  const coverageEntry = useMemo(() => {
    const syms = coverage.data?.symbols ?? [];
    if (symbolKeyed) {
      return syms.find(s => s.symbol === symbol) ?? null;
    }
    return syms[0] ?? null;
  }, [coverage.data, symbol, symbolKeyed]);

  const coverageStart = toIsoDay(coverageEntry?.event_date_min ?? null);
  const coverageEnd = toIsoDay(coverageEntry?.event_date_max ?? null);

  useEffect(() => {
    // Hydrate fields once we have a coverage entry and the user hasn't typed.
    if (!start && coverageStart) setStart(coverageStart);
    if (!end && coverageEnd) setEnd(coverageEnd);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coverageStart, coverageEnd]);

  const rows = usePagedDatasetRows(open ? datasetName : null, {
    symbol,
    start: start || undefined,
    end: end || undefined,
    q: q.trim() || undefined,
    page,
    pageSize,
  });

  const columns = useMemo<ColumnDef<Record<string, unknown>>[]>(() => {
    const r0 = rows.data?.rows[0];
    if (!r0) return [];
    return Object.keys(r0).map(k => ({ header: k, accessorKey: k }));
  }, [rows.data]);

  if (!open) return null;

  const resetFilters = () => {
    setStart(coverageStart);
    setEnd(coverageEnd);
    setQ("");
    setPage(0);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/70"
        onClick={onClose}
        aria-hidden="true"
      />

      <div className="relative z-10 bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-6xl mx-auto flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800 shrink-0">
          <div className="flex items-center gap-4 min-w-0">
            <h2 className="text-xl font-bold text-white truncate">
              {datasetName}
            </h2>
            {coverageEntry && (
              <span className="text-xs text-gray-500 font-mono whitespace-nowrap">
                {coverageEntry.row_count.toLocaleString()} rows · {coverageStart} → {coverageEnd}
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white ml-4 shrink-0 transition-colors"
            aria-label="Close"
          >
            <X size={20} />
          </button>
        </div>

        {/* Filter bar */}
        <div className="px-6 py-3 border-b border-gray-800 shrink-0 space-y-3 text-sm">
          {/* Row 1: symbol (if any) + search */}
          <div className="flex items-center gap-3">
            {symbolKeyed && (
              <select
                className="bg-gray-800 border border-gray-700 text-gray-200 rounded px-2 py-1 min-w-[12rem]"
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
            <div className="relative flex-1">
              <Search
                size={14}
                className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-500 pointer-events-none"
              />
              <input
                type="text"
                value={q}
                onChange={e => { setQ(e.target.value); setPage(0); }}
                placeholder="Search any column (case-insensitive substring)…"
                className="bg-gray-800 border border-gray-700 text-gray-200 rounded px-7 py-1 w-full"
              />
            </div>
            <button
              onClick={resetFilters}
              className="text-gray-400 hover:text-white px-2 py-1 border border-gray-700 rounded text-xs flex items-center gap-1 transition-colors"
              title="Reset start/end to full coverage range and clear search"
            >
              <RotateCcw size={12} /> Reset
            </button>
          </div>

          {/* Row 2: date range */}
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-gray-400">
              <span className="shrink-0">event_date ≥</span>
              <input
                type="date"
                value={start}
                min={coverageStart || undefined}
                max={coverageEnd || undefined}
                onChange={e => { setStart(e.target.value); setPage(0); }}
                className="bg-gray-800 border border-gray-700 text-gray-200 rounded px-2 py-1"
              />
            </label>
            <label className="flex items-center gap-2 text-gray-400">
              <span className="shrink-0">event_date ≤</span>
              <input
                type="date"
                value={end}
                min={coverageStart || undefined}
                max={coverageEnd || undefined}
                onChange={e => { setEnd(e.target.value); setPage(0); }}
                className="bg-gray-800 border border-gray-700 text-gray-200 rounded px-2 py-1"
              />
            </label>
            <span className="text-xs text-gray-500">
              Dates default to the dataset's full coverage range.
            </span>
          </div>
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
                <span>{rows.data.total.toLocaleString()} matching rows</span>
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
