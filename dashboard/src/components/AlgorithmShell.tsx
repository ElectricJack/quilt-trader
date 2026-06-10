import { Outlet, useParams, Link } from "react-router-dom";
import { useAlgorithm } from "../api/hooks";
import { AlgorithmStatusBadge } from "./AlgorithmStatusBadge";

export function AlgorithmShell() {
  const { id = "" } = useParams<{ id: string }>();
  const { data: algo, isLoading, error } = useAlgorithm(id);

  if (isLoading) {
    return (
      <div role="status" className="flex justify-center py-16">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
      </div>
    );
  }

  if (error || !algo) {
    return (
      <div className="px-6 py-12 text-center">
        <p className="text-lg text-gray-300">Algorithm not found</p>
        <Link to="/algorithms" className="mt-3 inline-block text-indigo-400 hover:text-indigo-300">
          ← Back to algorithms
        </Link>
      </div>
    );
  }

  const counts = algo.summary?.counts;

  return (
    <div className="px-6 py-4">
      <header className="mb-6 border-b border-gray-800 pb-4">
        <div className="flex items-baseline gap-3">
          <h1 className="text-2xl font-semibold text-gray-100">{algo.name}</h1>
          {algo.version && <span className="text-sm text-gray-500">v{algo.version}</span>}
          {algo.summary && <AlgorithmStatusBadge status={algo.summary.status} />}
        </div>
        {counts && (
          <p className="mt-1 text-sm text-gray-400">
            {counts.deployments} deployments · {counts.backtests} backtests · {counts.research_sessions} research sessions
          </p>
        )}
      </header>
      <Outlet />
    </div>
  );
}
