import { useEvents } from "../api/hooks";
import { StatusBadge } from "../components/StatusBadge";

const SEVERITY_CLASSES: Record<string, string> = {
  info: "bg-blue-900 text-blue-300",
  warning: "bg-yellow-900 text-yellow-300",
  error: "bg-red-900 text-red-300",
  critical: "bg-red-900 text-red-200",
};

function SeverityBadge({ severity }: { severity: string }) {
  const colorClass =
    SEVERITY_CLASSES[severity.toLowerCase()] ?? "bg-gray-700 text-gray-300";
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium capitalize ${colorClass}`}
    >
      {severity}
    </span>
  );
}

export function Notifications() {
  const { data, isLoading } = useEvents({ limit: 100 });

  const events = data?.items ?? [];

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-white">
        Notifications{" "}
        {!isLoading && (
          <span className="text-gray-400 text-base font-normal">
            ({data?.total ?? 0})
          </span>
        )}
      </h1>

      {isLoading ? (
        <p className="text-gray-400 text-sm">Loading…</p>
      ) : events.length === 0 ? (
        <p className="text-gray-500 text-sm">No events found.</p>
      ) : (
        <div className="space-y-2">
          {events.map((event) => (
            <div
              key={event.id}
              className="bg-gray-900 border border-gray-800 rounded p-3 flex items-start gap-3"
            >
              <div className="flex-shrink-0 pt-0.5">
                <SeverityBadge severity={event.severity} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-sm text-gray-200 font-medium">
                    {event.event_type}
                  </span>
                  <StatusBadge status={event.source_type} className="text-xs" />
                </div>
                {event.source_id && (
                  <p className="text-xs text-gray-500 mb-1">
                    Source: <span className="font-mono">{event.source_id}</span>
                  </p>
                )}
                {event.payload && Object.keys(event.payload).length > 0 && (
                  <pre className="text-xs text-gray-400 overflow-x-auto whitespace-pre-wrap max-h-24 overflow-y-auto">
                    {JSON.stringify(event.payload, null, 2)}
                  </pre>
                )}
              </div>
              <div className="flex-shrink-0 text-xs text-gray-500 whitespace-nowrap">
                {event.timestamp ? new Date(event.timestamp).toLocaleString() : "—"}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
