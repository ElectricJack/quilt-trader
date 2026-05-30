// src/components/DatasetsAvailableSection.tsx
import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { CoverageRange } from "../api/client";
import { DataTable } from "./DataTable";
import { DatasetPreviewModal } from "./DatasetPreviewModal";
import { DatasetsFilterBar } from "./DatasetsFilterBar";
import { InteractiveCoverageBar } from "./InteractiveCoverageBar";
import type { ColumnDef } from "@tanstack/react-table";

interface Row {
  name: string;
  provider: string;
  symbol_keyed: boolean;
  total_rows: number;
  event_date_min: string | null;
  event_date_max: string | null;
  knowledge_date_max: string | null;
  file_size_bytes: number;
}

function toIsoDay(value: string | null | undefined): string | null {
  if (!value) return null;
  return value.slice(0, 10);
}

export function DatasetsAvailableSection() {
  const [search, setSearch] = useState("");
  const [providerFilter, setProviderFilter] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ name: string; symbolKeyed: boolean } | null>(null);

  const specs = useQuery({
    queryKey: ["datasets", "list"],
    queryFn: api.listDatasets,
  });

  // Fetch per-dataset coverage in parallel to compute aggregate row counts
  const detailQueries = useQuery({
    queryKey: ["datasets", "coverage-details", specs.data?.map((s) => s.name)],
    queryFn: async () => {
      if (!specs.data) return {};
      const results = await Promise.all(
        specs.data.map(async (s) => {
          try {
            return [s.name, await api.getDatasetCoverage(s.name)] as const;
          } catch {
            return [s.name, { name: s.name, symbols: [] }] as const;
          }
        }),
      );
      return Object.fromEntries(results);
    },
    enabled: !!specs.data,
  });

  const providers = useMemo(
    () => Array.from(new Set((specs.data ?? []).map((s) => s.provider))),
    [specs.data],
  );

  const rows = useMemo<Row[]>(() => {
    if (!specs.data) return [];
    const details = detailQueries.data ?? {};
    return specs.data
      .filter((s) => !providerFilter || s.provider === providerFilter)
      .filter((s) => !search || s.name.toLowerCase().includes(search.toLowerCase()))
      .map((s) => {
        const cov: Array<{
          row_count?: number;
          event_date_min?: string | null;
          event_date_max?: string | null;
          knowledge_date_max?: string | null;
          file_size_bytes?: number;
        }> = details[s.name]?.symbols ?? [];

        const totalRows = cov.reduce((acc, x) => acc + (x.row_count ?? 0), 0);

        const eventMin =
          cov.length > 0
            ? cov.reduce((a, b) =>
                a.event_date_min && (!b.event_date_min || a.event_date_min < b.event_date_min)
                  ? a
                  : b,
              ).event_date_min ?? null
            : null;

        const eventMax =
          cov.length > 0
            ? cov.reduce((a, b) =>
                a.event_date_max && (!b.event_date_max || a.event_date_max > b.event_date_max)
                  ? a
                  : b,
              ).event_date_max ?? null
            : null;

        const knowledgeMax =
          cov.length > 0
            ? cov.reduce((a, b) =>
                a.knowledge_date_max &&
                (!b.knowledge_date_max || a.knowledge_date_max > b.knowledge_date_max)
                  ? a
                  : b,
              ).knowledge_date_max ?? null
            : null;

        const totalBytes = cov.reduce((acc, x) => acc + (x.file_size_bytes ?? 0), 0);

        return {
          name: s.name,
          provider: s.provider,
          symbol_keyed: s.symbol_keyed,
          total_rows: totalRows,
          event_date_min: eventMin,
          event_date_max: eventMax,
          knowledge_date_max: knowledgeMax,
          file_size_bytes: totalBytes,
        };
      });
  }, [specs.data, detailQueries.data, search, providerFilter]);

  // Shared bar window across all datasets so coverage spans are visually
  // comparable. Spans the wider of (today − 2y) and the earliest event_date
  // across all datasets, through today.
  const { windowStart, windowEnd } = useMemo(() => {
    const today = new Date();
    const twoYearsAgo = new Date(today);
    twoYearsAgo.setFullYear(twoYearsAgo.getFullYear() - 2);
    let earliestMs = twoYearsAgo.getTime();
    for (const r of rows) {
      const d = toIsoDay(r.event_date_min);
      if (d) earliestMs = Math.min(earliestMs, new Date(d).getTime());
    }
    return {
      windowStart: new Date(earliestMs).toISOString().slice(0, 10),
      windowEnd: today.toISOString().slice(0, 10),
    };
  }, [rows]);

  const columns = useMemo<ColumnDef<Row>[]>(
    () => [
      { header: "Dataset", accessorKey: "name" },
      { header: "Provider", accessorKey: "provider" },
      {
        header: "Scope",
        accessorFn: (r) => (r.symbol_keyed ? "per-symbol" : "firehose"),
      },
      {
        header: "Rows",
        accessorFn: (r) => (r.total_rows > 0 ? r.total_rows.toLocaleString() : "—"),
      },
      {
        header: "Coverage",
        cell: ({ row }) => {
          const r = row.original;
          const startIso = toIsoDay(r.event_date_min);
          const endIso = toIsoDay(r.event_date_max);
          if (!startIso || !endIso) {
            return (
              <span className="text-xs text-gray-600">no data</span>
            );
          }
          const ranges: CoverageRange[] = [{ start: startIso, end: endIso }];
          return (
            <div className="flex items-center gap-2 min-w-[16rem]">
              <span className="text-[10px] text-gray-500 font-mono whitespace-nowrap">
                {startIso}
              </span>
              <InteractiveCoverageBar
                ranges={ranges}
                provider={r.provider}
                windowStart={windowStart}
                windowEnd={windowEnd}
                markerDate={null}
                onClick={() => undefined}
              />
              <span className="text-[10px] text-gray-500 font-mono whitespace-nowrap">
                {endIso}
              </span>
            </div>
          );
        },
      },
      {
        header: "Fresh as of",
        accessorFn: (r) => (r.knowledge_date_max ? r.knowledge_date_max.slice(0, 10) : "—"),
      },
      {
        header: "Size",
        accessorFn: (r) =>
          r.file_size_bytes > 0
            ? `${(r.file_size_bytes / 1024 / 1024).toFixed(1)} MB`
            : "—",
      },
    ],
    [windowStart, windowEnd],
  );

  return (
    <div className="space-y-3">
      <DatasetsFilterBar
        search={search}
        onSearch={setSearch}
        provider={providerFilter}
        onProvider={setProviderFilter}
        providers={providers}
      />
      <div className="text-[10px] text-gray-500 font-mono px-2">
        Coverage bar window: {windowStart} → {windowEnd}
      </div>
      <DataTable
        data={rows}
        columns={columns}
        isLoading={specs.isLoading || detailQueries.isLoading}
        emptyMessage="No datasets registered yet"
        onRowClick={(r) => setPreview({ name: r.name, symbolKeyed: r.symbol_keyed })}
      />
      {preview && (
        <DatasetPreviewModal
          open={true}
          onClose={() => setPreview(null)}
          datasetName={preview.name}
          symbolKeyed={preview.symbolKeyed}
        />
      )}
    </div>
  );
}
