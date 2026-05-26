import { useEffect, useMemo, useState, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { z } from "zod";
import { Trash2, Play, Eye, RotateCcw } from "lucide-react";
import {
  useDownloads,
  useCreateDownload,
  useCancelDownload,
  useDeleteDownload,
  useClearDownloads,
  useRetryDownload,
  useScrapers,
  useInstallScraper,
  useDeleteScraper,
  useRunScraper,
  useDataSources,
} from "../api/hooks";
import { FormModal } from "../components/FormModal";
import { FormField } from "../components/FormField";
import { DataTable, type ColumnDef } from "../components/DataTable";
import { StatusBadge } from "../components/StatusBadge";
import { CustomDataPreviewModal } from "../components/CustomDataPreviewModal";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { LiveSubscriptionsSection } from "../components/LiveSubscriptionsSection";
import { AvailableDataTab } from "../components/AvailableDataTab";
import { DataGoalsTab } from "../components/DataGoalsTab";
import { useUIStore } from "../stores/ui";
import type { MarketDataDownload } from "../types";
import type { DataSourceRow } from "../api/client";

// ─── Schema ───────────────────────────────────────────────────────────────────

const DATA_TYPE_OPTIONS: { value: "bars" | "quotes" | "trades"; label: string; available: boolean }[] = [
  { value: "bars", label: "Bars (OHLCV)", available: true },
  { value: "quotes", label: "Quotes", available: false },
  { value: "trades", label: "Trades", available: false },
];

const DATA_TYPES = ["bars", "quotes", "trades"] as const;
type DataType = (typeof DATA_TYPES)[number];

const TIMEFRAMES = ["1min", "5min", "15min", "1hour", "1day"] as const;
type Timeframe = (typeof TIMEFRAMES)[number];

const downloadSchema = z.object({
  symbols: z.string().min(1, "Required"),
  date_range_start: z.string().min(1, "Required"),
  date_range_end: z.string().min(1, "Required"),
  provider: z.string().min(1, "Required"),
  data_types: z
    .array(z.enum(DATA_TYPES))
    .min(1, "Select at least one data type"),
  // Advanced: defaults to ["1min"]; user can expand to override.
  timeframes: z
    .array(z.enum(TIMEFRAMES))
    .min(1, "Select at least one timeframe"),
  show_advanced: z.boolean().optional(),
});

type DownloadFormValues = z.infer<typeof downloadSchema>;

const DOWNLOAD_PREFS_KEY = "data.downloadModal.lastSettings";

const EMPTY_DOWNLOAD_DEFAULTS: DownloadFormValues = {
  symbols: "",
  date_range_start: "",
  date_range_end: "",
  provider: "",
  data_types: [] as DataType[],
  timeframes: ["1min"] as Timeframe[],
  show_advanced: false,
};

function loadDownloadDefaults(): DownloadFormValues {
  if (typeof window === "undefined") return EMPTY_DOWNLOAD_DEFAULTS;
  try {
    const raw = window.localStorage.getItem(DOWNLOAD_PREFS_KEY);
    if (!raw) return EMPTY_DOWNLOAD_DEFAULTS;
    const parsed = downloadSchema.partial().safeParse(JSON.parse(raw));
    if (!parsed.success) return EMPTY_DOWNLOAD_DEFAULTS;
    return { ...EMPTY_DOWNLOAD_DEFAULTS, ...parsed.data };
  } catch {
    return EMPTY_DOWNLOAD_DEFAULTS;
  }
}

const scraperInstallSchema = z.object({
  repo_url: z.string().min(1, "Required").url("Must be a valid URL"),
  name: z.string().optional(),
});

type ScraperInstallValues = z.infer<typeof scraperInstallSchema>;

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
  setRowToDelete: (row: MarketDataDownload | null) => void,
  onRetry: (id: string) => void,
  isRetrying: boolean,
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
        <div className="flex items-center gap-1">
          {(row.original.status === "failed" || row.original.status === "cancelled") && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onRetry(row.original.id);
              }}
              disabled={isRetrying}
              title="Retry download (skips already-fetched data)"
              className="p-1 text-gray-400 hover:text-indigo-400 disabled:opacity-50 transition-colors"
            >
              <RotateCcw size={14} />
            </button>
          )}
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
        </div>
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
  const total = download.progress_total;
  // Overall pct = (symbols completed + fraction of current symbol) / total symbols
  const symbolFraction = download.current_symbol_pct ?? 0;
  const pct = total > 0
    ? Math.round(((download.progress_current + symbolFraction) / total) * 100)
    : 0;
  const isRunning = download.status === "running" || download.status === "queued";
  const indeterminate = isRunning && pct === 0;

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
      {indeterminate ? (
        <div className="bg-gray-700 rounded-full h-2 mb-1 progress-indeterminate" />
      ) : (
        <div className="bg-gray-700 rounded-full h-2 mb-1">
          <div
            className="bg-indigo-600 h-2 rounded-full transition-all duration-300"
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
      <p className="text-xs text-gray-500">
        {indeterminate
          ? "working…"
          : `${download.progress_current} of ${download.progress_total}${pct > 0 ? ` · ${pct}%` : ""}`}
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

const TABS = ["goals", "acquisition", "available", "history"] as const;
type Tab = (typeof TABS)[number];
const TAB_LABELS: Record<Tab, string> = {
  goals: "Data Goals",
  acquisition: "Data Acquisition",
  available: "Available Data",
  history: "Download History",
};

export function Data() {
  const [searchParams, setSearchParams] = useSearchParams();
  const rawTab = searchParams.get("tab");
  const activeTab: Tab = TABS.includes(rawTab as Tab) ? (rawTab as Tab) : "acquisition";
  const setActiveTab = useCallback(
    (tab: Tab) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        next.set("tab", tab);
        return next;
      }, { replace: true });
    },
    [setSearchParams],
  );

  const [modalOpen, setModalOpen] = useState(false);
  const [rowToDelete, setRowToDelete] = useState<MarketDataDownload | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [installScraperOpen, setInstallScraperOpen] = useState(false);
  const [scraperToDelete, setScraperToDelete] = useState<string | null>(null);
  const [customPreview, setCustomPreview] = useState<DataSourceRow | null>(null);
  const [downloadDefaults, setDownloadDefaults] = useState<DownloadFormValues>(loadDownloadDefaults);

  const { data: downloads, isLoading: downloadsLoading, refetch } = useDownloads();
  const { mutateAsync: createDownload, isPending: isCreating } = useCreateDownload();
  const { mutate: cancelDownload, isPending: isCancelling } = useCancelDownload();
  const deleteDownload = useDeleteDownload();
  const clearDownloads = useClearDownloads();
  const retryDownload = useRetryDownload();
  const { data: scrapers = [], isLoading: scrapersLoading } = useScrapers();
  const { data: scraperSources = [], isLoading: sourcesLoading } = useDataSources("scraper");
  const installScraper = useInstallScraper();
  const deleteScraper = useDeleteScraper();
  const runScraper = useRunScraper();
  const addAlert = useUIStore((s) => s.addAlert);

  // Index sources by scraper name so we can join with the scraper list cleanly.
  const sourceBySrc = useMemo(() => {
    const m = new Map<string, DataSourceRow>();
    for (const s of scraperSources) m.set(s.source, s);
    return m;
  }, [scraperSources]);

  async function handleInstallScraper(values: ScraperInstallValues) {
    try {
      const record = await installScraper.mutateAsync({
        repo_url: values.repo_url,
        name: values.name?.trim() || undefined,
      });
      addAlert({ message: `Installed scraper '${record.name}'.`, severity: "success" });
      setInstallScraperOpen(false);
    } catch (e) {
      addAlert({
        message: `Install failed: ${(e as Error).message}`,
        severity: "error",
      });
    }
  }

  async function handleRunScraper(name: string) {
    try {
      const r = await runScraper.mutateAsync(name);
      if (r.success) {
        addAlert({ message: `Scraper '${name}' completed.`, severity: "success" });
      } else {
        addAlert({ message: `Scraper '${name}' failed: ${r.error}`, severity: "error" });
      }
    } catch (e) {
      addAlert({ message: `Run failed: ${(e as Error).message}`, severity: "error" });
    }
  }

  async function handleConfirmDeleteScraper() {
    if (!scraperToDelete) return;
    try {
      await deleteScraper.mutateAsync(scraperToDelete);
      addAlert({ message: `Scraper '${scraperToDelete}' removed.`, severity: "success" });
    } catch (e) {
      addAlert({ message: `Delete failed: ${(e as Error).message}`, severity: "error" });
    } finally {
      setScraperToDelete(null);
    }
  }

  async function handleRetry(id: string) {
    try {
      const result = await retryDownload.mutateAsync(id);
      addAlert({ message: result.message, severity: "success" });
    } catch (e) {
      addAlert({
        message: `Retry failed: ${(e as Error).message}`,
        severity: "error",
      });
    }
  }

  const historyColumns = useMemo(
    () => makeHistoryColumns(setRowToDelete, handleRetry, retryDownload.isPending),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [retryDownload.isPending],
  );

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

  async function handleSubmit(values: DownloadFormValues) {
    setDownloadDefaults(values);
    try {
      window.localStorage.setItem(DOWNLOAD_PREFS_KEY, JSON.stringify(values));
    } catch {
      // Storage may be unavailable (private mode, quota); silently ignore.
    }

    const symbols = values.symbols
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);

    const jobs = values.data_types.flatMap((dt) =>
      values.timeframes.map((tf) => ({ data_type: dt, timeframe: tf }))
    );

    const results = await Promise.allSettled(
      jobs.map((j) =>
        createDownload({
          symbols,
          date_range_start: values.date_range_start,
          date_range_end: values.date_range_end,
          provider: values.provider,
          data_type: j.data_type,
          timeframe: j.timeframe,
        })
      )
    );
    const successes = results.filter((r) => r.status === "fulfilled").length;
    const failures = results.length - successes;
    setModalOpen(false);
    if (failures === 0) {
      addAlert({
        message: `Queued ${successes} download${successes === 1 ? "" : "s"} (${values.data_types.join(", ")} × ${values.timeframes.join(", ")} for ${symbols.join(", ")}).`,
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
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Data</h1>
        <div className="flex gap-2">
          {activeTab === "acquisition" && (
            <button
              onClick={() => setInstallScraperOpen(true)}
              className="px-3 py-2 rounded text-sm font-medium text-gray-200 bg-gray-700 hover:bg-gray-600 transition-colors"
            >
              Install Scraper
            </button>
          )}
          {activeTab === "available" && (
            <button
              onClick={() => setModalOpen(true)}
              className="px-4 py-2 rounded text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 transition-colors"
            >
              Download Market Data
            </button>
          )}
          {activeTab === "history" && historyDownloads.length > 0 && (
            <button
              onClick={() => setConfirmClear(true)}
              className="px-3 py-2 rounded text-sm font-medium text-gray-200 bg-gray-700 hover:bg-gray-600 transition-colors"
            >
              Clear Download History
            </button>
          )}
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-gray-800">
        {TABS.map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 text-sm font-medium transition-colors relative ${
              activeTab === tab
                ? "text-white"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            {TAB_LABELS[tab]}
            {activeTab === tab && (
              <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-indigo-500" />
            )}
          </button>
        ))}
      </div>

      {/* ── Data Goals tab ── */}
      {activeTab === "goals" && <DataGoalsTab />}

      {/* ── Data Acquisition tab ── */}
      {activeTab === "acquisition" && (
        <div className="space-y-8">
          <section>
            <h2 className="text-sm font-semibold text-gray-400 uppercase mb-3">
              Scrapers{" "}
              {scrapers.length > 0 && (
                <span className="font-normal text-gray-500">({scrapers.length})</span>
              )}
            </h2>
            {scrapersLoading || sourcesLoading ? (
              <p className="text-gray-400 text-sm">Loading…</p>
            ) : scrapers.length === 0 ? (
              <p className="text-gray-500 text-sm">
                No scrapers installed. Click "Install Scraper" above to add one from a git repo.
              </p>
            ) : (
              <div className="space-y-2">
                {scrapers.map((s) => {
                  const source = sourceBySrc.get(s.name);
                  const rowCount = (source?.metadata as { row_count?: number } | null)?.row_count ?? null;
                  return (
                    <div
                      key={s.name}
                      className="bg-gray-900 border border-gray-800 rounded p-3 flex items-center justify-between gap-3"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-gray-100 truncate">{s.name}</span>
                          {s.version && (
                            <span className="text-[10px] text-gray-500 font-mono">v{s.version}</span>
                          )}
                          {s.last_status && (
                            <span
                              className={`text-[10px] px-1.5 py-0.5 rounded border ${
                                s.last_status === "ok"
                                  ? "bg-green-900/40 text-green-300 border-green-800"
                                  : s.last_status === "running"
                                  ? "bg-blue-900/40 text-blue-300 border-blue-800"
                                  : "bg-red-900/40 text-red-300 border-red-800"
                              }`}
                            >
                              {s.last_status}
                            </span>
                          )}
                        </div>
                        <div className="text-xs text-gray-500 mt-0.5 flex flex-wrap gap-x-4 gap-y-0.5">
                          {s.description && <span className="truncate max-w-md">{s.description}</span>}
                          <span>schedule: <span className="font-mono text-gray-400">{s.schedule}</span></span>
                          {s.next_run_at && s.next_run_at !== "None" && (
                            <span>next: <span className="text-gray-400">{s.next_run_at}</span></span>
                          )}
                          {source?.last_updated && (
                            <span>
                              updated: <span className="text-gray-400">{new Date(source.last_updated).toLocaleString()}</span>
                              {rowCount != null && <span className="text-gray-500"> ({rowCount} rows)</span>}
                            </span>
                          )}
                        </div>
                        {s.last_error && (
                          <p className="text-xs text-red-400 mt-1 truncate" title={s.last_error}>
                            {s.last_error}
                          </p>
                        )}
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        {source && (
                          <button
                            onClick={() => setCustomPreview(source)}
                            className="p-1.5 text-gray-400 hover:text-indigo-400 hover:bg-gray-800 rounded transition-colors"
                            title="Preview output"
                          >
                            <Eye size={14} />
                          </button>
                        )}
                        <button
                          onClick={() => handleRunScraper(s.name)}
                          disabled={runScraper.isPending}
                          className="p-1.5 text-gray-400 hover:text-green-400 hover:bg-gray-800 rounded transition-colors disabled:opacity-60"
                          title="Run now"
                        >
                          <Play size={14} />
                        </button>
                        <button
                          onClick={() => setScraperToDelete(s.name)}
                          className="p-1.5 text-gray-400 hover:text-red-400 hover:bg-gray-800 rounded transition-colors"
                          title="Uninstall"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </section>

          <LiveSubscriptionsSection />
        </div>
      )}

      {/* ── Available Data tab ── */}
      {activeTab === "available" && <AvailableDataTab />}

      {/* ── Download History tab ── */}
      {activeTab === "history" && (
        <div className="space-y-8">
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

          <section>
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
        </div>
      )}

      {/* Custom data preview modal (scraper outputs) */}
      <CustomDataPreviewModal
        open={!!customPreview}
        onClose={() => setCustomPreview(null)}
        sourceName={customPreview?.source ?? null}
        description={customPreview?.description ?? null}
        lastUpdated={customPreview?.last_updated ?? null}
      />

      {/* Install scraper modal */}
      <FormModal
        open={installScraperOpen}
        onClose={() => setInstallScraperOpen(false)}
        title="Install Scraper"
        schema={scraperInstallSchema}
        defaultValues={{ repo_url: "", name: "" }}
        onSubmit={handleInstallScraper}
        submitLabel="Install"
        isSubmitting={installScraper.isPending}
      >
        {(form) => (
          <>
            <FormField label="Repository URL" error={form.formState.errors.repo_url?.message}>
              <input
                {...form.register("repo_url")}
                className={INPUT_CLS}
                placeholder="https://github.com/owner/scraper-repo.git"
              />
            </FormField>
            <FormField
              label="Local Name (optional)"
              error={form.formState.errors.name?.message}
            >
              <input
                {...form.register("name")}
                className={INPUT_CLS}
                placeholder="derived from repo name if blank"
              />
            </FormField>
            <p className="text-xs text-gray-500">
              The scraper is cloned to <code>packages/&lt;name&gt;</code>, a venv is created,
              <code> requirements.txt </code>is installed, and the cron schedule from
              <code> quilt.yaml </code>is registered. This can take ~30s.
            </p>
          </>
        )}
      </FormModal>

      {/* Confirm scraper delete */}
      <ConfirmDialog
        open={!!scraperToDelete}
        title="Uninstall scraper"
        message={`Remove '${scraperToDelete}'? This unschedules cron and deletes the package directory.`}
        confirmLabel="Uninstall"
        onConfirm={handleConfirmDeleteScraper}
        onCancel={() => setScraperToDelete(null)}
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
        defaultValues={downloadDefaults}
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

            {/* Advanced section: timeframe selector (defaults to 1min) */}
            <div>
              <button
                type="button"
                onClick={() => form.setValue("show_advanced", !form.watch("show_advanced"))}
                className="text-xs text-gray-400 hover:text-gray-200 transition-colors flex items-center gap-1"
              >
                <span>{form.watch("show_advanced") ? "▾" : "▸"}</span>
                <span>Advanced</span>
              </button>
              {form.watch("show_advanced") && (
                <FormField
                  label="Timeframes"
                  error={form.formState.errors.timeframes?.message as string | undefined}
                >
                  <div className="flex flex-wrap gap-x-4 gap-y-2 text-sm mt-2">
                    {TIMEFRAMES.map((tf) => (
                      <label key={tf} className="inline-flex items-center gap-2 cursor-pointer">
                        <input
                          type="checkbox"
                          value={tf}
                          {...form.register("timeframes")}
                          className="accent-indigo-500"
                        />
                        <span>{tf}</span>
                      </label>
                    ))}
                  </div>
                  <p className="text-[10px] text-gray-500 mt-1">
                    Defaults to 1min. Each selected timeframe queues its own download.
                  </p>
                </FormField>
              )}
              {!form.watch("show_advanced") && (
                <p className="text-[10px] text-gray-500 mt-1">Timeframe: 1min (canonical — higher timeframes are derived on read)</p>
              )}
            </div>
          </>
        )}
      </FormModal>
    </div>
  );
}
