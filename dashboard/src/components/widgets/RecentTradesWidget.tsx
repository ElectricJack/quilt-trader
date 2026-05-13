import { Widget } from "../Widget";
import { useEvents } from "../../api/hooks";

function formatTimestamp(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function RecentTradesWidget() {
  const { data, isLoading } = useEvents({ event_type: "trade_executed", limit: 5 });
  const trades = data?.items ?? [];

  return (
    <Widget title="Recent Trades" isLoading={isLoading}>
      {trades.length === 0 ? (
        <p className="text-gray-500 text-sm">No recent trades</p>
      ) : (
        <ul className="space-y-2">
          {trades.map((event) => (
            <li
              key={event.id}
              className="flex items-center justify-between py-1.5 border-b border-gray-800 last:border-b-0"
            >
              <div className="flex flex-col gap-0.5 min-w-0">
                <span className="text-xs text-gray-300 font-medium capitalize">
                  {event.event_type.replace(/_/g, " ")}
                </span>
                <span className="text-xs text-gray-500 font-mono truncate max-w-[140px]">
                  {event.source_id ?? "—"}
                </span>
              </div>
              <span className="text-xs text-gray-500 shrink-0 ml-2">
                {formatTimestamp(event.timestamp)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Widget>
  );
}
