import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { z } from "zod";
import { Pencil, Trash2 } from "lucide-react";
import type { UseFormReturn } from "react-hook-form";
import {
  useAccounts,
  useCreateAccount,
  useUpdateAccount,
  useDeleteAccount,
  useBrokerAssetTypes,
} from "../api/hooks";
import { api } from "../api/client";
import type { Account } from "../types";
import { DataTable, type ColumnDef } from "../components/DataTable";
import { FormModal } from "../components/FormModal";
import { FormField } from "../components/FormField";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { StatusBadge } from "../components/StatusBadge";
import { useUIStore } from "../stores/ui";

// ─── Schemas ──────────────────────────────────────────────────────────────────

const BROKER_OPTIONS = [
  { value: "alpaca", label: "Alpaca" },
  { value: "tradier", label: "Tradier" },
] as const;

const createAccountSchema = z
  .object({
    name: z.string().min(1, "Name is required"),
    broker_type: z.enum(["alpaca", "tradier"]),
    environment: z.enum(["paper", "live"]),
    supported_asset_types: z.array(z.string()).min(1, "Select at least one asset type"),
    pdt_mode: z.string().min(1, "PDT mode is required"),
    alpaca_api_key: z.string().optional(),
    alpaca_secret_key: z.string().optional(),
    tradier_access_token: z.string().optional(),
    tradier_account_id: z.string().optional(),
  })
  .superRefine((data, ctx) => {
    if (data.broker_type === "alpaca") {
      if (!data.alpaca_api_key?.trim())
        ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["alpaca_api_key"], message: "API Key is required" });
      if (!data.alpaca_secret_key?.trim())
        ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["alpaca_secret_key"], message: "Secret Key is required" });
    } else if (data.broker_type === "tradier") {
      if (!data.tradier_access_token?.trim())
        ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["tradier_access_token"], message: "Access Token is required" });
      if (!data.tradier_account_id?.trim())
        ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["tradier_account_id"], message: "Account ID is required" });
    }
  });

type CreateAccountValues = z.infer<typeof createAccountSchema>;

const editAccountSchema = z.object({
  name: z.string().min(1, "Name is required"),
  environment: z.enum(["paper", "live"]),
  supported_asset_types: z.array(z.string()).min(1, "Select at least one asset type"),
  pdt_mode: z.string().min(1, "PDT mode is required"),
  // Optional credential rotation; empty means "leave unchanged".
  alpaca_api_key: z.string().optional(),
  alpaca_secret_key: z.string().optional(),
  tradier_access_token: z.string().optional(),
  tradier_account_id: z.string().optional(),
});

type EditAccountValues = z.infer<typeof editAccountSchema>;

const INPUT_CLASS =
  "bg-gray-800 border border-gray-700 text-gray-100 rounded px-3 py-2 text-sm w-full";

function buildCredentials(values: {
  broker_type: string;
  alpaca_api_key?: string;
  alpaca_secret_key?: string;
  tradier_access_token?: string;
  tradier_account_id?: string;
}): Record<string, string> | null {
  if (values.broker_type === "alpaca") {
    if (!values.alpaca_api_key && !values.alpaca_secret_key) return null;
    return {
      api_key: values.alpaca_api_key ?? "",
      secret_key: values.alpaca_secret_key ?? "",
    };
  }
  if (values.broker_type === "tradier") {
    if (!values.tradier_access_token && !values.tradier_account_id) return null;
    return {
      access_token: values.tradier_access_token ?? "",
      account_id: values.tradier_account_id ?? "",
    };
  }
  return null;
}

// ─── U1: Asset-type checkbox sub-components ───────────────────────────────────

function AssetTypeCheckboxes({
  brokerType,
  value,
  onChange,
  error,
}: {
  brokerType: string;
  value: string[];
  onChange: (v: string[]) => void;
  error?: string;
}) {
  const { data: assetTypes = [], isLoading } = useBrokerAssetTypes(brokerType);

  const toggle = (type: string) => {
    if (value.includes(type)) {
      onChange(value.filter((v) => v !== type));
    } else {
      onChange([...value, type]);
    }
  };

  return (
    <FormField label="Supported Asset Types" error={error}>
      {isLoading ? (
        <p className="text-xs text-gray-400">Loading asset types…</p>
      ) : assetTypes.length === 0 ? (
        <p className="text-xs text-gray-400">No asset types available.</p>
      ) : (
        <div className="flex flex-wrap gap-3">
          {assetTypes.map((type) => (
            <label key={type} className="flex items-center gap-1.5 cursor-pointer text-sm text-gray-300">
              <input
                type="checkbox"
                className="accent-indigo-500"
                checked={value.includes(type)}
                onChange={() => toggle(type)}
              />
              {type}
            </label>
          ))}
        </div>
      )}
    </FormField>
  );
}

function AddAccountFormBody({
  form,
  testingConnection,
  handleTestConnection,
}: {
  form: UseFormReturn<CreateAccountValues>;
  testingConnection: boolean;
  handleTestConnection: (values: CreateAccountValues) => Promise<void>;
}) {
  const broker = form.watch("broker_type");
  const env = form.watch("environment");
  const selectedTypes = form.watch("supported_asset_types");

  return (
    <>
      <FormField label="Name" error={form.formState.errors.name?.message}>
        <input
          className={INPUT_CLASS}
          placeholder="My Trading Account"
          {...form.register("name")}
        />
      </FormField>

      <FormField label="Broker" error={form.formState.errors.broker_type?.message}>
        <select className={INPUT_CLASS} {...form.register("broker_type")}>
          {BROKER_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </FormField>

      <FormField label="Environment" error={form.formState.errors.environment?.message}>
        <div className="flex gap-3">
          {(["paper", "live"] as const).map((v) => (
            <label
              key={v}
              className={`flex-1 px-3 py-2 rounded border cursor-pointer text-sm text-center transition-colors ${
                env === v
                  ? v === "live"
                    ? "bg-red-900/40 border-red-700 text-red-200"
                    : "bg-blue-900/40 border-blue-700 text-blue-200"
                  : "bg-gray-800 border-gray-700 text-gray-300 hover:border-gray-600"
              }`}
            >
              <input type="radio" value={v} className="hidden" {...form.register("environment")} />
              {v === "live" ? "Live" : "Paper"}
            </label>
          ))}
        </div>
      </FormField>

      {broker === "alpaca" && (
        <>
          <FormField
            label="Alpaca API Key"
            error={form.formState.errors.alpaca_api_key?.message}
          >
            <input
              className={INPUT_CLASS}
              autoComplete="off"
              placeholder="PKxxxxxxxxxxxxxxxx"
              {...form.register("alpaca_api_key")}
            />
          </FormField>
          <FormField
            label="Alpaca Secret Key"
            error={form.formState.errors.alpaca_secret_key?.message}
          >
            <input
              type="password"
              className={INPUT_CLASS}
              autoComplete="off"
              {...form.register("alpaca_secret_key")}
            />
          </FormField>
        </>
      )}

      {broker === "tradier" && (
        <>
          <FormField
            label="Tradier Access Token"
            error={form.formState.errors.tradier_access_token?.message}
          >
            <input
              type="password"
              className={INPUT_CLASS}
              autoComplete="off"
              {...form.register("tradier_access_token")}
            />
          </FormField>
          <FormField
            label="Tradier Account ID"
            error={form.formState.errors.tradier_account_id?.message}
          >
            <input
              className={INPUT_CLASS}
              autoComplete="off"
              placeholder="VA1234567"
              {...form.register("tradier_account_id")}
            />
          </FormField>
        </>
      )}

      <AssetTypeCheckboxes
        brokerType={broker}
        value={selectedTypes}
        onChange={(v) => form.setValue("supported_asset_types", v, { shouldValidate: true })}
        error={form.formState.errors.supported_asset_types?.message as string | undefined}
      />

      <FormField label="PDT Mode" error={form.formState.errors.pdt_mode?.message}>
        <select className={INPUT_CLASS} {...form.register("pdt_mode")}>
          <option value="off">Off</option>
          <option value="warn">Warn</option>
          <option value="block">Block</option>
        </select>
      </FormField>

      <div className="pt-2">
        <button
          type="button"
          disabled={testingConnection}
          onClick={form.handleSubmit(handleTestConnection)}
          className="px-3 py-2 rounded text-sm font-medium text-gray-200 bg-gray-700 hover:bg-gray-600 disabled:opacity-60 transition-colors"
        >
          {testingConnection ? "Testing…" : "Test Connection"}
        </button>
      </div>
    </>
  );
}

function EditAccountFormBody({
  form,
  editTarget,
}: {
  form: UseFormReturn<EditAccountValues>;
  editTarget: Account;
}) {
  const env = form.watch("environment");
  const broker = editTarget.broker_type;
  const selectedTypes = form.watch("supported_asset_types");

  return (
    <>
      <FormField label="Name" error={form.formState.errors.name?.message}>
        <input className={INPUT_CLASS} {...form.register("name")} />
      </FormField>

      <FormField label="Broker">
        <input
          className={`${INPUT_CLASS} opacity-60 cursor-not-allowed`}
          value={broker ?? ""}
          disabled
          readOnly
        />
      </FormField>

      <FormField label="Environment" error={form.formState.errors.environment?.message}>
        <div className="flex gap-3">
          {(["paper", "live"] as const).map((v) => (
            <label
              key={v}
              className={`flex-1 px-3 py-2 rounded border cursor-pointer text-sm text-center transition-colors ${
                env === v
                  ? v === "live"
                    ? "bg-red-900/40 border-red-700 text-red-200"
                    : "bg-blue-900/40 border-blue-700 text-blue-200"
                  : "bg-gray-800 border-gray-700 text-gray-300 hover:border-gray-600"
              }`}
            >
              <input type="radio" value={v} className="hidden" {...form.register("environment")} />
              {v === "live" ? "Live" : "Paper"}
            </label>
          ))}
        </div>
      </FormField>

      <AssetTypeCheckboxes
        brokerType={broker}
        value={selectedTypes}
        onChange={(v) => form.setValue("supported_asset_types", v, { shouldValidate: true })}
        error={form.formState.errors.supported_asset_types?.message as string | undefined}
      />

      <FormField label="PDT Mode" error={form.formState.errors.pdt_mode?.message}>
        <select className={INPUT_CLASS} {...form.register("pdt_mode")}>
          <option value="off">Off</option>
          <option value="warn">Warn</option>
          <option value="block">Block</option>
        </select>
      </FormField>

      <div className="border-t border-gray-800 pt-3 mt-2">
        <p className="text-xs text-gray-500 mb-2">
          Rotate credentials (leave blank to keep current values)
        </p>
        {broker === "alpaca" && (
          <>
            <FormField label="New API Key">
              <input
                className={INPUT_CLASS}
                autoComplete="off"
                placeholder="leave blank to keep"
                {...form.register("alpaca_api_key")}
              />
            </FormField>
            <FormField label="New Secret Key">
              <input
                type="password"
                className={INPUT_CLASS}
                autoComplete="off"
                placeholder="leave blank to keep"
                {...form.register("alpaca_secret_key")}
              />
            </FormField>
          </>
        )}
        {broker === "tradier" && (
          <>
            <FormField label="New Access Token">
              <input
                type="password"
                className={INPUT_CLASS}
                autoComplete="off"
                placeholder="leave blank to keep"
                {...form.register("tradier_access_token")}
              />
            </FormField>
            <FormField label="New Account ID">
              <input
                className={INPUT_CLASS}
                autoComplete="off"
                placeholder="leave blank to keep"
                {...form.register("tradier_account_id")}
              />
            </FormField>
          </>
        )}
      </div>
    </>
  );
}

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
  const [testingConnection, setTestingConnection] = useState(false);

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
      id: "environment",
      header: "Env",
      accessorKey: "environment",
      cell: ({ row }) => {
        const env = row.original.environment;
        const cls = env === "live"
          ? "bg-emerald-900/40 text-emerald-300 border-emerald-800"
          : "bg-blue-900/40 text-blue-300 border-blue-800";
        return (
          <span className={`text-xs px-2 py-0.5 rounded border ${cls}`}>
            {env === "live" ? "LIVE" : "PAPER"}
          </span>
        );
      },
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

  // ─── Test connection ────────────────────────────────────────────────────────

  async function handleTestConnection(values: CreateAccountValues) {
    const credentials = buildCredentials(values);
    if (!credentials) {
      addAlert({ message: "Fill in credential fields before testing.", severity: "warning" });
      return;
    }
    setTestingConnection(true);
    try {
      const result = await api.testAccountConnection({
        broker_type: values.broker_type,
        environment: values.environment,
        credentials,
      });
      if (result.ok) {
        const cur = result.info?.currency ?? "USD";
        const pv = result.info?.portfolio_value;
        const detail = pv != null ? `${cur} ${pv.toLocaleString()}` : "credentials verified";
        addAlert({ message: `Connection OK — ${detail}`, severity: "success" });
      } else {
        addAlert({ message: `Connection failed: ${result.error}`, severity: "error" });
      }
    } catch (e) {
      addAlert({ message: `Test connection error: ${(e as Error).message}`, severity: "error" });
    } finally {
      setTestingConnection(false);
    }
  }

  // ─── Submit handlers ────────────────────────────────────────────────────────

  async function handleCreate(values: CreateAccountValues) {
    const credentials = buildCredentials(values) ?? {};
    await createAccount.mutateAsync({
      name: values.name,
      broker_type: values.broker_type,
      environment: values.environment,
      supported_asset_types: values.supported_asset_types,
      pdt_mode: values.pdt_mode,
      credentials,
    });
    addAlert({ message: "Account created.", severity: "success" });
    setAddOpen(false);
  }

  async function handleEdit(values: EditAccountValues) {
    if (!editTarget) return;
    const credentials = buildCredentials({
      broker_type: editTarget.broker_type,
      alpaca_api_key: values.alpaca_api_key,
      alpaca_secret_key: values.alpaca_secret_key,
      tradier_access_token: values.tradier_access_token,
      tradier_account_id: values.tradier_account_id,
    });
    await updateAccount.mutateAsync({
      id: editTarget.id,
      body: {
        name: values.name,
        environment: values.environment,
        supported_asset_types: values.supported_asset_types,
        pdt_mode: values.pdt_mode,
        ...(credentials ? { credentials } : {}),
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
        schema={createAccountSchema}
        defaultValues={{
          name: "",
          broker_type: "alpaca",
          environment: "paper",
          supported_asset_types: [],
          pdt_mode: "off",
          alpaca_api_key: "",
          alpaca_secret_key: "",
          tradier_access_token: "",
          tradier_account_id: "",
        }}
        onSubmit={handleCreate}
        submitLabel="Create Account"
        isSubmitting={createAccount.isPending}
      >
        {(form) => (
          <AddAccountFormBody
            form={form}
            testingConnection={testingConnection}
            handleTestConnection={handleTestConnection}
          />
        )}
      </FormModal>

      {/* Edit Account Modal */}
      <FormModal
        open={!!editTarget}
        onClose={() => setEditTarget(null)}
        title="Edit Account"
        schema={editAccountSchema}
        defaultValues={
          editTarget
            ? {
                name: editTarget.name,
                environment: editTarget.environment,
                supported_asset_types: editTarget.supported_asset_types ?? [],
                pdt_mode: editTarget.pdt_mode,
                alpaca_api_key: "",
                alpaca_secret_key: "",
                tradier_access_token: "",
                tradier_account_id: "",
              }
            : {
                name: "",
                environment: "paper",
                supported_asset_types: [],
                pdt_mode: "off",
                alpaca_api_key: "",
                alpaca_secret_key: "",
                tradier_access_token: "",
                tradier_account_id: "",
              }
        }
        onSubmit={handleEdit}
        submitLabel="Save Changes"
        isSubmitting={updateAccount.isPending}
      >
        {(form) =>
          editTarget ? (
            <EditAccountFormBody form={form} editTarget={editTarget} />
          ) : null
        }
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
