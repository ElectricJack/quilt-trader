import { useState } from "react";
import { X } from "lucide-react";
import { JsonTextField } from "./JsonTextField";
import { useAlgorithms } from "../api/hooks";
import { useCreateResearchSweep } from "../hooks/useResearchMutations";

interface Props {
  open: boolean;
  sessionId: number;
  onClose: () => void;
}

export function NewSweepModal({ open, sessionId, onClose }: Props) {
  const algos = useAlgorithms();
  const mut = useCreateResearchSweep(sessionId);

  const [algorithmId, setAlgorithmId] = useState<string>("");
  const [baseConfig, setBaseConfig] = useState<Record<string, unknown> | null>({});
  const [baseConfigError, setBaseConfigError] = useState(false);
  const [paramSpace, setParamSpace] = useState<Record<string, unknown> | null>(null);
  const [search, setSearch] = useState<"grid" | "random" | "latin" | "tpe">("grid");
  const [maxTrials, setMaxTrials] = useState(50);
  const [parallelism, setParallelism] = useState(1);
  const [seed, setSeed] = useState<number | "">("");
  const [submitError, setSubmitError] = useState<string | null>(null);

  if (!open) return null;

  const canSubmit =
    algorithmId !== "" && baseConfig !== null && !baseConfigError && !mut.isPending;

  const handleSubmit = async () => {
    setSubmitError(null);
    try {
      await mut.mutateAsync({
        algorithm_id: algorithmId,
        base_config: baseConfig ?? {},
        parameter_space: paramSpace,
        search,
        max_trials: maxTrials,
        parallelism,
        seed: seed === "" ? undefined : seed,
      });
      onClose();
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : "Failed to start sweep");
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70" onClick={onClose} aria-hidden="true" />
      <div className="relative z-10 bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-2xl mx-auto flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800 shrink-0">
          <h2 className="text-xl font-bold text-white">New Sweep</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-white"><X size={20} /></button>
        </div>
        <div className="overflow-auto px-6 py-4 space-y-4">
          <div className="space-y-1">
            <label htmlFor="sw-algo" className="text-sm text-gray-300">
              Algorithm <span className="text-red-400">*</span>
            </label>
            <select
              id="sw-algo"
              value={algorithmId}
              onChange={(e) => setAlgorithmId(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
            >
              <option value="">— select —</option>
              {(algos.data ?? []).map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} ({a.id})
                </option>
              ))}
            </select>
          </div>
          <JsonTextField
            label="Base config"
            value={baseConfig}
            onChange={setBaseConfig}
            onError={setBaseConfigError}
            placeholder='{"vol_target": 0.10}'
          />
          <JsonTextField
            label="Parameter space (optional — falls back to session's space)"
            value={paramSpace}
            onChange={setParamSpace}
            placeholder='{"vol_target": [0.10, 0.15]}'
          />
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1">
              <label htmlFor="sw-search" className="text-sm text-gray-300">Search</label>
              <select
                id="sw-search"
                value={search}
                onChange={(e) => setSearch(e.target.value as typeof search)}
                className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
              >
                <option value="grid">grid</option>
                <option value="random">random</option>
                <option value="latin">latin</option>
                <option value="tpe">tpe</option>
              </select>
            </div>
            <div className="space-y-1">
              <label htmlFor="sw-max" className="text-sm text-gray-300">Max trials</label>
              <input
                id="sw-max" type="number" min={1} value={maxTrials}
                onChange={(e) => setMaxTrials(parseInt(e.target.value || "0", 10))}
                className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
              />
            </div>
            <div className="space-y-1">
              <label htmlFor="sw-par" className="text-sm text-gray-300">Parallelism</label>
              <input
                id="sw-par" type="number" min={1} value={parallelism}
                onChange={(e) => setParallelism(parseInt(e.target.value || "1", 10))}
                className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
              />
            </div>
            <div className="space-y-1">
              <label htmlFor="sw-seed" className="text-sm text-gray-300">Seed (optional)</label>
              <input
                id="sw-seed" type="number" value={seed}
                onChange={(e) => setSeed(e.target.value === "" ? "" : parseInt(e.target.value, 10))}
                className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
              />
            </div>
          </div>
          {submitError && <div className="text-red-400 text-sm">{submitError}</div>}
        </div>
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-800 shrink-0">
          <button onClick={onClose} className="text-gray-400 hover:text-white px-3 py-1.5 rounded text-sm">Cancel</button>
          <button
            onClick={() => void handleSubmit()}
            disabled={!canSubmit}
            className="bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-4 py-2 rounded disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {mut.isPending ? "Queuing…" : "Start sweep"}
          </button>
        </div>
      </div>
    </div>
  );
}
