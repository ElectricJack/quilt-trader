import { useEffect } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { useResearchSession } from "../../hooks/useResearchSession";

export function LegacyResearchSessionRedirect() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const numericId = id ? parseInt(id, 10) : null;
  const { data, isLoading, error } = useResearchSession(numericId);

  useEffect(() => {
    if (data?.algorithm_id) {
      navigate(`/algorithms/${data.algorithm_id}/research/${id}`, { replace: true });
    }
  }, [data, id, navigate]);

  if (isLoading) return <p className="p-6 text-gray-400">Redirecting…</p>;
  if (error || !data) {
    return (
      <div className="p-6">
        <p className="text-gray-300">Research session not found.</p>
        <Link to="/algorithms" className="text-indigo-400">← Back to algorithms</Link>
      </div>
    );
  }
  return <p className="p-6 text-gray-400">Redirecting…</p>;
}
