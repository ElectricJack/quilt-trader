import { useState } from "react";
import { useCreateBacktestRun } from "../api/hooks";
import { useUIStore } from "../stores/ui";
import type { ParameterSet } from "../types";

// ── Spec D Phase 5: batch backtest all parameter sets ──

interface Props {
  open: boolean;
  onClose: () => void;
  algorithmId: string;
  parameterSets: ParameterSet[];
}

export function BacktestAllModal({ open, onClose, algorithmId, parameterSets }: Props) {
  const addAlert = useUIStore((s) => s.addAlert);
  const create = useCreateBacktestRun();

  const _now = new Date();
  const _utcDateStr = (d: Date) =>
    `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
  const _defaultEnd = new Date(Date.UTC(_now.getUTCFullYear(), _now.getUTCMonth(), _now.getUTCDate() - 2));
  const _defaultStart = new Date(Date.UTC(_defaultEnd.getUTCFullYear() - 1, _defaultEnd.getUTCMonth(), _defaultEnd.getUTCDate()));

  const [start, setStart] = useState(_utcDateStr(_defaultStart));
  const [end, setEnd] = useState(_utcDateStr(_defaultEnd));
  const [cash, setCash] = useState(100_000);
  const [progress, setProgress] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);

  if (!open) return null;

  async function handleSubmit() {
    if (parameterSets.length === 0) return;
    setIsRunning(true);
    let succeeded = 0;
    let failed = 0;
    for (let i = 0; i < parameterSets.length; i++) {
      const ps = parameterSets[i];
      setProgress(`Running ${i + 1} of ${parameterSets.length}: "${ps.name}"…`);
      try {
        await create.mutateAsync({
          algorithm_id: algorithmId,
          date_range_start: new Date(start).toISOString(),
          date_range_end: new Date(end).toISOString(),
          initial_cash: cash,
          config_overrides: Object.keys(ps.config_values).length > 0 ? ps.config_values : undefined,
          parameter_set_id: ps.id,
        });
        succeeded++;
      } catch {
        failed++;
      }
    }
    setIsRunning(false);
    setProgress(null);
    const msg =
      failed === 0
        ? `Queued ${succeeded} backtest run${succeeded !== 1 ? "s" : ""}.`
        : `Queued ${succeeded} run${succeeded !== 1 ? "s" : ""}, ${failed} failed.`;
    addAlert({ message: msg, severity: failed === 0 ? "success" : "error" });
    onClose();
  }

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
      <div className="bg-gray-900 border border-gray-700 rounded-lg p-5 w-full max-w-md space-y-3">
        <h2 className="text-lg font-semibold">Backtest All Parameter Sets</h2>
        <p className="text-sm text-gray-400">
          Queue a backtest run for each of the {parameterSets.length} parameter set{parameterSets.length !== 1 ? "s" : ""}.
        </p>

        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm">
            Start date
            <input
              type="date"
              value={start}
              onChange={(e) => setStart(e.target.value)}
              className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1"
            />
          </label>
          <label className="text-sm">
            End date
            <input
              type="date"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
              className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1"
            />
          </label>
        </div>

        <label className="text-sm block">
          Initial cash
          <input
            type="number"
            value={cash}
            onChange={(e) => setCash(Number(e.target.value))}
            className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1"
          />
        </label>

        {progress && (
          <p className="text-xs text-indigo-400">{progress}</p>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button
            onClick={onClose}
            disabled={isRunning}
            className="px-3 py-1.5 rounded text-sm text-gray-300 bg-gray-700 hover:bg-gray-600 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={() => void handleSubmit()}
            disabled={isRunning || parameterSets.length === 0}
            className="px-3 py-1.5 rounded text-sm text-white bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50"
          >
            {isRunning ? "Running…" : `Run All (${parameterSets.length})`}
          </button>
        </div>
      </div>
    </div>
  );
}
