import { Link } from "react-router-dom";
import { useBacktests } from "../api/hooks";

export function Backtests() {
  const { data: backtests, isLoading } = useBacktests();

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-white">
        Backtests{" "}
        {!isLoading && (
          <span className="text-gray-400 text-base font-normal">
            ({backtests?.length ?? 0})
          </span>
        )}
      </h1>

      {isLoading ? (
        <p className="text-gray-400 text-sm">Loading…</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left text-gray-300">
            <thead className="text-xs text-gray-400 uppercase border-b border-gray-800">
              <tr>
                <th className="py-3 px-4">ID</th>
                <th className="py-3 px-4">Instance</th>
                <th className="py-3 px-4">Time Range</th>
                <th className="py-3 px-4">Total Ticks</th>
                <th className="py-3 px-4">Match %</th>
                <th className="py-3 px-4">Created</th>
              </tr>
            </thead>
            <tbody>
              {(backtests ?? []).map((bt) => (
                <tr key={bt.id} className="border-b border-gray-800 hover:bg-gray-900">
                  <td className="py-3 px-4">
                    <Link
                      to={`/backtests/${bt.id}`}
                      className="text-indigo-400 hover:underline font-mono text-xs"
                    >
                      {bt.id.slice(0, 8)}…
                    </Link>
                  </td>
                  <td className="py-3 px-4 font-mono text-xs text-gray-400">
                    <Link
                      to={`/instances/${bt.instance_id}`}
                      className="text-indigo-400 hover:underline"
                    >
                      {bt.instance_id.slice(0, 8)}…
                    </Link>
                  </td>
                  <td className="py-3 px-4 text-xs text-gray-400">
                    {bt.time_range_start
                      ? new Date(bt.time_range_start).toLocaleDateString()
                      : "—"}{" "}
                    –{" "}
                    {bt.time_range_end
                      ? new Date(bt.time_range_end).toLocaleDateString()
                      : "—"}
                  </td>
                  <td className="py-3 px-4">{bt.total_ticks.toLocaleString()}</td>
                  <td className="py-3 px-4">
                    <span
                      className={
                        bt.match_percentage >= 90
                          ? "text-green-400"
                          : bt.match_percentage >= 70
                          ? "text-yellow-400"
                          : "text-red-400"
                      }
                    >
                      {bt.match_percentage.toFixed(1)}%
                    </span>
                  </td>
                  <td className="py-3 px-4 text-xs text-gray-400">
                    {bt.created_at ? new Date(bt.created_at).toLocaleString() : "—"}
                  </td>
                </tr>
              ))}
              {(backtests ?? []).length === 0 && (
                <tr>
                  <td colSpan={6} className="py-6 text-center text-gray-500">
                    No backtests found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
