import { useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { ChevronLeft } from "lucide-react";
import { z } from "zod";
import {
  useAlgorithm,
  useInstances,
  useDeleteAlgorithm,
  useCreateInstance,
  useAccounts,
  useWorkers,
} from "../api/hooks";
import type { AlgorithmInstance } from "../types";
import { StatusBadge } from "../components/StatusBadge";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { FormModal } from "../components/FormModal";
import { RunBacktestModal } from "../components/RunBacktestModal";
import { FormField } from "../components/FormField";
import { DataTable } from "../components/DataTable";
import type { ColumnDef } from "../components/DataTable";
import { useUIStore } from "../stores/ui";

// ─── Create Instance schema ────────────────────────────────────────────────────

const createInstanceSchema = z.object({
  account_id: z.string().min(1),
  worker_id: z.string().min(1),
  config_values: z.string().optional(),
});
type CreateInstanceForm = z.infer<typeof createInstanceSchema>;

// ─── Instances table columns ───────────────────────────────────────────────────

function buildColumns(): ColumnDef<AlgorithmInstance, unknown>[] {
  return [
    {
      id: "id",
      header: "ID",
      accessorKey: "id",
      cell: ({ row }) => (
        <Link
          to={`/instances/${row.original.id}`}
          className="text-indigo-400 hover:underline font-mono text-xs"
          onClick={(e) => e.stopPropagation()}
        >
          {row.original.id.slice(0, 8)}…
        </Link>
      ),
    },
    {
      id: "status",
      header: "Status",
      accessorKey: "status",
      cell: ({ row }) => <StatusBadge status={row.original.status} />,
    },
    {
      id: "account_id",
      header: "Account",
      accessorKey: "account_id",
      cell: ({ row }) => (
        <Link
          to={`/accounts/${row.original.account_id}`}
          className="text-indigo-400 hover:underline text-xs"
          onClick={(e) => e.stopPropagation()}
        >
          {row.original.account_id.slice(0, 8)}…
        </Link>
      ),
    },
    {
      id: "worker_id",
      header: "Worker",
      accessorKey: "worker_id",
      cell: ({ row }) => (
        <span className="text-xs text-gray-400">
          {row.original.worker_id.slice(0, 8)}…
        </span>
      ),
    },
    {
      id: "created_at",
      header: "Created",
      accessorKey: "created_at",
      cell: ({ row }) => (
        <span className="text-xs text-gray-400">
          {new Date(row.original.created_at).toLocaleString()}
        </span>
      ),
    },
  ];
}

const instanceColumns = buildColumns();

// ─── Component ────────────────────────────────────────────────────────────────

export function AlgorithmDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const addAlert = useUIStore((s) => s.addAlert);

  const { data: algorithm, isLoading: loadingAlgo } = useAlgorithm(id ?? "");
  const { data: instances, isLoading: loadingInstances } = useInstances(
    id ?? ""
  );
  const { data: accounts } = useAccounts();
  const { data: workers } = useWorkers();

  const { mutateAsync: deleteAlgorithm } = useDeleteAlgorithm();
  const { mutateAsync: createInstance, isPending: isCreating } =
    useCreateInstance(id ?? "");

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [backtestOpen, setBacktestOpen] = useState(false);

  // ── Delete algorithm ───────────────────────────────────────────────────────

  async function handleDelete() {
    if (!id) return;
    try {
      await deleteAlgorithm(id);
      addAlert({
        message: `Deleted algorithm "${algorithm?.name}".`,
        severity: "success",
      });
      navigate("/algorithms");
    } catch {
      addAlert({ message: "Failed to delete algorithm.", severity: "error" });
      setDeleteOpen(false);
    }
  }

  // ── Create instance ────────────────────────────────────────────────────────

  async function handleCreateInstance(data: CreateInstanceForm) {
    let config_values: Record<string, unknown> | undefined;
    if (data.config_values && data.config_values.trim() !== "") {
      try {
        config_values = JSON.parse(data.config_values) as Record<
          string,
          unknown
        >;
      } catch {
        addAlert({
          message: "Config values is not valid JSON.",
          severity: "error",
        });
        return;
      }
    }
    try {
      await createInstance({
        account_id: data.account_id,
        worker_id: data.worker_id,
        config_values,
      });
      addAlert({ message: "Instance created.", severity: "success" });
      setCreateOpen(false);
    } catch {
      addAlert({ message: "Failed to create instance.", severity: "error" });
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  if (loadingAlgo) {
    return <p className="text-gray-400 text-sm">Loading…</p>;
  }

  if (!algorithm) {
    return <p className="text-gray-400 text-sm">Algorithm not found.</p>;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <Link
          to="/algorithms"
          className="flex items-center gap-1 text-gray-400 hover:text-gray-200 transition-colors text-sm"
        >
          <ChevronLeft className="w-4 h-4" />
          Algorithms
        </Link>

        <div className="flex items-center gap-3 flex-1 min-w-0">
          <h1 className="text-2xl font-bold text-white truncate">
            {algorithm.name}
          </h1>
          {algorithm.version && (
            <span className="text-gray-400 text-base shrink-0">
              v{algorithm.version}
            </span>
          )}
          <StatusBadge status={algorithm.install_status} />
        </div>

        <button
          onClick={() => setBacktestOpen(true)}
          className="ml-auto flex items-center gap-1.5 px-3 py-2 rounded text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 shrink-0"
        >
          Run Backtest
        </button>

        <button
          onClick={() => setDeleteOpen(true)}
          className="px-3 py-2 rounded text-sm font-medium text-red-400 bg-red-950 border border-red-900 hover:bg-red-900 hover:border-red-800 transition-colors shrink-0"
        >
          Delete Algorithm
        </button>
      </div>

      {/* Details */}
      <div className="bg-gray-900 border border-gray-800 rounded p-4 space-y-3">
        <h2 className="text-lg font-semibold text-gray-200">Details</h2>
        {algorithm.description && (
          <p className="text-sm text-gray-300">{algorithm.description}</p>
        )}
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-xs text-gray-500 uppercase">Repository</span>
            <p className="text-gray-200 mt-0.5 break-all">
              <a
                href={algorithm.repo_url}
                target="_blank"
                rel="noreferrer"
                className="text-indigo-400 hover:underline"
              >
                {algorithm.repo_url}
              </a>
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Commit</span>
            <p className="text-gray-200 mt-0.5 font-mono text-xs">
              {algorithm.commit_hash
                ? algorithm.commit_hash.slice(0, 8)
                : "—"}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">
              Required Assets
            </span>
            <p className="text-gray-200 mt-0.5">
              {(algorithm.required_asset_types ?? []).join(", ") || "—"}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">
              Supported Brokers
            </span>
            <p className="text-gray-200 mt-0.5">
              {(algorithm.supported_brokers ?? []).join(", ") || "—"}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">
              Required Options Level
            </span>
            <p className="text-gray-200 mt-0.5">
              {algorithm.required_options_level !== null
                ? algorithm.required_options_level
                : "—"}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Installed At</span>
            <p className="text-gray-200 mt-0.5 text-xs">
              {algorithm.installed_at
                ? new Date(algorithm.installed_at).toLocaleString()
                : "—"}
            </p>
          </div>
        </div>
        {algorithm.install_error && (
          <div className="bg-red-950 border border-red-800 rounded p-3">
            <p className="text-xs text-red-400">{algorithm.install_error}</p>
          </div>
        )}
      </div>

      {/* Config Schema */}
      {algorithm.config_schema && (
        <div className="bg-gray-900 border border-gray-800 rounded p-4">
          <h2 className="text-lg font-semibold text-gray-200 mb-3">
            Config Schema
          </h2>
          <pre className="text-xs text-gray-300 overflow-x-auto whitespace-pre-wrap">
            {JSON.stringify(algorithm.config_schema, null, 2)}
          </pre>
        </div>
      )}

      {/* Instances */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-gray-200">Instances</h2>
          <button
            onClick={() => setCreateOpen(true)}
            className="px-4 py-2 rounded text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 transition-colors"
          >
            Create Instance
          </button>
        </div>

        <DataTable
          data={instances ?? []}
          columns={instanceColumns}
          isLoading={loadingInstances}
          enableSorting
          emptyMessage="No instances found."
          onRowClick={(inst) => navigate(`/instances/${inst.id}`)}
        />
      </section>

      {/* Delete algorithm confirm */}
      <ConfirmDialog
        open={deleteOpen}
        title="Delete Algorithm"
        message={`Are you sure you want to delete "${algorithm.name}"? All associated data will be removed.`}
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setDeleteOpen(false)}
      />

      {/* Create instance modal */}
      <FormModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Create Instance"
        schema={createInstanceSchema}
        defaultValues={{ account_id: "", worker_id: "", config_values: "" }}
        onSubmit={handleCreateInstance}
        submitLabel="Create Instance"
        isSubmitting={isCreating}
      >
        {(form) => (
          <>
            <FormField
              label="Account"
              error={form.formState.errors.account_id?.message}
            >
              <select
                {...form.register("account_id")}
                className="bg-gray-800 border border-gray-700 text-gray-100 rounded px-3 py-2 text-sm w-full"
              >
                <option value="">Select an account</option>
                {(accounts ?? []).map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.name}
                  </option>
                ))}
              </select>
            </FormField>

            <FormField
              label="Worker"
              error={form.formState.errors.worker_id?.message}
            >
              <select
                {...form.register("worker_id")}
                className="bg-gray-800 border border-gray-700 text-gray-100 rounded px-3 py-2 text-sm w-full"
              >
                <option value="">Select a worker</option>
                {(workers ?? []).map((worker) => (
                  <option key={worker.id} value={worker.id}>
                    {worker.name} ({worker.tailscale_ip})
                  </option>
                ))}
              </select>
            </FormField>

            <FormField
              label="Config Values (JSON, optional)"
              error={form.formState.errors.config_values?.message}
            >
              <textarea
                {...form.register("config_values")}
                rows={4}
                placeholder="{}"
                className="bg-gray-800 border border-gray-700 text-gray-100 rounded px-3 py-2 text-sm w-full font-mono"
              />
            </FormField>
          </>
        )}
      </FormModal>

      {/* Run Backtest modal */}
      <RunBacktestModal
        open={backtestOpen}
        onClose={() => setBacktestOpen(false)}
        algorithmId={algorithm?.id ?? ""}
        manifestConfig={(algorithm?.config_schema?.parameters as Array<{ name: string; type: string; default?: unknown }>) ?? []}
      />
    </div>
  );
}
