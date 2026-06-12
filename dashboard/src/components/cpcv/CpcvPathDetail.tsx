import { Link } from "react-router-dom";
import type { CPCVPathSegment } from "../../types";

interface Props {
  algorithmId: string;
  path: CPCVPathSegment[];
  pathIndex: number;
  pathSharpe: number;
}

export function CpcvPathDetail({ algorithmId, path, pathIndex, pathSharpe }: Props) {
  return (
    <div className="rounded border border-gray-800 bg-gray-900 p-4">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-sm text-gray-300">Path {pathIndex}</h3>
        <span className="text-sm text-gray-400">Sharpe {pathSharpe.toFixed(2)}</span>
      </div>
      <table className="w-full text-sm">
        <thead className="text-left text-gray-500">
          <tr>
            <th className="px-2 py-1">Group</th>
            <th className="px-2 py-1">Split</th>
            <th className="px-2 py-1">Backtest</th>
          </tr>
        </thead>
        <tbody>
          {path.map((seg) => (
            <tr key={`${seg.group}-${seg.split}`} className="border-t border-gray-800">
              <td className="px-2 py-1">G{seg.group}</td>
              <td className="px-2 py-1 text-gray-400">S{seg.split}</td>
              <td className="px-2 py-1">
                <Link
                  className="text-indigo-400"
                  to={`/algorithms/${algorithmId}/backtests/${seg.run_id}`}
                >
                  {seg.run_id}
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
