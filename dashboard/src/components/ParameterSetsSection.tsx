import { useState, useRef } from "react";
import { Trash2 } from "lucide-react";
import {
  useParameterSets,
  useCreateParameterSet,
  useDeleteParameterSet,
  useImportParameterSets,
} from "../api/hooks";
import { api } from "../api/client";
import { useUIStore } from "../stores/ui";
import { ConfirmDialog } from "./ConfirmDialog";
import type { ParameterSet } from "../types";

// ─── Props ────────────────────────────────────────────────────────────────────

interface Props {
  algorithmId: string;
  manifestConfig: Array<{ name: string; type: string; default?: unknown }>;
  onBacktest: (setId: string) => void;
  onDeploy: () => void;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatParams(config_values: Record<string, unknown>): string {
  const entries = Object.entries(config_values);
  if (entries.length === 0) return "—";
  if (entries.length >= 3) {
    return entries.map(([, v]) => String(v)).join(" / ");
  }
  return entries.map(([k, v]) => `${k}=${String(v)}`).join(", ");
}

function fmtPct(val: number | null | undefined, decimals = 1): string {
  if (val == null) return "—";
  return `${(val * 100).toFixed(decimals)}%`;
}

function fmtNum(val: number | null | undefined, decimals = 2): string {
  if (val == null) return "—";
  return val.toFixed(decimals);
}

// ─── Create Modal ─────────────────────────────────────────────────────────────

interface CreateModalProps {
  open: boolean;
  onClose: () => void;
  algorithmId: string;
  manifestConfig: Array<{ name: string; type: string; default?: unknown }>;
}

function CreateModal({ open, onClose, algorithmId, manifestConfig }: CreateModalProps) {
  const addAlert = useUIStore((s) => s.addAlert);
  const { mutateAsync: createParameterSet, isPending } = useCreateParameterSet(algorithmId);

  const [name, setName] = useState("");
  const [fieldValues, setFieldValues] = useState<Record<string, unknown>>(
    Object.fromEntries(manifestConfig.map((p) => [p.name, p.default ?? ""]))
  );

  if (!open) return null;

  function setField(key: string, value: unknown) {
    setFieldValues((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSave() {
    if (!name.trim()) {
      addAlert({ message: "Name is required.", severity: "error" });
      return;
    }
    try {
      await createParameterSet({ name: name.trim(), config_values: fieldValues });
      addAlert({ message: `Parameter set "${name.trim()}" created.`, severity: "success" });
      setName("");
      setFieldValues(Object.fromEntries(manifestConfig.map((p) => [p.name, p.default ?? ""])));
      onClose();
    } catch {
      addAlert({ message: "Failed to create parameter set.", severity: "error" });
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} aria-hidden="true" />
      <div className="relative z-10 bg-gray-900 border border-gray-700 rounded-lg shadow-xl p-6 w-full max-w-md mx-4 space-y-4">
        <h2 className="text-base font-semibold text-white">New Parameter Set</h2>

        <div className="space-y-1">
          <label className="block text-xs text-gray-400 uppercase">Name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="bg-gray-800 border border-gray-700 text-gray-100 rounded px-3 py-2 text-sm w-full"
            placeholder="e.g. Aggressive"
          />
        </div>

        {manifestConfig.map((param) => {
          const isNumeric = param.type === "int" || param.type === "float";
          return (
            <div key={param.name} className="space-y-1">
              <label className="block text-xs text-gray-400 uppercase">{param.name}</label>
              <input
                type={isNumeric ? "number" : "text"}
                value={String(fieldValues[param.name] ?? "")}
                onChange={(e) => {
                  const raw = e.target.value;
                  if (isNumeric) {
                    setField(param.name, param.type === "int" ? parseInt(raw, 10) : parseFloat(raw));
                  } else {
                    setField(param.name, raw);
                  }
                }}
                className="bg-gray-800 border border-gray-700 text-gray-100 rounded px-3 py-2 text-sm w-full"
                placeholder={param.default != null ? String(param.default) : ""}
              />
            </div>
          );
        })}

        <div className="flex justify-end gap-3 pt-2">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded text-sm font-medium text-gray-300 bg-gray-700 hover:bg-gray-600 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => void handleSave()}
            disabled={isPending}
            className="px-4 py-2 rounded text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {isPending ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Row ─────────────────────────────────────────────────────────────────────

interface RowProps {
  ps: ParameterSet;
  isBest: boolean;
  onDeleteClick: () => void;
  onBacktest: (setId: string) => void;
  onDeploy: () => void;
}

function ParameterSetRow({ ps, isBest, onDeleteClick, onBacktest, onDeploy }: RowProps) {
  const bb = ps.best_backtest;
  const sharpe = bb?.sharpe_ratio ?? null;
  const ret = bb?.total_return ?? null;
  const dd = bb?.max_drawdown ?? null;
  const runs = bb?.run_count ?? 0;

  return (
    <tr className={isBest ? "bg-emerald-950/30" : undefined}>
      <td className="px-3 py-2 text-indigo-400 font-mono text-xs whitespace-nowrap">
        {ps.id.slice(0, 8)}
      </td>
      <td className="px-3 py-2 text-sm text-gray-200 whitespace-nowrap">{ps.name}</td>
      <td className="px-3 py-2 text-xs text-gray-400 max-w-[220px] truncate">
        {formatParams(ps.config_values)}
      </td>
      <td className={`px-3 py-2 text-xs font-mono whitespace-nowrap ${sharpe != null && sharpe > 0 ? "text-green-400" : "text-gray-400"}`}>
        {fmtNum(sharpe)}
      </td>
      <td className={`px-3 py-2 text-xs font-mono whitespace-nowrap ${ret != null && ret >= 0 ? "text-green-400" : ret != null ? "text-red-400" : "text-gray-400"}`}>
        {fmtPct(ret)}
      </td>
      <td className="px-3 py-2 text-xs font-mono whitespace-nowrap text-red-400/80">
        {dd != null ? fmtPct(dd) : "—"}
      </td>
      <td className="px-3 py-2 text-xs text-gray-500 whitespace-nowrap">{runs}</td>
      <td className="px-3 py-2 whitespace-nowrap">
        <div className="flex items-center gap-3">
          <button
            onClick={() => onBacktest(ps.id)}
            className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
          >
            Backtest
          </button>
          <button
            onClick={onDeploy}
            className="text-xs text-gray-400 hover:text-gray-200 transition-colors"
          >
            Deploy
          </button>
          <button
            onClick={onDeleteClick}
            className="text-red-500 hover:text-red-400 transition-colors"
            title="Delete parameter set"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </td>
    </tr>
  );
}

// ─── Main Component ───────────────────────────────────────────────────────────

export function ParameterSetsSection({ algorithmId, manifestConfig, onBacktest, onDeploy }: Props) {
  const addAlert = useUIStore((s) => s.addAlert);
  const { data: parameterSets = [] } = useParameterSets(algorithmId);
  const { mutateAsync: deleteParameterSet } = useDeleteParameterSet(algorithmId);
  const { mutateAsync: importParameterSets } = useImportParameterSets(algorithmId);

  const [createOpen, setCreateOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── Export ────────────────────────────────────────────────────────────────

  async function handleExport() {
    try {
      const blob = await api.exportParameterSets(algorithmId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "parameter-sets.json";
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      addAlert({ message: "Export failed.", severity: "error" });
    }
  }

  // ── Import ────────────────────────────────────────────────────────────────

  function handleImportClick() {
    fileInputRef.current?.click();
  }

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const parsed = JSON.parse(text) as unknown;
      const sets = Array.isArray(parsed)
        ? (parsed as Array<{ name: string; config_values: Record<string, unknown> }>)
        : [];
      const result = await importParameterSets(sets);
      addAlert({
        message: `Imported ${result.imported} set(s)${result.skipped > 0 ? `, skipped ${result.skipped}` : ""}.`,
        severity: "success",
      });
    } catch {
      addAlert({ message: "Import failed. Make sure the file is valid JSON.", severity: "error" });
    } finally {
      // Reset so the same file can be re-imported if needed
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  // ── Delete ────────────────────────────────────────────────────────────────

  async function handleDelete() {
    if (!deleteTarget) return;
    try {
      await deleteParameterSet(deleteTarget);
      addAlert({ message: "Parameter set deleted.", severity: "success" });
    } catch {
      addAlert({ message: "Failed to delete parameter set.", severity: "error" });
    } finally {
      setDeleteTarget(null);
    }
  }

  // ─── Render ───────────────────────────────────────────────────────────────

  return (
    <section>
      {/* Header */}
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h2 className="text-lg font-semibold text-gray-200">Parameter Sets</h2>
        <div className="flex items-center gap-2">
          <button
            onClick={handleImportClick}
            className="px-3 py-1.5 rounded text-xs font-medium text-gray-300 bg-gray-700 hover:bg-gray-600 transition-colors"
          >
            Import
          </button>
          <button
            onClick={() => void handleExport()}
            className="px-3 py-1.5 rounded text-xs font-medium text-gray-300 bg-gray-700 hover:bg-gray-600 transition-colors"
          >
            Export
          </button>
          <button
            onClick={() => setCreateOpen(true)}
            className="px-3 py-1.5 rounded text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-500 transition-colors"
          >
            + New Set
          </button>
        </div>
      </div>

      {/* Hidden file input for import */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".json"
        className="hidden"
        onChange={(e) => void handleFileChange(e)}
      />

      {/* Table or empty state */}
      <div className="bg-gray-900 border border-gray-800 rounded overflow-x-auto">
        {parameterSets.length === 0 ? (
          <p className="text-sm text-gray-400 p-6 text-center">
            No parameter sets defined. Create one to start tuning.
          </p>
        ) : (
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-gray-800">
                <th className="px-3 py-2 text-xs text-gray-500 uppercase font-medium">ID</th>
                <th className="px-3 py-2 text-xs text-gray-500 uppercase font-medium">Name</th>
                <th className="px-3 py-2 text-xs text-gray-500 uppercase font-medium">Parameters</th>
                <th className="px-3 py-2 text-xs text-gray-500 uppercase font-medium">Sharpe</th>
                <th className="px-3 py-2 text-xs text-gray-500 uppercase font-medium">Return</th>
                <th className="px-3 py-2 text-xs text-gray-500 uppercase font-medium">Max DD</th>
                <th className="px-3 py-2 text-xs text-gray-500 uppercase font-medium">Runs</th>
                <th className="px-3 py-2 text-xs text-gray-500 uppercase font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {parameterSets.map((ps, i) => (
                <ParameterSetRow
                  key={ps.id}
                  ps={ps}
                  isBest={i === 0}
                  onDeleteClick={() => setDeleteTarget(ps.id)}
                  onBacktest={onBacktest}
                  onDeploy={onDeploy}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Create modal */}
      <CreateModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        algorithmId={algorithmId}
        manifestConfig={manifestConfig}
      />

      {/* Delete confirm */}
      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete Parameter Set"
        message="Are you sure you want to delete this parameter set? This cannot be undone."
        confirmLabel="Delete"
        onConfirm={() => void handleDelete()}
        onCancel={() => setDeleteTarget(null)}
      />
    </section>
  );
}
