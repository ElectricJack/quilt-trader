import { Widget } from "../Widget";
import { useEvents } from "../../api/hooks";

const SEVERITY_CLASSES: Record<string, string> = {
  warning: "bg-yellow-900 text-yellow-300",
  error: "bg-red-900 text-red-300",
  critical: "bg-red-900 text-red-200",
  info: "bg-blue-900 text-blue-300",
};

function formatTimestamp(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function SystemEventsWidget() {
  const { data, isLoading } = useEvents({ severity: "warning", limit: 5 });
  const events = data?.items ?? [];

  return (
    <Widget title="System Events" isLoading={isLoading}>
      {events.length === 0 ? (
        <p className="text-gray-500 text-sm">No recent warnings</p>
      ) : (
        <ul className="space-y-2">
          {events.map((event) => {
            const severityClass =
              SEVERITY_CLASSES[event.severity.toLowerCase()] ??
              "bg-gray-700 text-gray-300";
            return (
              <li
                key={event.id}
                className="flex items-center gap-2 py-1.5 border-b border-gray-800 last:border-b-0"
              >
                <span
                  className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium capitalize shrink-0 ${severityClass}`}
                >
                  {event.severity}
                </span>
                <span className="text-xs text-gray-300 truncate flex-1 capitalize">
                  {event.event_type.replace(/_/g, " ")}
                </span>
                <span className="text-xs text-gray-500 shrink-0">
                  {formatTimestamp(event.timestamp)}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </Widget>
  );
}
