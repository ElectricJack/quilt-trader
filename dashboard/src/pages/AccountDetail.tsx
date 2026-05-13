import { useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { z } from "zod";
import { ChevronLeft, Pencil, Trash2 } from "lucide-react";
import {
  useAccount,
  useCashFlows,
  useCreateCashFlow,
  useUpdateAccount,
  useDeleteAccount,
  useAllInstances,
} from "../api/hooks";
import type { CashFlow, AlgorithmInstance } from "../types";
import { DataTable, type ColumnDef } from "../components/DataTable";
import { FormModal } from "../components/FormModal";
import { FormField } from "../components/FormField";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { StatusBadge } from "../components/StatusBadge";
import { useUIStore } from "../stores/ui";

// ─── Schemas ──────────────────────────────────────────────────────────────────

const accountEditSchema = z.object({
  name: z.string().min(1, "Name is required"),
  broker_type: z.string().min(1, "Broker type is required"),
  supported_asset_types: z.string().min(1, "At least one asset type is required"),
  pdt_mode: z.string().min(1, "PDT mode is required"),
});

type AccountEditValues = z.infer<typeof accountEditSchema>;

const cashFlowSchema = z.object({
  type: z.string().min(1, "Type is required"),
  amount: z.number({ invalid_type_error: "Amount must be a number" }),
  notes: z.string().optional(),
});

type CashFlowValues = z.infer<typeof cashFlowSchema>;

const INPUT_CLASS =
  "bg-gray-800 border border-gray-700 text-gray-100 rounded px-3 py-2 text-sm w-full";

// ─── Component ────────────────────────────────────────────────────────────────

export function AccountDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const addAlert = useUIStore((s) => s.addAlert);

  const { data: account, isLoading: loadingAccount } = useAccount(id ?? "");
  const { data: cashFlows = [], isLoading: loadingCashFlows } = useCashFlows(id ?? "");
  const { data: allInstances = [] } = useAllInstances();

  const updateAccount = useUpdateAccount();
  const deleteAccount = useDeleteAccount();
  const createCashFlow = useCreateCashFlow(id ?? "");

  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [cashFlowOpen, setCashFlowOpen] = useState(false);

  // ─── Derived data ────────────────────────────────────────────────────────────

  const accountInstances = allInstances.filter((inst) => inst.account_id === id);

  // ─── Cash flow columns ───────────────────────────────────────────────────────

  const cashFlowColumns: ColumnDef<CashFlow, unknown>[] = [
    {
      id: "date",
      header: "Date",
      accessorFn: (row) => (row.timestamp ? new Date(row.timestamp).toLocaleString() : "—"),
      cell: ({ row }) => (
        <span className="text-xs text-gray-400">
          {row.original.timestamp
            ? new Date(row.original.timestamp).toLocaleString()
            : "—"}
        </span>
      ),
    },
    {
      id: "type",
      header: "Type",
      accessorKey: "type",
    },
    {
      id: "amount",
      header: "Amount",
      accessorKey: "amount",
      cell: ({ row }) => (
        <span
          className={
            row.original.amount >= 0 ? "text-green-400" : "text-red-400"
          }
        >
          {row.original.amount >= 0 ? "+" : ""}
          {row.original.amount.toFixed(2)}
        </span>
      ),
    },
    {
      id: "notes",
      header: "Notes",
      accessorKey: "notes",
      cell: ({ row }) => (
        <span className="text-xs text-gray-500">{row.original.notes ?? "—"}</span>
      ),
    },
  ];

  // ─── Instance columns ────────────────────────────────────────────────────────

  const instanceColumns: ColumnDef<AlgorithmInstance, unknown>[] = [
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
      id: "algorithm_id",
      header: "Algorithm",
      accessorKey: "algorithm_id",
      cell: ({ row }) => (
        <span className="font-mono text-xs text-gray-400">
          {row.original.algorithm_id.slice(0, 8)}…
        </span>
      ),
    },
    {
      id: "worker_id",
      header: "Worker",
      accessorKey: "worker_id",
      cell: ({ row }) => (
        <span className="font-mono text-xs text-gray-400">
          {row.original.worker_id.slice(0, 8)}…
        </span>
      ),
    },
  ];

  // ─── Submit handlers ─────────────────────────────────────────────────────────

  async function handleEdit(values: AccountEditValues) {
    if (!account) return;
    const assetTypes = values.supported_asset_types
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean);
    await updateAccount.mutateAsync({
      id: account.id,
      body: {
        name: values.name,
        supported_asset_types: assetTypes,
        pdt_mode: values.pdt_mode,
      },
    });
    addAlert({ message: "Account updated.", severity: "success" });
    setEditOpen(false);
  }

  async function handleDelete() {
    if (!account) return;
    await deleteAccount.mutateAsync(account.id);
    addAlert({ message: "Account deleted.", severity: "success" });
    navigate("/accounts");
  }

  async function handleCashFlow(values: CashFlowValues) {
    await createCashFlow.mutateAsync({
      type: values.type,
      amount: values.amount,
      notes: values.notes || undefined,
    });
    addAlert({ message: "Cash flow recorded.", severity: "success" });
    setCashFlowOpen(false);
  }

  // ─── Loading / not found ─────────────────────────────────────────────────────

  if (loadingAccount) {
    return <p className="text-gray-400 text-sm">Loading…</p>;
  }

  if (!account) {
    return <p className="text-gray-400 text-sm">Account not found.</p>;
  }

  // ─── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <Link
            to="/accounts"
            className="flex-shrink-0 text-gray-400 hover:text-white transition-colors"
            title="Back to Accounts"
          >
            <ChevronLeft size={20} />
          </Link>
          <h1 className="text-2xl font-bold text-white truncate">{account.name}</h1>
          <StatusBadge status={account.locked_by ? "locked" : "available"} />
        </div>
        <div className="flex gap-2 flex-shrink-0">
          <button
            className="flex items-center gap-1.5 px-3 py-2 rounded text-sm font-medium text-gray-300 bg-gray-700 hover:bg-gray-600 transition-colors"
            onClick={() => setEditOpen(true)}
          >
            <Pencil size={14} />
            Edit
          </button>
          <button
            className="flex items-center gap-1.5 px-3 py-2 rounded text-sm font-medium text-white bg-red-700 hover:bg-red-600 transition-colors"
            onClick={() => setDeleteOpen(true)}
          >
            <Trash2 size={14} />
            Delete
          </button>
        </div>
      </div>

      {/* Account Info */}
      <div className="bg-gray-900 border border-gray-800 rounded p-4 space-y-3">
        <h2 className="text-lg font-semibold text-gray-200">Account Details</h2>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-xs text-gray-500 uppercase">Broker</span>
            <p className="text-gray-200 mt-0.5">{account.broker_type}</p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">PDT Mode</span>
            <p className="text-gray-200 mt-0.5">{account.pdt_mode}</p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Asset Types</span>
            <p className="text-gray-200 mt-0.5">
              {(account.supported_asset_types ?? []).join(", ") || "—"}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Options Level</span>
            <p className="text-gray-200 mt-0.5">
              {account.options_level !== null ? account.options_level : "—"}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Features</span>
            <p className="text-gray-200 mt-0.5">
              {(account.account_features ?? []).join(", ") || "—"}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Status</span>
            <div className="mt-0.5">
              <StatusBadge status={account.locked_by ? "locked" : "available"} />
            </div>
          </div>
        </div>
        {account.locked_by && (
          <p className="text-xs text-gray-500">Locked by: {account.locked_by}</p>
        )}
      </div>

      {/* Instances */}
      <section>
        <h2 className="text-lg font-semibold text-gray-200 mb-3">Instances</h2>
        {accountInstances.length === 0 ? (
          <p className="text-sm text-gray-500">No instances running on this account.</p>
        ) : (
          <DataTable
            data={accountInstances}
            columns={instanceColumns}
            emptyMessage="No instances running on this account."
            onRowClick={(row) => navigate(`/instances/${row.id}`)}
            enableSorting
          />
        )}
      </section>

      {/* Cash Flows */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-gray-200">Cash Flows</h2>
          <button
            className="px-3 py-2 rounded text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 transition-colors"
            onClick={() => setCashFlowOpen(true)}
          >
            Record Cash Flow
          </button>
        </div>
        <DataTable
          data={cashFlows}
          columns={cashFlowColumns}
          isLoading={loadingCashFlows}
          emptyMessage="No cash flows recorded."
          enableSorting
        />
      </section>

      {/* Edit Account Modal */}
      <FormModal
        open={editOpen}
        onClose={() => setEditOpen(false)}
        title="Edit Account"
        schema={accountEditSchema}
        defaultValues={{
          name: account.name,
          broker_type: account.broker_type,
          supported_asset_types: (account.supported_asset_types ?? []).join(", "),
          pdt_mode: account.pdt_mode,
        }}
        onSubmit={handleEdit}
        submitLabel="Save Changes"
        isSubmitting={updateAccount.isPending}
      >
        {(form) => (
          <>
            <FormField label="Name" error={form.formState.errors.name?.message}>
              <input className={INPUT_CLASS} {...form.register("name")} />
            </FormField>

            <FormField label="Broker Type" error={form.formState.errors.broker_type?.message}>
              <select className={INPUT_CLASS} {...form.register("broker_type")}>
                <option value="">Select broker…</option>
                <option value="alpaca">Alpaca</option>
                <option value="tradier">Tradier</option>
                <option value="interactive_brokers">Interactive Brokers</option>
              </select>
            </FormField>

            <FormField
              label="Supported Asset Types"
              error={form.formState.errors.supported_asset_types?.message}
            >
              <input className={INPUT_CLASS} {...form.register("supported_asset_types")} />
            </FormField>

            <FormField label="PDT Mode" error={form.formState.errors.pdt_mode?.message}>
              <select className={INPUT_CLASS} {...form.register("pdt_mode")}>
                <option value="">Select PDT mode…</option>
                <option value="off">Off</option>
                <option value="warn">Warn</option>
                <option value="block">Block</option>
              </select>
            </FormField>
          </>
        )}
      </FormModal>

      {/* Record Cash Flow Modal */}
      <FormModal
        open={cashFlowOpen}
        onClose={() => setCashFlowOpen(false)}
        title="Record Cash Flow"
        schema={cashFlowSchema}
        defaultValues={{ type: "", amount: 0, notes: "" }}
        onSubmit={handleCashFlow}
        submitLabel="Record"
        isSubmitting={createCashFlow.isPending}
      >
        {(form) => (
          <>
            <FormField label="Type" error={form.formState.errors.type?.message}>
              <select className={INPUT_CLASS} {...form.register("type")}>
                <option value="">Select type…</option>
                <option value="deposit">Deposit</option>
                <option value="withdrawal">Withdrawal</option>
              </select>
            </FormField>

            <FormField label="Amount" error={form.formState.errors.amount?.message}>
              <input
                type="number"
                step="0.01"
                className={INPUT_CLASS}
                {...form.register("amount", { valueAsNumber: true })}
              />
            </FormField>

            <FormField label="Notes (optional)" error={form.formState.errors.notes?.message}>
              <input
                className={INPUT_CLASS}
                placeholder="Optional notes…"
                {...form.register("notes")}
              />
            </FormField>
          </>
        )}
      </FormModal>

      {/* Delete Confirm */}
      <ConfirmDialog
        open={deleteOpen}
        title="Delete Account"
        message={`Are you sure you want to delete "${account.name}"? This action cannot be undone.`}
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setDeleteOpen(false)}
      />
    </div>
  );
}
