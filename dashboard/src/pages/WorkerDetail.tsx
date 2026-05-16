import { useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { ChevronLeft, Pencil, Trash2 } from "lucide-react";
import { z } from "zod";
import {
  useWorker,
  useUpdateWorker,
  useDeleteWorker,
  useAllInstances,
} from "../api/hooks";
import { StatusBadge } from "../components/StatusBadge";
import { FormModal } from "../components/FormModal";
import { FormField } from "../components/FormField";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { WorkerInstallCommand } from "../components/WorkerInstallCommand";
import { ActivityPanel } from "../components/ActivityPanel";
import { useUIStore } from "../stores/ui";

// ─── Schema ───────────────────────────────────────────────────────────────────

const workerSchema = z.object({
  name: z.string().min(1, "Name is required"),
  tailscale_ip: z.string().min(1, "Tailscale IP is required"),
  max_algorithms: z.number().int().min(1, "Must be at least 1"),
});

type WorkerFormValues = z.infer<typeof workerSchema>;

// ─── Helpers ──────────────────────────────────────────────────────────────────

const INPUT_CLS =
  "bg-gray-800 border border-gray-700 text-gray-100 rounded px-3 py-2 text-sm w-full";

function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

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

// ─── Info card ────────────────────────────────────────────────────────────────

function InfoCard({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-3">
      <p className="text-xs text-gray-500 uppercase mb-1">{label}</p>
      <div className="text-sm text-gray-200">{value}</div>
    </div>
  );
}

// ─── Component ────────────────────────────────────────────────────────────────

export function WorkerDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const addAlert = useUIStore((s) => s.addAlert);

  const { data: worker, isLoading } = useWorker(id ?? "");
  const { data: allInstances, isLoading: loadingInstances } = useAllInstances();
  const updateWorker = useUpdateWorker();
  const deleteWorker = useDeleteWorker();

  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  // Filter instances assigned to this worker
  const instances = (allInstances ?? []).filter((i) => i.worker_id === id);

  // ── Handlers ────────────────────────────────────────────────────────────────

  async function handleEdit(values: WorkerFormValues) {
    if (!id) return;
    try {
      await updateWorker.mutateAsync({ id, body: values });
      addAlert({ message: "Worker updated.", severity: "success" });
      setEditOpen(false);
    } catch {
      addAlert({ message: "Failed to update worker.", severity: "error" });
    }
  }

  async function handleDelete() {
    if (!id) return;
    try {
      await deleteWorker.mutateAsync(id);
      addAlert({ message: "Worker deleted.", severity: "success" });
      navigate("/workers");
    } catch {
      addAlert({ message: "Failed to delete worker.", severity: "error" });
      setDeleteOpen(false);
    }
  }

  // ── Loading / not found ─────────────────────────────────────────────────────

  if (isLoading) {
    return <p className="text-gray-400 text-sm">Loading…</p>;
  }

  if (!worker) {
    return <p className="text-gray-400 text-sm">Worker not found.</p>;
  }

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <Link
            to="/workers"
            className="inline-flex items-center gap-1 text-xs text-gray-400 hover:text-gray-200 transition-colors"
          >
            <ChevronLeft size={14} />
            Workers
          </Link>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-white">{worker.name}</h1>
            <StatusBadge status={worker.status} />
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0 mt-1">
          <button
            onClick={() => setEditOpen(true)}
            className="inline-flex items-center gap-1.5 bg-gray-700 hover:bg-gray-600 text-gray-200 text-sm px-3 py-2 rounded transition-colors"
          >
            <Pencil size={14} />
            Edit
          </button>
          <button
            onClick={() => setDeleteOpen(true)}
            className="inline-flex items-center gap-1.5 bg-red-800 hover:bg-red-700 text-red-100 text-sm px-3 py-2 rounded transition-colors"
          >
            <Trash2 size={14} />
            Delete
          </button>
        </div>
      </div>

      {/* Install command when worker hasn't claimed itself yet */}
      {worker.install_status === "pending" && (
        <WorkerInstallCommand workerId={worker.id} workerName={worker.name} />
      )}

      {/* Info cards */}
      <section>
        <h2 className="text-sm font-semibold text-gray-400 uppercase mb-3">
          Worker Info
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          <InfoCard label="Tailscale IP" value={
            <span className="font-mono">{worker.tailscale_ip}</span>
          } />
          <InfoCard label="Status" value={<StatusBadge status={worker.status} />} />
          <InfoCard label="Max Algorithms" value={worker.max_algorithms} />
          <InfoCard
            label="Last Heartbeat"
            value={
              <span title={worker.last_heartbeat ?? undefined}>
                {relativeTime(worker.last_heartbeat)}
              </span>
            }
          />
          <InfoCard label="Created" value={formatDateTime(worker.created_at)} />
        </div>
      </section>

      {/* Assigned instances */}
      <section>
        <h2 className="text-sm font-semibold text-gray-400 uppercase mb-3">
          Assigned Instances
        </h2>
        {loadingInstances ? (
          <p className="text-gray-400 text-sm">Loading…</p>
        ) : instances.length === 0 ? (
          <p className="text-gray-500 text-sm">
            No instances assigned to this worker.
          </p>
        ) : (
          <div className="space-y-2">
            {instances.map((inst) => (
              <div
                key={inst.id}
                onClick={() => navigate(`/instances/${inst.id}`)}
                className="bg-gray-900 border border-gray-800 rounded-lg p-4 cursor-pointer hover:border-gray-600 transition-colors"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="space-y-1 min-w-0">
                    <p className="text-xs text-gray-500 uppercase">Instance</p>
                    <p className="text-sm font-mono text-gray-200 truncate">
                      {inst.id}
                    </p>
                  </div>
                  <StatusBadge status={inst.status} />
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 mt-3 text-xs text-gray-400">
                  <div className="flex justify-between">
                    <span>Algorithm</span>
                    <span className="text-gray-300 font-mono truncate ml-2">
                      {inst.algorithm_id}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span>Account</span>
                    <span className="text-gray-300 font-mono truncate ml-2">
                      {inst.account_id}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Activity */}
      <section>
        <h2 className="text-sm font-semibold text-gray-400 uppercase mb-3">Activity</h2>
        <ActivityPanel target={`worker:${id ?? ""}` as const} />
      </section>

      {/* Edit modal */}
      <FormModal
        open={editOpen}
        onClose={() => setEditOpen(false)}
        title="Edit Worker"
        schema={workerSchema}
        defaultValues={{
          name: worker.name,
          tailscale_ip: worker.tailscale_ip,
          max_algorithms: worker.max_algorithms,
        }}
        onSubmit={handleEdit}
        submitLabel="Save"
        isSubmitting={updateWorker.isPending}
      >
        {(form) => (
          <>
            <FormField label="Name" error={form.formState.errors.name?.message}>
              <input {...form.register("name")} className={INPUT_CLS} />
            </FormField>
            <FormField
              label="Tailscale IP"
              error={form.formState.errors.tailscale_ip?.message}
            >
              <input {...form.register("tailscale_ip")} className={INPUT_CLS} />
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
        open={deleteOpen}
        title="Delete Worker"
        message={`Delete "${worker.name}"? This action cannot be undone.`}
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setDeleteOpen(false)}
      />
    </div>
  );
}
