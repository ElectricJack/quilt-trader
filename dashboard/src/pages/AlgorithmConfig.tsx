import { useParams } from "react-router-dom";
import { useAlgorithm } from "../api/hooks";

export function AlgorithmConfig() {
  const { id = "" } = useParams<{ id: string }>();
  const { data: algo } = useAlgorithm(id);
  if (!algo) return null;
  return (
    <div className="space-y-6">
      <section>
        <h2 className="mb-2 text-lg font-medium text-gray-100">Repository</h2>
        <p className="text-sm text-gray-400">
          <a
            href={algo.repo_url}
            target="_blank"
            rel="noreferrer"
            className="text-indigo-400"
          >
            {algo.repo_url}
          </a>{" "}
          · {algo.commit_hash?.slice(0, 8) ?? "—"}
        </p>
      </section>
      <section>
        <h2 className="mb-2 text-lg font-medium text-gray-100">Manifest</h2>
        <pre className="overflow-x-auto rounded bg-gray-950 p-3 text-xs text-gray-300">
{JSON.stringify(algo.config_schema, null, 2)}
        </pre>
      </section>
    </div>
  );
}
