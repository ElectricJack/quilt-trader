import { useParams, Link } from "react-router-dom";
import { useRun } from "../api/hooks";
import { StatusBadge } from "../components/StatusBadge";

export function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: run, isLoading } = useRun(id ?? "");

  if (isLoading) {
    return <p className="text-gray-400 text-sm">Loading…</p>;
  }

  if (!run) {
    return <p className="text-gray-400 text-sm">Run not found.</p>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold text-white">Run</h1>
        <StatusBadge status={run.status} />
      </div>

      {/* Run Info */}
      <div className="bg-gray-900 border border-gray-800 rounded p-4 space-y-3">
        <h2 className="text-lg font-semibold text-gray-200">Details</h2>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-xs text-gray-500 uppercase">Run ID</span>
            <p className="text-gray-200 mt-0.5 font-mono text-xs">{run.id}</p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Instance</span>
            <p className="text-gray-200 mt-0.5">
              <Link
                to={`/instances/${run.instance_id}`}
                className="text-indigo-400 hover:underline font-mono text-xs"
              >
                {run.instance_id.slice(0, 8)}…
              </Link>
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Started At</span>
            <p className="text-gray-200 mt-0.5 text-xs">
              {run.started_at ? new Date(run.started_at).toLocaleString() : "—"}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Ended At</span>
            <p className="text-gray-200 mt-0.5 text-xs">
              {run.ended_at ? new Date(run.ended_at).toLocaleString() : "—"}
            </p>
          </div>
          {run.started_at && run.ended_at && (
            <div>
              <span className="text-xs text-gray-500 uppercase">Duration</span>
              <p className="text-gray-200 mt-0.5 text-xs">
                {Math.round(
                  (new Date(run.ended_at).getTime() - new Date(run.started_at).getTime()) / 1000
                )}s
              </p>
            </div>
          )}
        </div>
        {run.error && (
          <div className="bg-red-950 border border-red-800 rounded p-3">
            <p className="text-xs text-red-400 font-mono">{run.error}</p>
          </div>
        )}
      </div>
    </div>
  );
}
