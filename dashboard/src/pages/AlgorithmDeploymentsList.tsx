import { useParams, Link } from "react-router-dom";
import { useDeployments } from "../api/hooks";

export function AlgorithmDeploymentsList() {
  const { id = "" } = useParams<{ id: string }>();
  const { data, isLoading } = useDeployments({ algorithm_id: id });

  if (isLoading) return <p className="text-gray-400">Loading deployments…</p>;
  if (!data?.length) {
    return (
      <p className="text-gray-400">
        No deployments.{" "}
        <Link to={`/algorithms/${id}`} className="text-indigo-400">
          Deploy from the hub →
        </Link>
      </p>
    );
  }
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-gray-500">
        <tr>
          <th className="px-2 py-1">Account</th>
          <th className="px-2 py-1">Status</th>
          <th className="px-2 py-1">Today's PnL</th>
        </tr>
      </thead>
      <tbody>
        {data.map((d: any) => (
          <tr key={d.id} className="border-t border-gray-800">
            <td className="px-2 py-1">
              <Link
                to={`/algorithms/${id}/deployments/${d.id}`}
                className="text-indigo-400"
              >
                {d.account_name ?? d.account_id}
              </Link>
            </td>
            <td className="px-2 py-1">{d.status}</td>
            <td className="px-2 py-1">{d.today_pnl?.toFixed(2) ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
