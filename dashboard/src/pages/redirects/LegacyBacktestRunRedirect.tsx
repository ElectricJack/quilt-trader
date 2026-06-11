import { useEffect } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { useBacktestRun } from "../../api/hooks";

export function LegacyBacktestRunRedirect() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data, isLoading, error } = useBacktestRun(id);

  useEffect(() => {
    if (data?.algorithm_id) {
      navigate(`/algorithms/${data.algorithm_id}/backtests/${id}`, { replace: true });
    }
  }, [data, id, navigate]);

  if (isLoading) return <p className="p-6 text-gray-400">Redirecting…</p>;
  if (error || !data) {
    return (
      <div className="p-6">
        <p className="text-gray-300">Backtest run not found.</p>
        <Link to="/algorithms" className="text-indigo-400">← Back to algorithms</Link>
      </div>
    );
  }
  return <p className="p-6 text-gray-400">Redirecting…</p>;
}
