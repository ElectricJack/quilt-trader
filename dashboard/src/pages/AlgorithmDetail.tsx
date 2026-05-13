import { useParams, Link } from "react-router-dom";
import { useAlgorithm, useInstances } from "../api/hooks";
import { StatusBadge } from "../components/StatusBadge";

export function AlgorithmDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: algorithm, isLoading: loadingAlgo } = useAlgorithm(id ?? "");
  const { data: instances, isLoading: loadingInstances } = useInstances(id ?? "");

  if (loadingAlgo) {
    return <p className="text-gray-400 text-sm">Loading…</p>;
  }

  if (!algorithm) {
    return <p className="text-gray-400 text-sm">Algorithm not found.</p>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold text-white">{algorithm.name}</h1>
        {algorithm.version && (
          <span className="text-gray-400 text-base">v{algorithm.version}</span>
        )}
        <StatusBadge status={algorithm.install_status} />
      </div>

      {/* Metadata */}
      <div className="bg-gray-900 border border-gray-800 rounded p-4 space-y-3">
        <h2 className="text-lg font-semibold text-gray-200">Details</h2>
        {algorithm.description && (
          <p className="text-sm text-gray-300">{algorithm.description}</p>
        )}
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-xs text-gray-500 uppercase">Repository</span>
            <p className="text-gray-200 mt-0.5 break-all">
              <a
                href={algorithm.repo_url}
                target="_blank"
                rel="noreferrer"
                className="text-indigo-400 hover:underline"
              >
                {algorithm.repo_url}
              </a>
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Commit</span>
            <p className="text-gray-200 mt-0.5 font-mono text-xs">
              {algorithm.commit_hash ? algorithm.commit_hash.slice(0, 8) : "—"}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Required Assets</span>
            <p className="text-gray-200 mt-0.5">
              {(algorithm.required_asset_types ?? []).join(", ") || "—"}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Supported Brokers</span>
            <p className="text-gray-200 mt-0.5">
              {(algorithm.supported_brokers ?? []).join(", ") || "—"}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Required Options Level</span>
            <p className="text-gray-200 mt-0.5">
              {algorithm.required_options_level !== null ? algorithm.required_options_level : "—"}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Installed At</span>
            <p className="text-gray-200 mt-0.5 text-xs">
              {algorithm.installed_at ? new Date(algorithm.installed_at).toLocaleString() : "—"}
            </p>
          </div>
        </div>
        {algorithm.install_error && (
          <div className="bg-red-950 border border-red-800 rounded p-3">
            <p className="text-xs text-red-400">{algorithm.install_error}</p>
          </div>
        )}
      </div>

      {/* Config Schema */}
      {algorithm.config_schema && (
        <div className="bg-gray-900 border border-gray-800 rounded p-4">
          <h2 className="text-lg font-semibold text-gray-200 mb-3">Config Schema</h2>
          <pre className="text-xs text-gray-300 overflow-x-auto whitespace-pre-wrap">
            {JSON.stringify(algorithm.config_schema, null, 2)}
          </pre>
        </div>
      )}

      {/* Instances */}
      <section>
        <h2 className="text-lg font-semibold text-gray-200 mb-3">Instances</h2>
        {loadingInstances ? (
          <p className="text-gray-400 text-sm">Loading…</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left text-gray-300">
              <thead className="text-xs text-gray-400 uppercase border-b border-gray-800">
                <tr>
                  <th className="py-3 px-4">ID</th>
                  <th className="py-3 px-4">Status</th>
                  <th className="py-3 px-4">Account</th>
                  <th className="py-3 px-4">Worker</th>
                  <th className="py-3 px-4">Created</th>
                </tr>
              </thead>
              <tbody>
                {(instances ?? []).map((inst) => (
                  <tr key={inst.id} className="border-b border-gray-800 hover:bg-gray-900">
                    <td className="py-3 px-4">
                      <Link
                        to={`/instances/${inst.id}`}
                        className="text-indigo-400 hover:underline font-mono text-xs"
                      >
                        {inst.id.slice(0, 8)}…
                      </Link>
                    </td>
                    <td className="py-3 px-4">
                      <StatusBadge status={inst.status} />
                    </td>
                    <td className="py-3 px-4 text-xs text-gray-400">
                      <Link
                        to={`/accounts/${inst.account_id}`}
                        className="text-indigo-400 hover:underline"
                      >
                        {inst.account_id.slice(0, 8)}…
                      </Link>
                    </td>
                    <td className="py-3 px-4 text-xs text-gray-400">
                      {inst.worker_id.slice(0, 8)}…
                    </td>
                    <td className="py-3 px-4 text-xs text-gray-400">
                      {new Date(inst.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
                {(instances ?? []).length === 0 && (
                  <tr>
                    <td colSpan={5} className="py-6 text-center text-gray-500">
                      No instances found.
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
