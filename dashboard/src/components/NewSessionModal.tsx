import { useState } from "react";
import { X } from "lucide-react";
import { JsonTextField } from "./JsonTextField";
import { useCreateResearchSession } from "../hooks/useResearchMutations";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (sessionId: number) => void;
}

export function NewSessionModal({ open, onClose, onCreated }: Props) {
  const [name, setName] = useState("");
  const [hypothesis, setHypothesis] = useState("");
  const [paramSpace, setParamSpace] = useState<Record<string, unknown> | null>(null);
  const [criteria, setCriteria] = useState<Record<string, unknown> | null>(null);
  const [notes, setNotes] = useState("");
  const [submitError, setSubmitError] = useState<string | null>(null);

  const mut = useCreateResearchSession();

  if (!open) return null;

  const canSubmit =
    name.trim().length > 0 &&
    hypothesis.trim().length > 0 &&
    paramSpace !== null &&
    criteria !== null &&
    !mut.isPending;

  const handleSubmit = async () => {
    setSubmitError(null);
    try {
      const session = await mut.mutateAsync({
        name: name.trim(),
        hypothesis: hypothesis.trim(),
        parameter_space: paramSpace!,
        pre_registered_criteria: criteria!,
        notes: notes.trim(),
      });
      onCreated(session.id);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to create session";
      setSubmitError(msg);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70" onClick={onClose} aria-hidden="true" />
      <div className="relative z-10 bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-2xl mx-auto flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800 shrink-0">
          <h2 className="text-xl font-bold text-white">New Research Session</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-white">
            <X size={20} />
          </button>
        </div>
        <div className="overflow-auto px-6 py-4 space-y-4">
          <div className="space-y-1">
            <label htmlFor="rs-name" className="text-sm text-gray-300">
              Name <span className="text-red-400">*</span>
            </label>
            <input
              id="rs-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="rs-hyp" className="text-sm text-gray-300">
              Hypothesis <span className="text-red-400">*</span>
            </label>
            <textarea
              id="rs-hyp"
              rows={4}
              value={hypothesis}
              onChange={(e) => setHypothesis(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
            />
          </div>
          <JsonTextField
            label="Parameter space"
            value={paramSpace}
            onChange={setParamSpace}
            required
            placeholder='{"vol_target": [0.10, 0.15, 0.20]}'
          />
          <JsonTextField
            label="Pre-registered criteria"
            value={criteria}
            onChange={setCriteria}
            required
            placeholder='{"min_sharpe": 1.0, "max_drawdown": 0.20}'
          />
          <div className="space-y-1">
            <label htmlFor="rs-notes" className="text-sm text-gray-300">Notes (optional)</label>
            <textarea
              id="rs-notes"
              rows={3}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
            />
          </div>
          {submitError && (
            <div className="text-red-400 text-sm">{submitError}</div>
          )}
        </div>
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-800 shrink-0">
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white px-3 py-1.5 rounded text-sm"
          >
            Cancel
          </button>
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
