import { useParams, Link } from "react-router-dom";
import { useInstance, useAlgorithm, useAccount, useWorker, useRuns } from "../api/hooks";
import { StatusBadge } from "../components/StatusBadge";

export function InstanceDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: instance, isLoading: loadingInstance } = useInstance(id ?? "");
  const { data: algorithm } = useAlgorithm(instance?.algorithm_id ?? "");
  const { data: account } = useAccount(instance?.account_id ?? "");
  const { data: worker } = useWorker(instance?.worker_id ?? "");
  const { data: runs, isLoading: loadingRuns } = useRuns(id ?? "");

  if (loadingInstance) {
    return <p className="text-gray-400 text-sm">Loading…</p>;
  }

  if (!instance) {
    return <p className="text-gray-400 text-sm">Instance not found.</p>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold text-white">Instance</h1>
        <StatusBadge status={instance.status} />
      </div>

      {/* Instance Info */}
      <div className="bg-gray-900 border border-gray-800 rounded p-4 space-y-3">
        <h2 className="text-lg font-semibold text-gray-200">Details</h2>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-xs text-gray-500 uppercase">Algorithm</span>
            <p className="text-gray-200 mt-0.5">
              {algorithm ? (
                <Link
                  to={`/algorithms/${instance.algorithm_id}`}
                  className="text-indigo-400 hover:underline"
                >
                  {algorithm.name}
                  {algorithm.version ? ` v${algorithm.version}` : ""}
                </Link>
              ) : (
                <span className="font-mono text-xs">{instance.algorithm_id}</span>
              )}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Account</span>
            <p className="text-gray-200 mt-0.5">
              {account ? (
                <Link
                  to={`/accounts/${instance.account_id}`}
                  className="text-indigo-400 hover:underline"
                >
                  {account.name}
                </Link>
              ) : (
                <span className="font-mono text-xs">{instance.account_id}</span>
              )}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Worker</span>
            <p className="text-gray-200 mt-0.5">
              {worker ? worker.name : (
                <span className="font-mono text-xs">{instance.worker_id}</span>
              )}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Active Run</span>
            <p className="text-gray-200 mt-0.5">
              {instance.active_run_id ? (
                <Link
                  to={`/runs/${instance.active_run_id}`}
                  className="text-indigo-400 hover:underline font-mono text-xs"
                >
                  {instance.active_run_id.slice(0, 8)}…
                </Link>
              ) : "—"}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">State Stale</span>
            <p className="text-gray-200 mt-0.5">{instance.state_stale ? "Yes" : "No"}</p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Created</span>
            <p className="text-gray-200 mt-0.5 text-xs">
              {new Date(instance.created_at).toLocaleString()}
            </p>
          </div>
        </div>
      </div>

      {/* Config Values */}
      {instance.config_values && Object.keys(instance.config_values).length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded p-4">
          <h2 className="text-lg font-semibold text-gray-200 mb-3">Config Values</h2>
          <pre className="text-xs text-gray-300 overflow-x-auto whitespace-pre-wrap">
            {JSON.stringify(instance.config_values, null, 2)}
          </pre>
        </div>
      )}

      {/* Persisted State */}
      {instance.persisted_state && Object.keys(instance.persisted_state).length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded p-4">
          <h2 className="text-lg font-semibold text-gray-200 mb-3">Persisted State</h2>
          <pre className="text-xs text-gray-300 overflow-x-auto whitespace-pre-wrap">
            {JSON.stringify(instance.persisted_state, null, 2)}
          </pre>
        </div>
      )}

      {/* Lifetime Metrics */}
      {instance.lifetime_metrics && Object.keys(instance.lifetime_metrics).length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded p-4">
          <h2 className="text-lg font-semibold text-gray-200 mb-3">Lifetime Metrics</h2>
          <div className="grid grid-cols-2 gap-3 text-sm">
            {Object.entries(instance.lifetime_metrics).map(([key, val]) => (
              <div key={key}>
                <span className="text-xs text-gray-500 uppercase">{key.replace(/_/g, " ")}</span>
                <p className="text-gray-200 mt-0.5">
                  {typeof val === "number" ? val.toLocaleString() : String(val)}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Runs */}
      <section>
        <h2 className="text-lg font-semibold text-gray-200 mb-3">Runs</h2>
        {loadingRuns ? (
          <p className="text-gray-400 text-sm">Loading…</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left text-gray-300">
              <thead className="text-xs text-gray-400 uppercase border-b border-gray-800">
                <tr>
                  <th className="py-3 px-4">ID</th>
                  <th className="py-3 px-4">Status</th>
                  <th className="py-3 px-4">Started</th>
                  <th className="py-3 px-4">Ended</th>
                </tr>
              </thead>
              <tbody>
                {(runs ?? []).map((run) => (
                  <tr key={run.id} className="border-b border-gray-800 hover:bg-gray-900">
                    <td className="py-3 px-4">
                      <Link
                        to={`/runs/${run.id}`}
                        className="text-indigo-400 hover:underline font-mono text-xs"
                      >
                        {run.id.slice(0, 8)}…
                      </Link>
                    </td>
                    <td className="py-3 px-4">
                      <StatusBadge status={run.status} />
                    </td>
                    <td className="py-3 px-4 text-xs text-gray-400">
                      {run.started_at ? new Date(run.started_at).toLocaleString() : "—"}
                    </td>
                    <td className="py-3 px-4 text-xs text-gray-400">
                      {run.ended_at ? new Date(run.ended_at).toLocaleString() : "—"}
                    </td>
                  </tr>
                ))}
                {(runs ?? []).length === 0 && (
                  <tr>
                    <td colSpan={4} className="py-6 text-center text-gray-500">
                      No runs found.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
