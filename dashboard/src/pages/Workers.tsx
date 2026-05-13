import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Pencil, Trash2 } from "lucide-react";
import { z } from "zod";
import {
  useWorkers,
  useCreateWorker,
  useUpdateWorker,
  useDeleteWorker,
} from "../api/hooks";
import { StatusBadge } from "../components/StatusBadge";
import { FormModal } from "../components/FormModal";
import { FormField } from "../components/FormField";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useUIStore } from "../stores/ui";
import type { Worker } from "../types";

// ─── Schema ───────────────────────────────────────────────────────────────────

const workerSchema = z.object({
  name: z.string().min(1, "Name is required"),
  tailscale_ip: z.string().min(1, "Tailscale IP is required"),
  max_algorithms: z.number().int().min(1, "Must be at least 1"),
});

type WorkerFormValues = z.infer<typeof workerSchema>;

// ─── Helpers ──────────────────────────────────────────────────────────────────

function relativeTime(iso: string | null): string {
  if (!iso) return "never";
  const diffMs = Date.now() - new Date(iso).getTime();
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay}d ago`;
}

// ─── Input class ──────────────────────────────────────────────────────────────

const INPUT_CLS =
  "bg-gray-800 border border-gray-700 text-gray-100 rounded px-3 py-2 text-sm w-full";

// ─── Component ────────────────────────────────────────────────────────────────

export function Workers() {
  const navigate = useNavigate();
  const addAlert = useUIStore((s) => s.addAlert);

  const { data: workers, isLoading } = useWorkers();
  const createWorker = useCreateWorker();
  const updateWorker = useUpdateWorker();
  const deleteWorker = useDeleteWorker();

  // Register modal
  const [registerOpen, setRegisterOpen] = useState(false);

  // Edit modal
  const [editTarget, setEditTarget] = useState<Worker | null>(null);

  // Delete confirm
  const [deleteTarget, setDeleteTarget] = useState<Worker | null>(null);

  // ── Handlers ────────────────────────────────────────────────────────────────

  async function handleRegister(values: WorkerFormValues) {
    try {
      await createWorker.mutateAsync(values);
      addAlert({ message: "Worker registered.", severity: "success" });
      setRegisterOpen(false);
    } catch {
      addAlert({ message: "Failed to register worker.", severity: "error" });
    }
  }

  async function handleEdit(values: WorkerFormValues) {
    if (!editTarget) return;
    try {
      await updateWorker.mutateAsync({ id: editTarget.id, body: values });
      addAlert({ message: "Worker updated.", severity: "success" });
      setEditTarget(null);
    } catch {
      addAlert({ message: "Failed to update worker.", severity: "error" });
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    try {
      await deleteWorker.mutateAsync(deleteTarget.id);
      addAlert({ message: "Worker deleted.", severity: "success" });
      setDeleteTarget(null);
    } catch {
      addAlert({ message: "Failed to delete worker.", severity: "error" });
    }
  }

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">
          Workers{" "}
          {!isLoading && (
            <span className="text-gray-400 text-base font-normal">
              ({workers?.length ?? 0})
            </span>
          )}
        </h1>
        <button
          onClick={() => setRegisterOpen(true)}
          className="bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-4 py-2 rounded"
        >
          Register Worker
        </button>
      </div>

      {/* Worker list */}
      {isLoading ? (
        <p className="text-gray-400 text-sm">Loading…</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {(workers ?? []).map((w) => (
            <div
              key={w.id}
              onClick={() => navigate(`/workers/${w.id}`)}
              className="bg-gray-900 border border-gray-800 rounded-lg p-4 cursor-pointer hover:border-gray-600 transition-colors"
            >
              {/* Top row: name + badge + actions */}
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-sm font-medium text-gray-100 truncate">
                    {w.name}
                  </span>
                  <StatusBadge status={w.status} />
                </div>
                <div
                  className="flex items-center gap-1 shrink-0 ml-2"
                  onClick={(e) => e.stopPropagation()}
                >
                  <button
                    onClick={() => setEditTarget(w)}
                    className="p-1.5 text-gray-400 hover:text-gray-100 hover:bg-gray-700 rounded transition-colors"
                    title="Edit worker"
                  >
                    <Pencil size={14} />
                  </button>
                  <button
                    onClick={() => setDeleteTarget(w)}
                    className="p-1.5 text-gray-400 hover:text-red-400 hover:bg-gray-700 rounded transition-colors"
                    title="Delete worker"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>

              {/* Info rows */}
              <div className="space-y-1 text-xs text-gray-400">
                <div className="flex justify-between">
                  <span>Tailscale IP</span>
                  <span className="text-gray-300 font-mono">{w.tailscale_ip}</span>
                </div>
                <div className="flex justify-between">
                  <span>Max algorithms</span>
                  <span className="text-gray-300">{w.max_algorithms}</span>
                </div>
                <div className="flex justify-between">
                  <span>Last heartbeat</span>
                  <span className="text-gray-300">{relativeTime(w.last_heartbeat)}</span>
                </div>
              </div>
            </div>
          ))}
          {(workers ?? []).length === 0 && (
            <p className="text-gray-500 text-sm col-span-2">
              No workers registered.
            </p>
          )}
        </div>
      )}

      {/* Register modal */}
      <FormModal
        open={registerOpen}
        onClose={() => setRegisterOpen(false)}
        title="Register Worker"
        schema={workerSchema}
        defaultValues={{ name: "", tailscale_ip: "", max_algorithms: 2 }}
        onSubmit={handleRegister}
        submitLabel="Register"
        isSubmitting={createWorker.isPending}
      >
        {(form) => (
          <>
            <FormField label="Name" error={form.formState.errors.name?.message}>
              <input
                {...form.register("name")}
                className={INPUT_CLS}
                placeholder="e.g. worker-1"
              />
            </FormField>
            <FormField
              label="Tailscale IP"
              error={form.formState.errors.tailscale_ip?.message}
            >
              <input
                {...form.register("tailscale_ip")}
                className={INPUT_CLS}
                placeholder="e.g. 100.64.0.1"
              />
            </FormField>
            <FormField
              label="Max Algorithms"
              error={form.formState.errors.max_algorithms?.message}
            >
              <input
                type="number"
                {...form.register("max_algorithms", { valueAsNumber: true })}
                className={INPUT_CLS}
                placeholder="2"
              />
            </FormField>
          </>
        )}
      </FormModal>

      {/* Edit modal */}
      <FormModal
        open={!!editTarget}
        onClose={() => setEditTarget(null)}
        title="Edit Worker"
        schema={workerSchema}
        defaultValues={
          editTarget
            ? {
                name: editTarget.name,
                tailscale_ip: editTarget.tailscale_ip,
                max_algorithms: editTarget.max_algorithms,
              }
            : { name: "", tailscale_ip: "", max_algorithms: 2 }
        }
        onSubmit={handleEdit}
        submitLabel="Save"
        isSubmitting={updateWorker.isPending}
      >
        {(form) => (
          <>
            <FormField label="Name" error={form.formState.errors.name?.message}>
              <input
                {...form.register("name")}
                className={INPUT_CLS}
              />
            </FormField>
            <FormField
              label="Tailscale IP"
              error={form.formState.errors.tailscale_ip?.message}
            >
              <input
                {...form.register("tailscale_ip")}
                className={INPUT_CLS}
              />
            </FormField>
            <FormField
              label="Max Algorithms"
              error={form.formState.errors.max_algorithms?.message}
            >
              <input
                type="number"
                {...form.register("max_algorithms", { valueAsNumber: true })}
                className={INPUT_CLS}
              />
            </FormField>
          </>
        )}
      </FormModal>

      {/* Delete confirm */}
      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete Worker"
        message={`Delete "${deleteTarget?.name}"? This action cannot be undone.`}
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
