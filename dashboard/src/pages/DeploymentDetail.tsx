import { useParams, Link } from "react-router-dom";
import { ChevronLeft } from "lucide-react";
import {
  useDeployment,
  useStartDeployment,
  useStopDeployment,
} from "../api/hooks";
import { StatusBadge } from "../components/StatusBadge";
import { useUIStore } from "../stores/ui";

export function DeploymentDetail() {
  const { id = "" } = useParams<{ id: string }>();
  const { data: dep, isLoading } = useDeployment(id);
  const start = useStartDeployment();
  const stop = useStopDeployment();
  const addAlert = useUIStore((s) => s.addAlert);

  if (isLoading) return <p className="text-gray-400 text-sm">Loading…</p>;
  if (!dep) return <p className="text-gray-400 text-sm">Deployment not found.</p>;

  const canStart = dep.status === "stopped" || dep.status === "error";
  const isRunning = dep.status === "running" || dep.status === "starting";

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3 flex-wrap">
        <Link
          to={`/algorithms/${dep.algorithm_id}`}
          className="flex items-center gap-1 text-sm text-gray-400 hover:text-gray-200 transition-colors"
        >
          <ChevronLeft size={16} />
          {dep.algorithm_name}
        </Link>

        <div className="flex items-center gap-3 flex-1 min-w-0">
          <h1 className="text-2xl font-bold text-white truncate">
            {dep.algorithm_name}
          </h1>
          <StatusBadge status={dep.status} />
        </div>

        <div className="text-sm text-gray-400">
          <Link
            to={`/accounts/${dep.account_id}`}
            className="hover:text-gray-200"
          >
            {dep.account_name}
          </Link>
          {" · "}
          <Link
            to={`/workers/${dep.worker_id}`}
            className="hover:text-gray-200"
          >
            {dep.worker_name}
          </Link>
        </div>

        <div className="flex gap-2 ml-auto">
          {canStart && (
            <button
              onClick={() =>
                start.mutate(id, {
                  onError: () =>
                    addAlert({
                      message: "Failed to start deployment.",
                      severity: "error",
                    }),
                })
              }
              disabled={start.isPending}
              className="bg-green-600 hover:bg-green-500 disabled:opacity-50 text-white text-sm px-3 py-1.5 rounded"
            >
              Start
            </button>
          )}
          {isRunning && (
            <button
              onClick={() =>
                stop.mutate(id, {
                  onError: () =>
                    addAlert({
                      message: "Failed to stop deployment.",
                      severity: "error",
                    }),
                })
              }
              disabled={stop.isPending}
              className="bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white text-sm px-3 py-1.5 rounded"
            >
              Stop
            </button>
          )}
        </div>
      </div>

      {/* KPIs / chart grid / tables / activity panel will be added by M6.2–M6.5 */}
      <p className="text-gray-500 text-sm">
        Live report visualization coming soon.
      </p>
    </div>
  );
}
