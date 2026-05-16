import { useEffect, useMemo, useState } from "react";
import { wsManager } from "../api/websocket";
import { useWorkerActivity, useDeploymentActivity } from "../api/hooks";
import type { ActivityRow } from "../types";

const SEVERITY_DOT: Record<string, string> = {
  debug: "bg-gray-500",
  info: "bg-blue-400",
  warn: "bg-yellow-400",
  error: "bg-red-500",
};

interface ActivityPanelProps {
  target: `worker:${string}` | `deployment:${string}`;
  initialRows?: ActivityRow[]; // for tests only
}

export function ActivityPanel({ target, initialRows }: ActivityPanelProps) {
  const [severity, setSeverity] = useState<"debug" | "info" | "warn" | "error">("info");
  const [kind, setKind] = useState<"all" | "event" | "log">("all");

  // Route to the right query hook depending on target prefix
  const isWorker = target.startsWith("worker:");
  const id = target.slice(target.indexOf(":") + 1);

  const workerQuery = useWorkerActivity(isWorker ? id : "", { severity, kind });
  const deploymentQuery = useDeploymentActivity(!isWorker ? id : "", { severity, kind });
  const fetched = isWorker ? workerQuery.data : deploymentQuery.data;

  const [liveRows, setLiveRows] = useState<ActivityRow[]>([]);

  // Reset live rows when filter or target changes (the new fetched data already
  // reflects the filter; live rows would be stale).
  useEffect(() => {
    setLiveRows([]);
  }, [target, severity, kind]);

  useEffect(() => {
    if (initialRows) return;
    wsManager.send({ type: "subscribe", target });
    const offEvent = wsManager.subscribe("activity_event", (data: unknown) => {
      const m = data as Record<string, unknown>;
      const matches = isWorker
        ? m.worker_id === id
        : m.instance_id === id;
      if (!matches) return;
      const row: ActivityRow = {
        id: String(m.id ?? Math.random()),
        worker_id: String(m.worker_id ?? ""),
        instance_id: (m.instance_id ?? null) as string | null,
        timestamp: String(m.timestamp ?? new Date().toISOString()),
        kind: "event",
        event_type: (m.event_type ?? null) as string | null,
        severity: ((m.severity ?? "info") as ActivityRow["severity"]),
        logger_name: null,
        message: (m.message ?? null) as string | null,
        payload: (m.payload ?? null) as Record<string, unknown> | null,
      };
      setLiveRows((prev) => [row, ...prev].slice(0, 500));
    });
    const offLog = wsManager.subscribe("algo_log", (data: unknown) => {
      const m = data as Record<string, unknown>;
      const matches = isWorker
        ? m.worker_id === id
        : m.instance_id === id;
      if (!matches) return;
      const row: ActivityRow = {
        id: String(m.id ?? Math.random()),
        worker_id: String(m.worker_id ?? ""),
        instance_id: (m.instance_id ?? null) as string | null,
        timestamp: String(m.timestamp ?? new Date().toISOString()),
        kind: "log",
        event_type: null,
        severity: ((m.severity ?? "info") as ActivityRow["severity"]),
        logger_name: (m.logger_name ?? null) as string | null,
        message: (m.message ?? null) as string | null,
        payload: null,
      };
      setLiveRows((prev) => [row, ...prev].slice(0, 500));
    });
    return () => {
      wsManager.send({ type: "unsubscribe", target });
      offEvent();
      offLog();
    };
  }, [target, id, isWorker, initialRows]);

  const rows = useMemo(() => {
    if (initialRows) return initialRows;
    const fetchedRows = fetched?.items ?? [];
    // Live rows precede fetched rows; dedupe by id.
    const seen = new Set<string>();
    const merged: ActivityRow[] = [];
    for (const r of [...liveRows, ...fetchedRows]) {
      if (seen.has(r.id)) continue;
      seen.add(r.id);
      merged.push(r);
    }
    return merged;
  }, [initialRows, fetched, liveRows]);

  if (rows.length === 0) {
    return <div className="text-gray-500 text-sm py-4">No activity in the last hour.</div>;
  }

  return (
    <div className="bg-gray-900 border border-gray-800 rounded">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-800 text-xs">
        <label className="text-gray-400">Severity</label>
        <select
          value={severity}
          onChange={(e) => setSeverity(e.target.value as ActivityRow["severity"])}
          className="bg-gray-800 border border-gray-700 rounded px-2 py-1"
        >
          <option value="debug">debug+</option>
          <option value="info">info+</option>
          <option value="warn">warn+</option>
          <option value="error">error only</option>
        </select>
        <label className="text-gray-400 ml-3">Kind</label>
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value as typeof kind)}
          className="bg-gray-800 border border-gray-700 rounded px-2 py-1"
        >
          <option value="all">all</option>
          <option value="event">events</option>
          <option value="log">logs</option>
        </select>
      </div>
      <ul className="max-h-96 overflow-y-auto font-mono text-xs">
        {rows.map((r) => (
          <li key={r.id} className="px-3 py-1 border-b border-gray-800 last:border-b-0 flex items-baseline gap-2">
            <span className="text-gray-500">{new Date(r.timestamp).toLocaleTimeString()}</span>
            <span className={`inline-block w-2 h-2 rounded-full ${SEVERITY_DOT[r.severity] ?? "bg-gray-500"}`} />
            <span className="text-gray-300">{r.event_type ?? r.logger_name}</span>
            {r.message && <span className="text-gray-200 ml-1">{r.message}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
