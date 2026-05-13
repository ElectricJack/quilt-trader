import { useParams, Link } from "react-router-dom";
import { useBacktest } from "../api/hooks";

export function BacktestDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: bt, isLoading } = useBacktest(id ?? "");

  if (isLoading) {
    return <p className="text-gray-400 text-sm">Loading…</p>;
  }

  if (!bt) {
    return <p className="text-gray-400 text-sm">Backtest not found.</p>;
  }

  const matchColor =
    bt.match_percentage >= 90
      ? "text-green-400"
      : bt.match_percentage >= 70
      ? "text-yellow-400"
      : "text-red-400";

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">Backtest Comparison</h1>

      {/* Overview */}
      <div className="bg-gray-900 border border-gray-800 rounded p-4 space-y-3">
        <h2 className="text-lg font-semibold text-gray-200">Details</h2>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-xs text-gray-500 uppercase">Instance</span>
            <p className="text-gray-200 mt-0.5">
              <Link
                to={`/instances/${bt.instance_id}`}
                className="text-indigo-400 hover:underline font-mono text-xs"
              >
                {bt.instance_id.slice(0, 8)}…
              </Link>
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Algorithm</span>
            <p className="text-gray-200 mt-0.5">
              <Link
                to={`/algorithms/${bt.algorithm_id}`}
                className="text-indigo-400 hover:underline font-mono text-xs"
              >
                {bt.algorithm_id.slice(0, 8)}…
              </Link>
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Time Range Start</span>
            <p className="text-gray-200 mt-0.5 text-xs">
              {bt.time_range_start ? new Date(bt.time_range_start).toLocaleString() : "—"}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Time Range End</span>
            <p className="text-gray-200 mt-0.5 text-xs">
              {bt.time_range_end ? new Date(bt.time_range_end).toLocaleString() : "—"}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Total Ticks</span>
            <p className="text-gray-200 mt-0.5">{bt.total_ticks.toLocaleString()}</p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Matching Ticks</span>
            <p className="text-gray-200 mt-0.5">{bt.matching_ticks.toLocaleString()}</p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Match Percentage</span>
            <p className={`mt-0.5 font-bold text-lg ${matchColor}`}>
              {bt.match_percentage.toFixed(2)}%
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Created</span>
            <p className="text-gray-200 mt-0.5 text-xs">
              {bt.created_at ? new Date(bt.created_at).toLocaleString() : "—"}
            </p>
          </div>
        </div>
      </div>

      {/* Summary */}
      {bt.summary && (
        <div className="bg-gray-900 border border-gray-800 rounded p-4">
          <h2 className="text-lg font-semibold text-gray-200 mb-2">Summary</h2>
          <p className="text-sm text-gray-300">{bt.summary}</p>
        </div>
      )}

      {/* Divergences */}
      <section>
        <h2 className="text-lg font-semibold text-gray-200 mb-3">
          Divergences{" "}
          {bt.divergences && (
            <span className="text-gray-400 text-base font-normal">
              ({bt.divergences.length})
            </span>
          )}
        </h2>
        {!bt.divergences || bt.divergences.length === 0 ? (
          <p className="text-gray-500 text-sm">No divergences recorded.</p>
        ) : (
          <div className="space-y-2">
            {bt.divergences.map((div, i) => (
              <div
                key={i}
                className="bg-gray-900 border border-gray-800 rounded p-3"
              >
                <pre className="text-xs text-gray-300 overflow-x-auto whitespace-pre-wrap">
                  {JSON.stringify(div, null, 2)}
                </pre>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
