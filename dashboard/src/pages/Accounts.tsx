import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { z } from "zod";
import { Pencil, Trash2 } from "lucide-react";
import {
  useAccounts,
  useCreateAccount,
  useUpdateAccount,
  useDeleteAccount,
} from "../api/hooks";
import type { Account } from "../types";
import { DataTable, type ColumnDef } from "../components/DataTable";
import { FormModal } from "../components/FormModal";
import { FormField } from "../components/FormField";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { StatusBadge } from "../components/StatusBadge";
import { useUIStore } from "../stores/ui";

// ─── Schemas ──────────────────────────────────────────────────────────────────

const accountFormSchema = z.object({
  name: z.string().min(1, "Name is required"),
  broker_type: z.string().min(1, "Broker type is required"),
  supported_asset_types: z.string().min(1, "At least one asset type is required"),
  pdt_mode: z.string().min(1, "PDT mode is required"),
});

type AccountFormValues = z.infer<typeof accountFormSchema>;

const INPUT_CLASS =
  "bg-gray-800 border border-gray-700 text-gray-100 rounded px-3 py-2 text-sm w-full";

// ─── Component ────────────────────────────────────────────────────────────────

export function Accounts() {
  const navigate = useNavigate();
  const { data: accounts = [], isLoading } = useAccounts();
  const createAccount = useCreateAccount();
  const updateAccount = useUpdateAccount();
  const deleteAccount = useDeleteAccount();
  const addAlert = useUIStore((s) => s.addAlert);

  const [addOpen, setAddOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Account | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Account | null>(null);

  // ─── Columns ────────────────────────────────────────────────────────────────

  const columns: ColumnDef<Account, unknown>[] = [
    {
      id: "name",
      header: "Name",
      accessorKey: "name",
      cell: ({ row }) => (
        <Link
          to={`/accounts/${row.original.id}`}
          className="text-indigo-400 hover:underline"
          onClick={(e) => e.stopPropagation()}
        >
          {row.original.name}
        </Link>
      ),
    },
    {
      id: "broker_type",
      header: "Broker",
      accessorKey: "broker_type",
    },
    {
      id: "supported_asset_types",
      header: "Asset Types",
      accessorFn: (row) => (row.supported_asset_types ?? []).join(", "),
    },
    {
      id: "pdt_mode",
      header: "PDT Mode",
      accessorKey: "pdt_mode",
    },
    {
      id: "status",
      header: "Status",
      accessorFn: (row) => (row.locked_by ? "locked" : "available"),
      cell: ({ row }) => (
        <StatusBadge status={row.original.locked_by ? "locked" : "available"} />
      ),
    },
    {
      id: "actions",
      header: "Actions",
      cell: ({ row }) => (
        <div className="flex gap-2">
          <button
            className="p-1 text-gray-400 hover:text-indigo-400 transition-colors"
            title="Edit"
            onClick={(e) => {
              e.stopPropagation();
              setEditTarget(row.original);
            }}
          >
            <Pencil size={14} />
          </button>
          <button
            className="p-1 text-gray-400 hover:text-red-400 transition-colors"
            title="Delete"
            onClick={(e) => {
              e.stopPropagation();
              setDeleteTarget(row.original);
            }}
          >
            <Trash2 size={14} />
          </button>
        </div>
      ),
    },
  ];

  // ─── Submit handlers ────────────────────────────────────────────────────────

  async function handleCreate(values: AccountFormValues) {
    const assetTypes = values.supported_asset_types
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean);
    await createAccount.mutateAsync({
      name: values.name,
      broker_type: values.broker_type,
      supported_asset_types: assetTypes,
      pdt_mode: values.pdt_mode,
      credentials: {},
    });
    addAlert({ message: "Account created.", severity: "success" });
    setAddOpen(false);
  }

  async function handleEdit(values: AccountFormValues) {
    if (!editTarget) return;
    const assetTypes = values.supported_asset_types
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean);
    await updateAccount.mutateAsync({
      id: editTarget.id,
      body: {
        name: values.name,
        supported_asset_types: assetTypes,
        pdt_mode: values.pdt_mode,
      },
    });
    addAlert({ message: "Account updated.", severity: "success" });
    setEditTarget(null);
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    await deleteAccount.mutateAsync(deleteTarget.id);
    addAlert({ message: "Account deleted.", severity: "success" });
    setDeleteTarget(null);
  }

  // ─── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">
          Accounts{" "}
          {!isLoading && (
            <span className="text-lg font-normal text-gray-400">
              ({accounts.length})
            </span>
          )}
        </h1>
        <button
          className="px-4 py-2 rounded text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 transition-colors"
          onClick={() => setAddOpen(true)}
        >
          Add Account
        </button>
      </div>

      {/* Table */}
      <DataTable
        data={accounts}
        columns={columns}
        isLoading={isLoading}
        emptyMessage="No accounts found."
        enableSorting
        onRowClick={(row) => navigate(`/accounts/${row.id}`)}
      />

      {/* Add Account Modal */}
      <FormModal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        title="Add Account"
        schema={accountFormSchema}
        defaultValues={{ name: "", broker_type: "", supported_asset_types: "", pdt_mode: "" }}
        onSubmit={handleCreate}
        submitLabel="Create Account"
        isSubmitting={createAccount.isPending}
      >
        {(form) => (
          <>
            <FormField label="Name" error={form.formState.errors.name?.message}>
              <input
                className={INPUT_CLASS}
                placeholder="My Trading Account"
                {...form.register("name")}
              />
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
              <input
                className={INPUT_CLASS}
                placeholder="equities, options, crypto"
                {...form.register("supported_asset_types")}
              />
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

      {/* Edit Account Modal */}
      <FormModal
        open={!!editTarget}
        onClose={() => setEditTarget(null)}
        title="Edit Account"
        schema={accountFormSchema}
        defaultValues={
          editTarget
            ? {
                name: editTarget.name,
                broker_type: editTarget.broker_type,
                supported_asset_types: (editTarget.supported_asset_types ?? []).join(", "),
                pdt_mode: editTarget.pdt_mode,
              }
            : { name: "", broker_type: "", supported_asset_types: "", pdt_mode: "" }
        }
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

      {/* Delete Confirm */}
      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete Account"
        message={`Are you sure you want to delete "${deleteTarget?.name}"? This action cannot be undone.`}
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
