import { useEffect, useMemo, useState } from "react";
import { z } from "zod";
import {
  useAvailableData,
  useDownloads,
  useCreateDownload,
  useCancelDownload,
} from "../api/hooks";
import { FormModal } from "../components/FormModal";
import { FormField } from "../components/FormField";
import { DataTable, type ColumnDef } from "../components/DataTable";
import { StatusBadge } from "../components/StatusBadge";
import { useUIStore } from "../stores/ui";
import type { MarketDataDownload } from "../types";

// ─── Schema ───────────────────────────────────────────────────────────────────

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

const historyColumns: ColumnDef<MarketDataDownload, unknown>[] = [
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
    cell: ({ row }) => <StatusBadge status={row.original.status} />,
  },
  {
    id: "completed_at",
    header: "Completed",
    accessorKey: "completed_at",
    cell: ({ row }) => formatDate(row.original.completed_at),
  },
];

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

  const { data: available, isLoading: availableLoading } = useAvailableData();
  const { data: downloads, isLoading: downloadsLoading, refetch } = useDownloads();
  const { mutateAsync: createDownload, isPending: isCreating } = useCreateDownload();
  const { mutate: cancelDownload, isPending: isCancelling } = useCancelDownload();
  const addAlert = useUIStore((s) => s.addAlert);

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
    const id = setInterval(() => void refetch(), 5000);
    return () => clearInterval(id);
  }, [activeDownloads.length, refetch]);

  const providers = available ? Object.entries(available) : [];

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
        <h2 className="text-sm font-semibold text-gray-400 uppercase mb-3">
          Download History
        </h2>
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
        ) : providers.length === 0 ? (
          <p className="text-gray-500 text-sm">No data sources available.</p>
        ) : (
          <div className="space-y-4">
            {providers.map(([provider, symbols]) => (
              <div
                key={provider}
                className="bg-gray-900 border border-gray-800 rounded p-4"
              >
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-lg font-semibold text-gray-200 capitalize">
                    {provider}
                  </h3>
                  <span className="text-xs text-gray-500">
                    {symbols.length} symbol{symbols.length !== 1 ? "s" : ""}
                  </span>
                </div>
                {symbols.length === 0 ? (
                  <p className="text-sm text-gray-500">No symbols available.</p>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {symbols.map((symbol) => (
                      <span
                        key={symbol}
                        className="bg-gray-800 border border-gray-700 rounded px-2 py-0.5 text-xs text-gray-300 font-mono"
                      >
                        {symbol}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

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

            <FormField
              label="Data Types"
              error={form.formState.errors.data_types?.message as string | undefined}
            >
              <div className="flex gap-4 text-sm">
                {DATA_TYPES.map((t) => (
                  <label
                    key={t}
                    className="inline-flex items-center gap-2 cursor-pointer text-gray-200"
                  >
                    <input
                      type="checkbox"
                      value={t}
                      {...form.register("data_types")}
                      className="accent-indigo-500"
                    />
                    <span className="capitalize">{t}</span>
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
