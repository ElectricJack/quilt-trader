import { useState, useMemo } from "react";
import { X } from "lucide-react";
import { useCreateCpcvJob } from "../api/hooks";
import { useUIStore } from "../stores/ui";
import { JsonTextField } from "./JsonTextField";
import type { CPCVRequest } from "../types/index";

interface Props {
  open: boolean;
  sessionId: number;
  onClose: () => void;
}

function projectedBacktests(req: CPCVRequest): number {
  const N = req.n_groups, k = req.test_groups_per_split;
  if (req.mode === "fixed") return N;
  const fact = (n: number, r: number) => {
    if (r > n) return 0;
    let num = 1;
    for (let i = 0; i < r; i++) num *= (n - i);
    let den = 1;
    for (let i = 2; i <= r; i++) den *= i;
    return num / den;
  };
  const C = fact(N, k);
  return C * ((req.max_trials_per_split ?? 0) + k);
}

export function NewCpcvModal({ open, sessionId, onClose }: Props) {
  const mut = useCreateCpcvJob();
  const addAlert = useUIStore((s) => s.addAlert);
  const [mode, setMode] = useState<"fixed" | "select">("fixed");
  const [nGroups, setNGroups] = useState(6);
  const [k, setK] = useState(2);
  const [embargo, setEmbargo] = useState(5);
  const [purge, setPurge] = useState(0);
  const [search, setSearch] = useState<"grid" | "random" | "latin" | "tpe">("random");
  const [maxTrials, setMaxTrials] = useState(20);
  const [paramSpace, setParamSpace] = useState<Record<string, unknown> | null>({});
  const [paramSpaceValid, setParamSpaceValid] = useState(true);

  const req: CPCVRequest = useMemo(() => {
    const r: CPCVRequest = {
      mode, n_groups: nGroups, test_groups_per_split: k,
      embargo, purge_horizon: purge,
    };
    if (mode === "select") {
      r.search = search;
      r.max_trials_per_split = maxTrials;
      r.parameter_space = paramSpace ?? {};
    }
    return r;
  }, [mode, nGroups, k, embargo, purge, search, maxTrials, paramSpace]);

  const projected = projectedBacktests(req);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70" onClick={onClose} aria-hidden="true" />
      <div className="relative z-10 bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-lg mx-auto flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800 shrink-0">
          <h2 className="text-xl font-bold text-white">New CPCV job</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-white"><X size={20} /></button>
        </div>
        <div className="px-6 py-4 space-y-4">
          <fieldset className="flex gap-6">
            <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
              <input
                type="radio"
                name="cpcv-mode"
                checked={mode === "fixed"}
                onChange={() => setMode("fixed")}
                className="accent-indigo-500"
              />
              Fixed
            </label>
            <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
              <input
                type="radio"
                name="cpcv-mode"
                checked={mode === "select"}
                onChange={() => setMode("select")}
                className="accent-indigo-500"
              />
              Select
            </label>
          </fieldset>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <label htmlFor="cpcv-n-groups" className="text-sm text-gray-300">N groups</label>
              <input
                id="cpcv-n-groups"
                type="number"
                min={2}
                value={nGroups}
                onChange={e => setNGroups(+e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
              />
            </div>
            <div className="space-y-1">
              <label htmlFor="cpcv-test-groups" className="text-sm text-gray-300">Test groups</label>
              <input
                id="cpcv-test-groups"
                type="number"
                min={1}
                value={k}
                onChange={e => setK(+e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
              />
            </div>
            <div className="space-y-1">
              <label htmlFor="cpcv-embargo" className="text-sm text-gray-300">Embargo (bars)</label>
              <input
                id="cpcv-embargo"
                type="number"
                min={0}
                value={embargo}
                onChange={e => setEmbargo(+e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
              />
            </div>
            <div className="space-y-1">
              <label htmlFor="cpcv-purge" className="text-sm text-gray-300">Purge horizon (bars)</label>
              <input
                id="cpcv-purge"
                type="number"
                min={0}
                value={purge}
                onChange={e => setPurge(+e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
              />
            </div>
          </div>
          {mode === "select" && (
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <label htmlFor="cpcv-search" className="text-sm text-gray-300">Search</label>
                <select
                  id="cpcv-search"
                  value={search}
                  onChange={e => setSearch(e.target.value as typeof search)}
                  className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
                >
                  <option value="grid">grid</option>
                  <option value="random">random</option>
                  <option value="latin">latin</option>
                  <option value="tpe">tpe</option>
                </select>
              </div>
              <div className="space-y-1">
                <label htmlFor="cpcv-max-trials" className="text-sm text-gray-300">Max trials</label>
                <input
                  id="cpcv-max-trials"
                  type="number"
                  min={1}
                  value={maxTrials}
                  onChange={e => setMaxTrials(+e.target.value)}
                  className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full"
                />
              </div>
              <div className="col-span-2">
                <JsonTextField
                  label="Parameter space (JSON)"
                  value={paramSpace}
                  onChange={setParamSpace}
                  onError={(hasErr) => setParamSpaceValid(!hasErr)}
                  rows={4}
                  placeholder='{"lookback": [20, 50, 100]}'
                />
              </div>
            </div>
          )}
          <div className="text-xs text-gray-400">
            {`projected ${projected} backtests`}
          </div>
        </div>
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-800 shrink-0">
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white px-3 py-1.5 rounded text-sm"
          >
            Cancel
          </button>
          <button
            onClick={async () => {
              try {
                await mut.mutateAsync({ sessionId, body: req });
                addAlert({
                  message: `CPCV job queued (${projected} projected backtests).`,
                  severity: "success",
                });
                onClose();
              } catch (e) {
                addAlert({
                  message: `Failed to queue CPCV job: ${e instanceof Error ? e.message : "unknown error"}`,
                  severity: "error",
                });
              }
            }}
            disabled={mut.isPending || (mode === "select" && !paramSpaceValid)}
            className="bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-4 py-2 rounded disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {mut.isPending ? "Queuing…" : "Submit"}
          </button>
        </div>
      </div>
    </div>
  );
}
