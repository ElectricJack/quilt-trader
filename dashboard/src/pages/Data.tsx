import { useEffect, useMemo, useState } from "react";
import { z } from "zod";
import { Trash2 } from "lucide-react";
import {
  useAvailableData,
  useDownloads,
  useCreateDownload,
  useCancelDownload,
  useDeleteDownload,
  useClearDownloads,
} from "../api/hooks";
import { FormModal } from "../components/FormModal";
import { FormField } from "../components/FormField";
import { DataTable, type ColumnDef } from "../components/DataTable";
import { StatusBadge } from "../components/StatusBadge";
import { DatasetPreviewModal } from "../components/DatasetPreviewModal";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useUIStore } from "../stores/ui";
import type { MarketDataDownload, AvailableMarketData } from "../types";

// ─── Schema ───────────────────────────────────────────────────────────────────

const DATA_TYPE_OPTIONS: { value: "bars" | "quotes" | "trades"; label: string; available: boolean }[] = [
  { value: "bars", label: "Bars (OHLCV)", available: true },
  { value: "quotes", label: "Quotes", available: false },
  { value: "trades", label: "Trades", available: false },
];

const DATA_TYPES = ["bars", "quotes", "trades"] as const;
type DataType = (typeof DATA_TYPES)[number];

const downloadSchema = z.object({
  symbols: z.string().min(1, "Required"),
  date_range_start: z.string().min(1, "Required"),
  date_range_end: z.string().min(1, "Required"),
  provider: z.string().min(1, "Required"),
  data_types: z
    .array(z.enum(DATA_TYPES))
    .min(1, "Select at least one data type"),
  timeframe: z.string().min(1, "Required"),
});

type DownloadFormValues = z.infer<typeof downloadSchema>;

// ─── Helpers ──────────────────────────────────────────────────────────────────

const INPUT_CLS =
  "bg-gray-800 border border-gray-700 text-gray-100 rounded px-3 py-2 text-sm w-full";

function formatDate(dateStr: string | null): string {
  if (!dateStr) return "—";
  return new Date(dateStr).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

const ACTIVE_STATUSES = new Set(["pending", "running"]);

// ─── History columns ──────────────────────────────────────────────────────────

function makeHistoryColumns(
  setRowToDelete: (row: MarketDataDownload | null) => void
): ColumnDef<MarketDataDownload, unknown>[] {
  return [
    {
      id: "symbols",
      header: "Symbols",
      accessorFn: (row) => row.symbols.join(", "),
    },
    {
      id: "provider",
      header: "Provider",
      accessorKey: "provider",
    },
    {
      id: "data_type",
      header: "Type",
      accessorKey: "data_type",
    },
    {
      id: "timeframe",
      header: "Timeframe",
      accessorKey: "timeframe",
    },
    {
      id: "date_range",
      header: "Date Range",
      accessorFn: (row) => `${row.date_range_start} – ${row.date_range_end}`,
      cell: ({ row }) =>
        `${formatDate(row.original.date_range_start)} – ${formatDate(row.original.date_range_end)}`,
    },
    {
      id: "status",
      header: "Status",
      accessorKey: "status",
      cell: ({ row }) => (
        <div className="flex flex-col gap-0.5">
          <StatusBadge status={row.original.status} />
          {row.original.error_message && (
            <span
              className="text-[10px] text-red-400 truncate max-w-[260px]"
              title={row.original.error_message}
            >
              {row.original.error_message}
            </span>
          )}
        </div>
      ),
    },
    {
      id: "completed_at",
      header: "Completed",
      accessorKey: "completed_at",
      cell: ({ row }) => formatDate(row.original.completed_at),
    },
    {
      id: "actions",
      header: "",
      cell: ({ row }) => (
        <button
          onClick={(e) => {
            e.stopPropagation();
            setRowToDelete(row.original);
          }}
          title="Delete row"
          className="p-1 text-gray-400 hover:text-red-400 transition-colors"
        >
          <Trash2 size={14} />
        </button>
      ),
    },
  ];
}

// ─── Active download card ─────────────────────────────────────────────────────

interface ActiveCardProps {
  download: MarketDataDownload;
  onCancel: (id: string) => void;
  isCancelling: boolean;
}

function ActiveDownloadCard({ download, onCancel, isCancelling }: ActiveCardProps) {
  const pct =
    download.progress_total > 0
      ? Math.round((download.progress_current / download.progress_total) * 100)
      : 0;

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-medium text-gray-200 truncate">
            {download.symbols.join(", ")}
          </span>
          <span className="bg-indigo-900 text-indigo-300 text-xs px-2 py-0.5 rounded capitalize shrink-0">
            {download.provider}
          </span>
          <span className="text-xs text-gray-400 shrink-0">{download.timeframe}</span>
        </div>
        <div className="flex items-center gap-3 ml-4 shrink-0">
          <StatusBadge status={download.status} />
          <button
            onClick={() => onCancel(download.id)}
            disabled={isCancelling}
            className="text-xs text-red-400 hover:text-red-300 disabled:opacity-50 transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>

      {/* Progress bar */}
      <div className="bg-gray-700 rounded-full h-2 mb-1">
        <div
          className="bg-indigo-600 h-2 rounded-full transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-xs text-gray-500">
        {download.progress_current} of {download.progress_total}
      </p>
      {download.progress_message && (
        <p className="text-xs text-gray-400 mt-0.5">{download.progress_message}</p>
      )}

      {/* Error */}
      {download.error_message && (
        <p className="text-xs text-red-400 mt-2">{download.error_message}</p>
      )}
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

export function Data() {
  const [modalOpen, setModalOpen] = useState(false);
  const [preview, setPreview] = useState<{ provider: string; symbol: string; timeframe: string } | null>(null);
  const [rowToDelete, setRowToDelete] = useState<MarketDataDownload | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);

  const { data: available, isLoading: availableLoading } = useAvailableData();
  const { data: downloads, isLoading: downloadsLoading, refetch } = useDownloads();
  const { mutateAsync: createDownload, isPending: isCreating } = useCreateDownload();
  const { mutate: cancelDownload, isPending: isCancelling } = useCancelDownload();
  const deleteDownload = useDeleteDownload();
  const clearDownloads = useClearDownloads();
  const addAlert = useUIStore((s) => s.addAlert);

  const historyColumns = useMemo(() => makeHistoryColumns(setRowToDelete), []);

  const activeDownloads = useMemo(
    () => (downloads ?? []).filter((d) => ACTIVE_STATUSES.has(d.status)),
    [downloads]
  );

  const historyDownloads = useMemo(
    () => (downloads ?? []).filter((d) => !ACTIVE_STATUSES.has(d.status)),
    [downloads]
  );

  // Poll while there are active downloads
  useEffect(() => {
    if (activeDownloads.length === 0) return;
    const id = setInterval(() => void refetch(), 2000);
    return () => clearInterval(id);
  }, [activeDownloads.length, refetch]);

  const grouped = useMemo(() => {
    const out: Record<string, AvailableMarketData[]> = {};
    for (const item of available ?? []) {
      (out[item.provider] ??= []).push(item);
    }
    for (const key of Object.keys(out)) {
      out[key].sort(
        (a, b) =>
          a.symbol.localeCompare(b.symbol) || a.timeframe.localeCompare(b.timeframe)
      );
    }
    return out;
  }, [available]);

  function formatSize(b: number): string {
    if (b < 1024) return `${b} B`;
    if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
    return `${(b / 1024 / 1024).toFixed(2)} MB`;
  }

  async function handleSubmit(values: DownloadFormValues) {
    const symbols = values.symbols
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);

    const results = await Promise.allSettled(
      values.data_types.map((dt) =>
        createDownload({
          symbols,
          date_range_start: values.date_range_start,
          date_range_end: values.date_range_end,
          provider: values.provider,
          data_type: dt,
          timeframe: values.timeframe,
        })
      )
    );
    const successes = results.filter((r) => r.status === "fulfilled").length;
    const failures = results.length - successes;
    setModalOpen(false);
    if (failures === 0) {
      addAlert({
        message: `Queued ${successes} download${successes === 1 ? "" : "s"} (${values.data_types.join(", ")} for ${symbols.join(", ")}).`,
        severity: "success",
      });
    } else {
      const firstError = results.find(
        (r): r is PromiseRejectedResult => r.status === "rejected"
      )?.reason;
      addAlert({
        message: `Queued ${successes}, failed ${failures}. ${
          firstError instanceof Error ? firstError.message : "Check console."
        }`,
        severity: "error",
      });
      if (firstError) console.error("Download submission failures:", results);
    }
  }

  function handleCancel(id: string) {
    cancelDownload(id, {
      onError: (err) =>
        addAlert({
          message: err instanceof Error ? err.message : "Failed to cancel download.",
          severity: "error",
        }),
    });
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Market Data</h1>
        <button
          onClick={() => setModalOpen(true)}
          className="px-4 py-2 rounded text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 transition-colors"
        >
          Download Data
        </button>
      </div>

      {/* Active Downloads */}
      {(activeDownloads.length > 0 || downloadsLoading) && (
        <section>
          <h2 className="text-sm font-semibold text-gray-400 uppercase mb-3">
            Active Downloads
          </h2>
          {downloadsLoading ? (
            <p className="text-gray-400 text-sm">Loading…</p>
          ) : (
            <div className="space-y-3">
              {activeDownloads.map((d) => (
                <ActiveDownloadCard
                  key={d.id}
                  download={d}
                  onCancel={handleCancel}
                  isCancelling={isCancelling}
                />
              ))}
            </div>
          )}
        </section>
      )}

      {/* Download History */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-gray-400 uppercase">Download History</h2>
          {historyDownloads.length > 0 && (
            <button
              onClick={() => setConfirmClear(true)}
              className="text-xs text-gray-400 hover:text-red-400 transition-colors"
            >
              Clear History
            </button>
          )}
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
          <DataTable
            data={historyDownloads}
            columns={historyColumns}
            isLoading={downloadsLoading}
            emptyMessage="No completed downloads yet."
            enableSorting
          />
        </div>
      </section>

      {/* Available Data */}
      <section>
        <h2 className="text-sm font-semibold text-gray-400 uppercase mb-3">
          Available Data
        </h2>
        {availableLoading ? (
          <p className="text-gray-400 text-sm">Loading…</p>
        ) : Object.keys(grouped).length === 0 ? (
          <p className="text-gray-500 text-sm">No data sources available.</p>
        ) : (
          <div className="space-y-4">
            {Object.entries(grouped).map(([provider, items]) => (
              <div
                key={provider}
                className="bg-gray-900 border border-gray-800 rounded p-4"
              >
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-lg font-semibold text-gray-200 capitalize">
                    {provider}
                  </h3>
                  <span className="text-xs text-gray-500">
                    {items.length} dataset{items.length !== 1 ? "s" : ""}
                  </span>
                </div>
                <div className="flex flex-wrap gap-2">
                  {items.map((it) => (
                    <button
                      key={`${it.symbol}-${it.timeframe}`}
                      onClick={() => setPreview({ provider: it.provider, symbol: it.symbol, timeframe: it.timeframe })}
                      title={`${formatSize(it.size_bytes)} · ${it.file_path}`}
                      className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs font-mono text-gray-300 hover:bg-gray-700 hover:border-indigo-600 cursor-pointer transition-colors"
                    >
                      <strong>{it.symbol}</strong>
                      <span className="text-gray-500 ml-1">{it.timeframe}</span>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Dataset Preview modal */}
      <DatasetPreviewModal
        open={!!preview}
        onClose={() => setPreview(null)}
        provider={preview?.provider ?? null}
        symbol={preview?.symbol ?? null}
        timeframe={preview?.timeframe ?? null}
      />

      {/* Delete single row confirm */}
      <ConfirmDialog
        open={!!rowToDelete}
        title="Delete download row"
        message={`Remove this ${rowToDelete?.status} download for ${rowToDelete?.symbols.join(", ")} from history?`}
        confirmLabel="Delete"
        onConfirm={async () => {
          if (!rowToDelete) return;
          try {
            await deleteDownload.mutateAsync(rowToDelete.id);
            addAlert({ message: "Download row deleted.", severity: "success" });
          } catch (err) {
            addAlert({
              message: err instanceof Error ? err.message : "Failed to delete row.",
              severity: "error",
            });
          } finally {
            setRowToDelete(null);
          }
        }}
        onCancel={() => setRowToDelete(null)}
      />

      {/* Clear history confirm */}
      <ConfirmDialog
        open={confirmClear}
        title="Clear download history"
        message={`Delete all ${historyDownloads.length} non-active download rows from history? Active downloads will be preserved.`}
        confirmLabel="Clear"
        onConfirm={async () => {
          try {
            const result = await clearDownloads.mutateAsync(undefined);
            addAlert({
              message: `Cleared ${result.deleted} download row${result.deleted === 1 ? "" : "s"}.`,
              severity: "success",
            });
          } catch (err) {
            addAlert({
              message: err instanceof Error ? err.message : "Failed to clear history.",
              severity: "error",
            });
          } finally {
            setConfirmClear(false);
          }
        }}
        onCancel={() => setConfirmClear(false)}
      />

      {/* Download Data modal */}
      <FormModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title="Download Data"
        schema={downloadSchema}
        defaultValues={{
          symbols: "",
          date_range_start: "",
          date_range_end: "",
          provider: "",
          data_types: [] as DataType[],
          timeframe: "",
        }}
        onSubmit={handleSubmit}
        submitLabel="Start Download"
        isSubmitting={isCreating}
      >
        {(form) => (
          <>
            <FormField label="Symbols" error={form.formState.errors.symbols?.message}>
              <input
                {...form.register("symbols")}
                className={INPUT_CLS}
                placeholder="AAPL, MSFT, SPY"
              />
            </FormField>

            <FormField
              label="Start Date"
              error={form.formState.errors.date_range_start?.message}
            >
              <input
                {...form.register("date_range_start")}
                type="date"
                className={INPUT_CLS}
              />
            </FormField>

            <FormField
              label="End Date"
              error={form.formState.errors.date_range_end?.message}
            >
              <input
                {...form.register("date_range_end")}
                type="date"
                className={INPUT_CLS}
              />
            </FormField>

            <FormField label="Provider" error={form.formState.errors.provider?.message}>
              <select {...form.register("provider")} className={INPUT_CLS}>
                <option value="">Select provider…</option>
                <option value="polygon">polygon</option>
                <option value="theta">theta</option>
              </select>
            </FormField>

            <FormField label="Data Types" error={form.formState.errors.data_types?.message as string | undefined}>
              <div className="flex flex-col gap-2 text-sm">
                {DATA_TYPE_OPTIONS.map((t) => (
                  <label
                    key={t.value}
                    className={`inline-flex items-center gap-2 ${
                      t.available ? "cursor-pointer" : "cursor-not-allowed opacity-50"
                    }`}
                  >
                    <input
                      type="checkbox"
                      value={t.value}
                      disabled={!t.available}
                      {...form.register("data_types")}
                      className="accent-indigo-500"
                    />
                    <span>{t.label}</span>
                    {!t.available && (
                      <span className="text-[10px] text-gray-500">(not yet supported)</span>
                    )}
                  </label>
                ))}
              </div>
            </FormField>

            <FormField label="Timeframe" error={form.formState.errors.timeframe?.message}>
              <select {...form.register("timeframe")} className={INPUT_CLS}>
                <option value="">Select timeframe…</option>
                <option value="1min">1min</option>
                <option value="5min">5min</option>
                <option value="15min">15min</option>
                <option value="1hour">1hour</option>
                <option value="1day">1day</option>
              </select>
            </FormField>
          </>
        )}
      </FormModal>
    </div>
  );
}
