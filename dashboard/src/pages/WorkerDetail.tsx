import { useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { ChevronLeft, Pencil, Trash2 } from "lucide-react";
import { z } from "zod";
import {
  useWorker,
  useUpdateWorker,
  useDeleteWorker,
  useDeployments,
  useTriggerWorkerUpdate,
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
  const { data: deployments, isLoading: loadingDeployments } = useDeployments({ worker_id: id });
  const updateWorker = useUpdateWorker();
  const deleteWorker = useDeleteWorker();
  const triggerUpdate = useTriggerWorkerUpdate();

  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

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
            onClick={() =>
              triggerUpdate.mutate(worker.id, {
                onSuccess: () => {
                  addAlert({
                    message: "Update command sent — worker will pull latest code and restart.",
                    severity: "success",
                  });
                },
                onError: (err) => {
                  addAlert({
                    message: `Update failed: ${(err as Error).message}`,
                    severity: "error",
                  });
                },
              })
            }
            disabled={worker.status === "offline" || triggerUpdate.isPending}
            className="inline-flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-3 py-2 rounded transition-colors disabled:opacity-50"
          >
            {triggerUpdate.isPending ? "Updating…" : "Update"}
          </button>
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

      {/* Running Algorithms */}
      <section>
        <h2 className="text-sm font-semibold text-gray-400 uppercase mb-3">
          Running Algorithms
        </h2>
        {loadingDeployments ? (
          <p className="text-gray-400 text-sm">Loading…</p>
        ) : deployments && deployments.length > 0 ? (
          <div className="space-y-2">
            {deployments.map((d) => {
              const pnl = (d.lifetime_metrics as { total_pnl?: number } | null)?.total_pnl ?? null;
              const pnlClass =
                pnl == null ? "text-gray-400" : pnl >= 0 ? "text-green-400" : "text-red-400";
              return (
                <div
                  key={d.id}
                  onClick={() => navigate(`/deployments/${d.id}`)}
                  className="bg-gray-900 border border-gray-800 rounded-lg p-4 cursor-pointer hover:border-gray-600 transition-colors"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0 flex items-center gap-3">
                      <StatusBadge status={d.status} />
                      <Link
                        to={`/algorithms/${d.algorithm_id}`}
                        className="text-indigo-400 hover:underline text-sm font-medium"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {d.algorithm_name}
                      </Link>
                      <span className="text-gray-500">·</span>
                      <Link
                        to={`/accounts/${d.account_id}`}
                        className="text-gray-300 hover:text-gray-100 text-sm"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {d.account_name}
                      </Link>
                    </div>
                    <div className={`text-sm font-mono ${pnlClass}`}>
                      {pnl == null
                        ? "—"
                        : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(pnl)}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="text-gray-500 text-sm">No algorithms deployed to this worker.</p>
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
