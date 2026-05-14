import { useState } from "react";
import { useEvents } from "../api/hooks";
import { DataTable, ColumnDef } from "../components/DataTable";

interface SystemEvent {
  id: string;
  source_type: string;
  source_id: string | null;
  event_type: string;
  severity: string;
  payload: Record<string, unknown> | null;
  timestamp: string | null;
  routed_to_discord: boolean;
  discord_channel: string | null;
}

const SEVERITY_COLORS: Record<string, string> = {
  info: "bg-blue-900 text-blue-300",
  warning: "bg-yellow-900 text-yellow-300",
  error: "bg-red-900 text-red-300",
  critical: "bg-red-700 text-red-100",
};

const LIMIT = 20;

const SELECT_CLASS =
  "bg-gray-800 border border-gray-700 text-gray-200 rounded px-3 py-2 text-sm";

const columns: ColumnDef<SystemEvent>[] = [
  {
    id: "severity",
    header: "Severity",
    accessorKey: "severity",
    cell: ({ getValue }) => {
      const severity = getValue<string>();
      const colorClass =
        SEVERITY_COLORS[severity?.toLowerCase()] ?? "bg-gray-700 text-gray-300";
      return (
        <span
          className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium capitalize ${colorClass}`}
        >
          {severity}
        </span>
      );
    },
  },
  {
    id: "event_type",
    header: "Event Type",
    accessorKey: "event_type",
    cell: ({ getValue }) => (
      <span className="text-gray-200">{getValue<string>()}</span>
    ),
  },
  {
    id: "source",
    header: "Source",
    accessorKey: "source_type",
    cell: ({ row }) => (
      <div>
        <div className="text-gray-200">{row.original.source_type}</div>
        {row.original.source_id && (
          <div className="text-xs text-gray-500 font-mono mt-0.5">
            {row.original.source_id}
          </div>
        )}
      </div>
    ),
  },
  {
    id: "timestamp",
    header: "Timestamp",
    accessorKey: "timestamp",
    cell: ({ getValue }) => {
      const ts = getValue<string | null>();
      return (
        <span className="text-gray-400 whitespace-nowrap text-xs">
          {ts ? new Date(ts).toLocaleString() : "—"}
        </span>
      );
    },
  },
  {
    id: "discord",
    header: "Discord",
    accessorKey: "routed_to_discord",
    cell: ({ getValue }) =>
      getValue<boolean>() ? (
        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-indigo-900 text-indigo-300">
          Sent
        </span>
      ) : null,
  },
];

export function Notifications() {
  const [severity, setSeverity] = useState("");
  const [eventType, setEventType] = useState("");
  const [sourceType, setSourceType] = useState("");
  const [offset, setOffset] = useState(0);

  const { data, isLoading } = useEvents({
    ...(severity ? { severity } : {}),
    ...(eventType ? { event_type: eventType } : {}),
    ...(sourceType ? { source_type: sourceType } : {}),
    limit: LIMIT,
    offset,
  });

  const events = data?.items ?? [];
  const total = data?.total ?? 0;

  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + LIMIT, total);
  const hasPrev = offset > 0;
  const hasNext = offset + LIMIT < total;

  function handleFilterChange<T>(setter: (v: T) => void) {
    return (v: T) => {
      setter(v);
      setOffset(0);
    };
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-white">
        Notifications{" "}
        {!isLoading && (
          <span className="text-gray-400 text-base font-normal">
            ({total})
          </span>
        )}
      </h1>

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-3">
        <select
          className={SELECT_CLASS}
          value={severity}
          onChange={(e) => handleFilterChange(setSeverity)(e.target.value)}
        >
          <option value="">All severities</option>
          <option value="info">Info</option>
          <option value="warning">Warning</option>
          <option value="error">Error</option>
          <option value="critical">Critical</option>
        </select>

        <input
          type="text"
          className={SELECT_CLASS}
          placeholder="Filter by event type..."
          value={eventType}
          onChange={(e) => handleFilterChange(setEventType)(e.target.value)}
        />

        <select
          className={SELECT_CLASS}
          value={sourceType}
          onChange={(e) => handleFilterChange(setSourceType)(e.target.value)}
        >
          <option value="">All sources</option>
          <option value="algorithm_instance">algorithm_instance</option>
          <option value="worker">worker</option>
          <option value="system">system</option>
        </select>
      </div>

      {/* Table */}
      <div className="bg-gray-900 border border-gray-800 rounded overflow-hidden">
        <DataTable<SystemEvent>
          data={events}
          columns={columns}
          isLoading={isLoading}
          emptyMessage="No events match the current filters."
          enableSorting={true}
        />
      </div>

      {/* Server-side pagination */}
      {!isLoading && total > 0 && (
        <div className="flex items-center justify-between px-1">
          <span className="text-sm text-gray-400">
            Showing {rangeStart}–{rangeEnd} of {total}
          </span>
          <div className="flex gap-2">
            <button
              className="bg-gray-700 hover:bg-gray-600 text-gray-300 px-3 py-1 rounded text-sm disabled:opacity-50 disabled:cursor-not-allowed"
              onClick={() => setOffset(offset - LIMIT)}
              disabled={!hasPrev}
            >
              Previous
            </button>
            <button
              className="bg-gray-700 hover:bg-gray-600 text-gray-300 px-3 py-1 rounded text-sm disabled:opacity-50 disabled:cursor-not-allowed"
              onClick={() => setOffset(offset + LIMIT)}
              disabled={!hasNext}
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
