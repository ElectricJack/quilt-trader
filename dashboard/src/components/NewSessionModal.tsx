import { useState } from "react";
import { X } from "lucide-react";
import { useAlgorithms } from "../api/hooks";
import { ExperimentConfigEditor } from "./ExperimentConfigEditor";
import { useCreateResearchSession } from "../hooks/useResearchMutations";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (sessionId: number) => void;
}

export function NewSessionModal({ open, onClose, onCreated }: Props) {
  const algos = useAlgorithms();
  const mut = useCreateResearchSession();

  const [name, setName] = useState("");
  const [algorithmId, setAlgorithmId] = useState<string>("");
  const [hypothesis, setHypothesis] = useState("");
  const [notes, setNotes] = useState("");
  const [config, setConfig] = useState<{
    base_config: Record<string, unknown> | null;
    parameter_space: Record<string, unknown> | null;
    pre_registered_criteria: Record<string, unknown> | null;
  }>({
    base_config: {},
    parameter_space: null,
    pre_registered_criteria: null,
  });
  const [configValid, setConfigValid] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  if (!open) return null;

  const canSubmit =
    name.trim().length > 0 &&
    algorithmId !== "" &&
    hypothesis.trim().length > 0 &&
    config.base_config !== null &&
    config.parameter_space !== null &&
    config.pre_registered_criteria !== null &&
    configValid &&
    !mut.isPending;

  const handleSubmit = async () => {
    setSubmitError(null);
    try {
      const session = await mut.mutateAsync({
        name: name.trim(),
        hypothesis: hypothesis.trim(),
        algorithm_id: algorithmId,
        base_config: config.base_config ?? {},
        parameter_space: config.parameter_space ?? {},
        pre_registered_criteria: config.pre_registered_criteria ?? {},
        notes: notes.trim(),
      });
      onCreated(session.id);
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : "Failed to create session");
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70" onClick={onClose} aria-hidden="true" />
      <div className="relative z-10 bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-5xl mx-auto flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800 shrink-0">
          <h2 className="text-xl font-bold text-white">New Research Session</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-white"><X size={20} /></button>
        </div>
        <div className="overflow-auto px-6 py-4 space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1">
              <label htmlFor="rs-name" className="text-sm text-gray-300">
                Name <span className="text-red-400">*</span>
              </label>
              <input
                id="rs-name" type="text" value={name}
                onChange={(e) => setName(e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
              />
            </div>
            <div className="space-y-1">
              <label htmlFor="rs-algo" className="text-sm text-gray-300">
                Algorithm <span className="text-red-400">*</span>
              </label>
              <select
                id="rs-algo" value={algorithmId}
                onChange={(e) => setAlgorithmId(e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
              >
                <option value="">— select —</option>
                {(algos.data ?? []).map((a) => (
                  <option key={a.id} value={a.id}>{a.name} ({a.id})</option>
                ))}
              </select>
            </div>
          </div>

          <div className="space-y-1">
            <label htmlFor="rs-hyp" className="text-sm text-gray-300">
              Hypothesis <span className="text-red-400">*</span>
            </label>
            <textarea
              id="rs-hyp" rows={4} value={hypothesis}
              onChange={(e) => setHypothesis(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
            />
          </div>

          <div className="space-y-1">
            <label htmlFor="rs-notes" className="text-sm text-gray-300">Notes (optional)</label>
            <textarea
              id="rs-notes" rows={2} value={notes}
              onChange={(e) => setNotes(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
            />
          </div>

          <ExperimentConfigEditor
            baseConfig={config.base_config}
            parameterSpace={config.parameter_space}
            criteria={config.pre_registered_criteria}
            onChange={setConfig}
            onValidityChange={setConfigValid}
          />

          {submitError && <div className="text-red-400 text-sm">{submitError}</div>}
        </div>
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-800 shrink-0">
          <button onClick={onClose} className="text-gray-400 hover:text-white px-3 py-1.5 rounded text-sm">Cancel</button>
          <button
            onClick={() => void handleSubmit()}
            disabled={!canSubmit}
            className="bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-4 py-2 rounded disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {mut.isPending ? "Creating…" : "Create session"}
          </button>
        </div>
      </div>
    </div>
  );
}
