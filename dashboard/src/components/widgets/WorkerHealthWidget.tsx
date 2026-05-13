import { Widget } from "../Widget";
import { useWorkers } from "../../api/hooks";

function relativeTime(ts: string | null): string {
  if (!ts) return "no heartbeat";
  const diffMs = Date.now() - new Date(ts).getTime();
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  return `${diffHr}h ago`;
}

export function WorkerHealthWidget() {
  const { data: workers, isLoading } = useWorkers();

  return (
    <Widget title="Worker Health" isLoading={isLoading}>
      {!workers || workers.length === 0 ? (
        <p className="text-gray-500 text-sm">No workers registered</p>
      ) : (
        <ul className="space-y-2">
          {workers.map((worker) => {
            const isOnline = worker.status === "online";
            return (
              <li
                key={worker.id}
                className="flex items-center justify-between py-1.5 border-b border-gray-800 last:border-b-0"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span
                    className={`w-2 h-2 rounded-full shrink-0 ${
                      isOnline ? "bg-green-400" : "bg-gray-600"
                    }`}
                  />
                  <span className="text-sm text-gray-200 truncate">
                    {worker.name}
                  </span>
                </div>
                <span className="text-xs text-gray-500 shrink-0 ml-2">
                  {relativeTime(worker.last_heartbeat)}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </Widget>
  );
}
