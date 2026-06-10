import { useEffect } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { useDeployment } from "../../api/hooks";

export function LegacyDeploymentRedirect() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data, isLoading, error } = useDeployment(id);

  useEffect(() => {
    if (data?.algorithm_id) {
      navigate(`/algorithms/${data.algorithm_id}/deployments/${id}`, { replace: true });
    }
  }, [data, id, navigate]);

  if (isLoading) return <p className="p-6 text-gray-400">Redirecting…</p>;
  if (error || !data) {
    return (
      <div className="p-6">
        <p className="text-gray-300">Deployment not found.</p>
        <Link to="/algorithms" className="text-indigo-400">← Back to algorithms</Link>
      </div>
    );
  }
  return <p className="p-6 text-gray-400">Redirecting…</p>;
}
