import { useState } from "react";
import { useParams } from "react-router-dom";
import { useAlgorithm } from "../api/hooks";
import { ParameterSetsSection } from "../components/ParameterSetsSection";
import { RunBacktestModal } from "../components/RunBacktestModal";

export function AlgorithmConfig() {
  const { id = "" } = useParams<{ id: string }>();
  const { data: algo, isLoading } = useAlgorithm(id);
  const [backtestOpen, setBacktestOpen] = useState(false);
  const [backtestPreloadSetId, setBacktestPreloadSetId] = useState<string>();

  if (isLoading) return <p className="text-sm text-gray-400">Loading…</p>;
  if (!algo) return <p className="text-sm text-gray-400">Algorithm not found.</p>;

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

      <ParameterSetsSection
        algorithmId={algo.id}
        manifestConfig={
          (algo.config_schema?.parameters as Array<{
            name: string;
            type: string;
            default?: unknown;
          }>) ?? []
        }
        onBacktest={(setId) => {
          setBacktestPreloadSetId(setId);
          setBacktestOpen(true);
        }}
      />

      <section>
        <h2 className="mb-2 text-lg font-medium text-gray-100">Manifest</h2>
        <pre className="overflow-x-auto rounded bg-gray-950 p-3 text-xs text-gray-300">
{JSON.stringify(algo.config_schema, null, 2)}
        </pre>
      </section>

      <RunBacktestModal
        open={backtestOpen}
        onClose={() => { setBacktestOpen(false); setBacktestPreloadSetId(undefined as any); }}
        algorithmId={algo.id}
        manifestConfig={(algo.config_schema?.parameters as Array<{ name: string; type: string; default?: unknown }>) ?? []}
        parameterSets={(algo as any).parameter_sets ?? []}
        preloadSetId={backtestPreloadSetId}
      />
    </div>
  );
}
