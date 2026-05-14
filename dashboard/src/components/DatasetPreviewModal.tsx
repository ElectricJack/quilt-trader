import { useMemo } from "react";
import { useMarketData } from "../api/hooks";
import type { MarketDataBar } from "../types";
import type { ColumnDef } from "./DataTable";
import { DataTable } from "./DataTable";
import { PriceChart } from "./PriceChart";

// ─── Format helpers ────────────────────────────────────────────────────────────

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  });
}

function formatPrice(n: number): string {
  return `$${n.toFixed(2)}`;
}

function formatVolume(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

// ─── Table columns ─────────────────────────────────────────────────────────────

const barColumns: ColumnDef<MarketDataBar, unknown>[] = [
  {
    id: "timestamp",
    header: "Time",
    accessorKey: "timestamp",
    cell: ({ row }) => (
      <span className="tabular-nums text-gray-300">{formatDate(row.original.timestamp)}</span>
    ),
  },
  {
    id: "open",
    header: () => <span className="block text-right w-full">Open</span>,
    accessorKey: "open",
    cell: ({ row }) => (
      <span className="tabular-nums text-right block">{formatPrice(row.original.open)}</span>
    ),
  },
  {
    id: "high",
    header: () => <span className="block text-right w-full">High</span>,
    accessorKey: "high",
    cell: ({ row }) => (
      <span className="tabular-nums text-right block text-green-400">{formatPrice(row.original.high)}</span>
    ),
  },
  {
    id: "low",
    header: () => <span className="block text-right w-full">Low</span>,
    accessorKey: "low",
    cell: ({ row }) => (
      <span className="tabular-nums text-right block text-red-400">{formatPrice(row.original.low)}</span>
    ),
  },
  {
    id: "close",
    header: () => <span className="block text-right w-full">Close</span>,
    accessorKey: "close",
    cell: ({ row }) => (
      <span className="tabular-nums text-right block font-medium">{formatPrice(row.original.close)}</span>
    ),
  },
  {
    id: "volume",
    header: () => <span className="block text-right w-full">Volume</span>,
    accessorKey: "volume",
    cell: ({ row }) => (
      <span className="tabular-nums text-right block text-gray-400">{formatVolume(row.original.volume)}</span>
    ),
  },
];

// ─── Props ─────────────────────────────────────────────────────────────────────

interface DatasetPreviewModalProps {
  open: boolean;
  onClose: () => void;
  provider: string | null;
  symbol: string | null;
  timeframe: string | null;
}

// ─── Component ─────────────────────────────────────────────────────────────────

export function DatasetPreviewModal({
  open,
  onClose,
  provider,
  symbol,
  timeframe,
}: DatasetPreviewModalProps) {
  const { data, isLoading } = useMarketData(
    open ? provider : null,
    open ? symbol : null,
    open ? timeframe : null
  );

  const bars = data?.data ?? [];

  const stats = useMemo(() => {
    if (bars.length === 0) return null;
    const closes = bars.map((b) => b.close);
    const minClose = Math.min(...closes);
    const maxClose = Math.max(...closes);
    const earliest = bars[0].timestamp;
    const latest = bars[bars.length - 1].timestamp;
    return { count: bars.length, earliest, latest, minClose, maxClose };
  }, [bars]);

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
      <div className="relative z-10 bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-4xl mx-auto flex flex-col max-h-[90vh]">

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800 shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <h2 className="text-xl font-bold text-white truncate">
              {symbol ?? "—"} · {timeframe ?? "—"}
            </h2>
            {provider && (
              <span className="bg-indigo-900 text-indigo-300 text-xs px-2 py-0.5 rounded capitalize shrink-0">
                {provider}
              </span>
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

        {/* Scrollable content */}
        <div className="overflow-y-auto flex-1 px-6 py-4 space-y-4">
          {isLoading ? (
            <div className="flex items-center justify-center py-20">
              <span className="text-gray-400 text-sm animate-pulse">Loading…</span>
            </div>
          ) : (
            <>
              {/* Stats row */}
              {stats && (
                <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-gray-300 bg-gray-800/50 rounded-lg px-4 py-3">
                  <span>
                    <span className="text-gray-500">Bars: </span>
                    <strong className="tabular-nums">{stats.count.toLocaleString()}</strong>
                  </span>
                  <span>
                    <span className="text-gray-500">From: </span>
                    <strong>{formatDate(stats.earliest)}</strong>
                  </span>
                  <span>
                    <span className="text-gray-500">To: </span>
                    <strong>{formatDate(stats.latest)}</strong>
                  </span>
                  <span>
                    <span className="text-gray-500">Close: </span>
                    <strong className="tabular-nums">
                      {formatPrice(stats.minClose)} – {formatPrice(stats.maxClose)}
                    </strong>
                  </span>
                </div>
              )}

              {/* Price chart */}
              {bars.length > 0 && <PriceChart bars={bars} height={280} />}

              {/* Data table */}
              {bars.length > 0 && (
                <div className="bg-gray-950 border border-gray-800 rounded-lg overflow-hidden">
                  <DataTable
                    data={bars}
                    columns={barColumns}
                    enablePagination
                    pageSize={25}
                    emptyMessage="No bars available."
                  />
                </div>
              )}

              {!isLoading && bars.length === 0 && (
                <p className="text-gray-500 text-sm text-center py-10">No data available for this dataset.</p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
