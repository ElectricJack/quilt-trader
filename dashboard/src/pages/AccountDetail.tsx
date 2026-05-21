import { useMemo, useState, type ReactNode } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { z } from "zod";
import { ChevronLeft, Pencil, Trash2, RefreshCw, RotateCw, ChevronDown } from "lucide-react";
import {
  useAccount,
  useCashFlows,
  useCreateCashFlow,
  useUpdateAccount,
  useDeleteAccount,
  useAllInstances,
  useBrokerInfo,
  useSyncAccount,
  useAccountTrades,
  useAccountEquityCurve,
  useClosePosition,
  useWebSocketTopic,
} from "../api/hooks";
import { OpenPositionModal } from "../components/OpenPositionModal";
import type { CashFlow, AlgorithmInstance, TradeRow } from "../types";
import type { BrokerPosition } from "../api/client";
import { DataTable, type ColumnDef } from "../components/DataTable";
import { EquityCurve } from "../components/EquityCurve";
import { FormModal } from "../components/FormModal";
import { FormField } from "../components/FormField";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { StatusBadge } from "../components/StatusBadge";
import { useUIStore } from "../stores/ui";

function fmtUsd(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
}

function fmtNum(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", { maximumFractionDigits: digits });
}

interface CollapsibleSectionProps {
  title: string;
  count?: number;
  actions?: ReactNode;
  defaultOpen?: boolean;
  maxHeight?: string;
  children: ReactNode;
}

function CollapsibleSection({
  title,
  count,
  actions,
  defaultOpen = true,
  maxHeight = "320px",
  children,
}: CollapsibleSectionProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="bg-gray-900 border border-gray-800 rounded">
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-800">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-2 text-left flex-1 min-w-0 hover:text-white text-gray-200 transition-colors"
        >
          <ChevronDown
            size={16}
            className={`flex-shrink-0 transition-transform ${open ? "" : "-rotate-90"}`}
          />
          <span className="text-base font-semibold">{title}</span>
          {count != null && count > 0 && (
            <span className="text-sm font-normal text-gray-500">({count})</span>
          )}
        </button>
        {actions && <div className="flex-shrink-0">{actions}</div>}
      </div>
      {open && (
        <div className="overflow-y-auto" style={{ maxHeight }}>
          <div className="p-3">{children}</div>
        </div>
      )}
    </section>
  );
}

function DetailItem({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline gap-2 whitespace-nowrap">
      <span className="text-[10px] uppercase tracking-wide text-gray-500">{label}</span>
      <span className="text-sm text-gray-200">{children}</span>
    </div>
  );
}

// ─── Schemas ──────────────────────────────────────────────────────────────────

const accountEditSchema = z.object({
  name: z.string().min(1, "Name is required"),
  environment: z.enum(["paper", "live"]),
  supported_asset_types: z.string().min(1, "At least one asset type is required"),
  pdt_mode: z.string().min(1, "PDT mode is required"),
  show_in_overview: z.boolean(),
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
  const {
    data: brokerInfo,
    isLoading: loadingBrokerInfo,
    isFetching: fetchingBrokerInfo,
    error: brokerInfoError,
    refetch: refetchBrokerInfo,
  } = useBrokerInfo(id ?? "");
  const syncAccount = useSyncAccount(id ?? "");
  const { data: tradesData, isLoading: loadingTrades } = useAccountTrades(id ?? "", 200);
  const trades = tradesData?.items ?? [];

  const updateAccount = useUpdateAccount();
  const deleteAccount = useDeleteAccount();
  const createCashFlow = useCreateCashFlow(id ?? "");

  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [cashFlowOpen, setCashFlowOpen] = useState(false);
  const [openPositionOpen, setOpenPositionOpen] = useState(false);
  const [syncRange, setSyncRange] = useState<"incremental" | "30d" | "90d" | "1y" | "all">("incremental");
  const [chartRange, setChartRange] = useState<"30d" | "90d" | "1y" | "all">("90d");
  const [closeTarget, setCloseTarget] = useState<BrokerPosition | null>(null);
  const [closeError, setCloseError] = useState<string | null>(null);
  const [showActions, setShowActions] = useState(false);
  const closePos = useClosePosition(id ?? "");

  const liveProgress = useWebSocketTopic<{ message: string }>(
    id ? `account:${id}:setup_progress` : null
  );

  // Round to start-of-day so the cache key is stable across renders within a day,
  // preventing the equity-curve query from refetching on every render.
  const chartSince = useMemo(() => {
    const d = new Date();
    if (chartRange === "30d") d.setDate(d.getDate() - 30);
    else if (chartRange === "90d") d.setDate(d.getDate() - 90);
    else if (chartRange === "1y") d.setFullYear(d.getFullYear() - 1);
    else d.setFullYear(2000, 0, 1);
    d.setHours(0, 0, 0, 0);
    return d.toISOString();
  }, [chartRange]);
  const { data: equityData, isLoading: loadingEquity } = useAccountEquityCurve(id ?? "", chartSince);
  // Dedup by second + sort ascending; lightweight-charts requires unique increasing times.
  const equityPoints = useMemo(() => {
    const items = equityData?.items ?? [];
    const byTime = new Map<number, { timestamp: string; equity: number }>();
    for (const p of items) {
      const sec = Math.floor(new Date(p.timestamp).getTime() / 1000);
      if (!Number.isFinite(sec)) continue;
      // Keep last write at a given second (snapshots win over estimated since they come later in our ordering).
      byTime.set(sec, { timestamp: p.timestamp, equity: p.value });
    }
    return Array.from(byTime.entries())
      .sort(([a], [b]) => a - b)
      .map(([, v]) => v);
  }, [equityData]);

  // ─── Derived data ────────────────────────────────────────────────────────────

  const accountInstances = allInstances.filter((inst) => inst.account_id === id);
  const positions = brokerInfo?.positions ?? [];
  const accountInfo = brokerInfo?.account_info;

  // ─── Position columns ────────────────────────────────────────────────────────

  const positionColumns = useMemo<ColumnDef<BrokerPosition, unknown>[]>(
    () => [
      {
        id: "symbol",
        header: "Symbol",
        accessorKey: "symbol",
        cell: ({ row }) => (
          <span className="font-mono text-gray-200">{row.original.symbol}</span>
        ),
      },
      {
        id: "side",
        header: "Side",
        accessorKey: "side",
        cell: ({ row }) => (
          <span className={row.original.side === "short" ? "text-red-400" : "text-green-400"}>
            {row.original.side}
          </span>
        ),
      },
      {
        id: "quantity",
        header: "Qty",
        accessorKey: "quantity",
        cell: ({ row }) => fmtNum(row.original.quantity, 4),
      },
      {
        id: "avg_price",
        header: "Avg Price",
        accessorKey: "avg_price",
        cell: ({ row }) => fmtUsd(row.original.avg_price),
      },
      {
        id: "current_price",
        header: "Current",
        accessorKey: "current_price",
        cell: ({ row }) => fmtUsd(row.original.current_price),
      },
      {
        id: "market_value",
        header: "Value",
        accessorKey: "market_value",
        cell: ({ row }) => (
          <span className="font-semibold text-gray-200">{fmtUsd(row.original.market_value)}</span>
        ),
      },
      {
        id: "unrealized_pnl",
        header: "Unrealized P&L",
        accessorKey: "unrealized_pnl",
        cell: ({ row }) => {
          const v = row.original.unrealized_pnl;
          return (
            <span className={v >= 0 ? "text-green-400" : "text-red-400"}>
              {v >= 0 ? "+" : ""}
              {fmtUsd(v)}
            </span>
          );
        },
      },
      {
        id: "actions",
        header: "",
        cell: ({ row }) => (
          <button
            type="button"
            onClick={() => { setCloseError(null); setCloseTarget(row.original); }}
            className="px-2 py-1 rounded text-xs font-medium text-white bg-red-600 hover:bg-red-500 transition-colors"
          >
            Close
          </button>
        ),
      },
    ],
    [setCloseError, setCloseTarget],
  );

  // ─── Trade columns ──────────────────────────────────────────────────────────

  const tradeColumns: ColumnDef<TradeRow, unknown>[] = [
    {
      id: "timestamp",
      header: "Date",
      accessorFn: (row) => (row.timestamp ? new Date(row.timestamp).getTime() : 0),
      cell: ({ row }) => (
        <span className="text-xs text-gray-400">
          {row.original.timestamp ? new Date(row.original.timestamp).toLocaleString() : "—"}
        </span>
      ),
    },
    {
      id: "symbol",
      header: "Symbol",
      accessorKey: "symbol",
      cell: ({ row }) => <span className="font-mono">{row.original.symbol}</span>,
    },
    {
      id: "side",
      header: "Side",
      accessorKey: "side",
      cell: ({ row }) => (
        <span className={row.original.side === "buy" ? "text-green-400" : "text-red-400"}>
          {row.original.side}
        </span>
      ),
    },
    {
      id: "quantity",
      header: "Qty",
      accessorKey: "quantity",
      cell: ({ row }) => fmtNum(row.original.quantity, 4),
    },
    {
      id: "filled_price",
      header: "Price",
      accessorKey: "filled_price",
      cell: ({ row }) => fmtUsd(row.original.filled_price),
    },
    {
      id: "notional",
      header: "Notional",
      accessorKey: "notional",
      cell: ({ row }) => fmtUsd(row.original.notional),
    },
    {
      id: "fees",
      header: "Fees",
      accessorKey: "fees",
      cell: ({ row }) => (
        <span className="text-gray-400">{fmtUsd(row.original.fees)}</span>
      ),
    },
  ];

  function sinceFromRange(range: typeof syncRange): string | undefined {
    if (range === "incremental") return undefined;
    const now = new Date();
    const d = new Date(now);
    if (range === "30d") d.setDate(d.getDate() - 30);
    else if (range === "90d") d.setDate(d.getDate() - 90);
    else if (range === "1y") d.setFullYear(d.getFullYear() - 1);
    else if (range === "all") d.setFullYear(2000, 0, 1);
    return d.toISOString();
  }

  async function handleSync() {
    try {
      const result = await syncAccount.mutateAsync(sinceFromRange(syncRange));
      addAlert({
        message: `Sync complete — ${result.trades_inserted} new trades, ${result.cash_flows_inserted} new cash flows.`,
        severity: "success",
      });
    } catch (e) {
      addAlert({ message: `Sync failed: ${(e as Error).message}`, severity: "error" });
    }
  }

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
          to={`/deployments/${row.original.id}`}
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

  const onConfirmClose = () => {
    if (!closeTarget || closePos.isPending) return;
    setCloseError(null);
    closePos.mutate(
      {
        symbol: closeTarget.symbol,
        asset_type: closeTarget.asset_class || "equities",
        side: closeTarget.side === "short" ? "short" : "long",
        quantity: closeTarget.quantity,
      },
      {
        onSuccess: () => {
          setCloseTarget(null);
        },
        onError: (err: Error) => {
          setCloseError(err.message);
        },
      },
    );
  };

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
        environment: values.environment,
        supported_asset_types: assetTypes,
        pdt_mode: values.pdt_mode,
        show_in_overview: values.show_in_overview,
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
          {account.locked_by && (
            <Link
              to={`/deployments/${account.locked_by}`}
              className="text-xs px-2 py-0.5 rounded border bg-amber-900/40 text-amber-300 border-amber-800 hover:underline"
            >
              Locked by instance {account.locked_by.slice(0, 8)}…
            </Link>
          )}
        </div>
        <div className="flex gap-2 flex-shrink-0">
          <button
            className="flex items-center gap-1.5 px-3 py-2 rounded text-sm font-medium text-gray-200 bg-gray-700 hover:bg-gray-600 disabled:opacity-60 transition-colors"
            onClick={() => refetchBrokerInfo()}
            disabled={fetchingBrokerInfo}
            title="Refresh broker data"
          >
            <RefreshCw size={14} className={fetchingBrokerInfo ? "animate-spin" : ""} />
            Refresh
          </button>
          <div className="relative">
            <button
              onClick={() => setShowActions(!showActions)}
              className="px-3 py-1.5 text-sm border border-gray-600 rounded-md text-gray-200 hover:bg-gray-700 transition-colors"
            >
              Actions ▾
            </button>
            {showActions && (
              <div className="absolute right-0 mt-1 w-56 bg-gray-800 border border-gray-700 rounded-md shadow-lg z-10">
                <div className="py-1">
                  <div className="px-4 py-1.5 text-xs text-gray-500 uppercase tracking-wide border-b border-gray-700">
                    Sync range
                  </div>
                  <select
                    value={syncRange}
                    onChange={(e) => setSyncRange(e.target.value as typeof syncRange)}
                    disabled={syncAccount.isPending}
                    className="w-full bg-gray-800 text-gray-200 text-sm px-4 py-2 border-b border-gray-700 focus:outline-none disabled:opacity-60"
                  >
                    <option value="incremental">Since last</option>
                    <option value="30d">Last 30 days</option>
                    <option value="90d">Last 90 days</option>
                    <option value="1y">Last 1 year</option>
                    <option value="all">All time</option>
                  </select>
                  <button
                    onClick={() => { void handleSync(); setShowActions(false); }}
                    disabled={syncAccount.isPending}
                    className="w-full text-left px-4 py-2 text-sm text-gray-200 hover:bg-gray-700 disabled:opacity-60 flex items-center gap-2 transition-colors"
                  >
                    <RotateCw size={14} className={syncAccount.isPending ? "animate-spin" : ""} />
                    {syncAccount.isPending ? "Syncing…" : "Force Sync"}
                  </button>
                </div>
              </div>
            )}
          </div>
          {/* Open Position */}
          <button
            className="flex items-center gap-1.5 px-3 py-2 rounded text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            onClick={() => setOpenPositionOpen(true)}
            disabled={!!account.locked_by}
            title={account.locked_by
              ? "Locked by algorithm. Stop the algo to open positions manually."
              : "Open a new position"}
          >
            Open Position
          </button>

          {/* Strategies (options only) */}
          {(account.supported_asset_types ?? []).includes("options") && (
            <button
              className="flex items-center gap-1.5 px-3 py-2 rounded text-sm font-medium text-gray-200 bg-gray-700 hover:bg-gray-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              onClick={() => navigate(`/accounts/${account.id}/strategies`)}
              disabled={!!account.locked_by}
              title={account.locked_by
                ? "Locked by algorithm. Stop the algo to use the strategy builder."
                : "Open the options strategy builder"}
            >
              Strategies
            </button>
          )}

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

      {/* Backfill progress banner */}
      {liveProgress && (
        <div className="bg-blue-950 border-l-4 border-blue-400 p-4 rounded">
          <div className="flex items-center gap-2">
            <svg className="animate-spin h-4 w-4 text-blue-400 flex-shrink-0" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            <p className="text-sm text-blue-300">{liveProgress.message}</p>
          </div>
        </div>
      )}

      {/* Portfolio Value */}
      <div className="bg-gray-900 border border-gray-800 rounded p-5">
        {brokerInfoError ? (
          <div className="text-sm text-red-400">
            Couldn't fetch live data from broker: {(brokerInfoError as Error).message}
          </div>
        ) : loadingBrokerInfo ? (
          <p className="text-sm text-gray-500">Fetching live broker data…</p>
        ) : accountInfo ? (
          <div className="grid grid-cols-3 gap-6">
            <div>
              <div className="text-xs uppercase text-gray-500 mb-1">Portfolio Value</div>
              <div className="text-3xl font-bold text-white">
                {fmtUsd(accountInfo.portfolio_value)}
              </div>
            </div>
            <div>
              <div className="text-xs uppercase text-gray-500 mb-1">Cash</div>
              <div className="text-2xl font-semibold text-gray-200">
                {fmtUsd(accountInfo.cash)}
              </div>
            </div>
            <div>
              <div className="text-xs uppercase text-gray-500 mb-1">Buying Power</div>
              <div className="text-2xl font-semibold text-gray-200">
                {fmtUsd(accountInfo.buying_power)}
              </div>
            </div>
          </div>
        ) : (
          <p className="text-sm text-gray-500">No broker data yet. Click Refresh to fetch.</p>
        )}
      </div>

      {/* Equity curve */}
      <div className="bg-gray-900 border border-gray-800 rounded p-3">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-gray-300">
            Estimated Value Over Time
            <span className="ml-2 text-[10px] uppercase tracking-wide text-gray-500">
              flat between cash events — improves as snapshots accumulate
            </span>
          </h3>
          <div className="flex bg-gray-800 rounded overflow-hidden">
            {(["30d", "90d", "1y", "all"] as const).map((r) => (
              <button
                key={r}
                onClick={() => setChartRange(r)}
                className={`px-2.5 py-1 text-xs font-medium transition-colors ${
                  chartRange === r
                    ? "bg-indigo-600 text-white"
                    : "text-gray-400 hover:text-white hover:bg-gray-700"
                }`}
              >
                {r.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
        {loadingEquity ? (
          <div className="text-xs text-gray-500 py-12 text-center">Building curve…</div>
        ) : equityPoints.length === 0 ? (
          <div className="text-xs text-gray-500 py-12 text-center">
            No data yet. Sync the account to populate.
          </div>
        ) : (
          <EquityCurve data={equityPoints} height={220} />
        )}
      </div>

      {/* Account Details — compact single row */}
      <div className="bg-gray-900 border border-gray-800 rounded px-3 py-2">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
          <DetailItem label="Broker">{account.broker_type}</DetailItem>
          <DetailItem label="Env">
            <span
              className={`text-[10px] px-1.5 py-0.5 rounded border ${
                account.environment === "live"
                  ? "bg-emerald-900/40 text-emerald-300 border-emerald-800"
                  : "bg-blue-900/40 text-blue-300 border-blue-800"
              }`}
            >
              {account.environment === "live" ? "LIVE" : "PAPER"}
            </span>
          </DetailItem>
          <DetailItem label="PDT">{account.pdt_mode}</DetailItem>
          <DetailItem label="Type">
            <span className="text-[10px] px-1.5 py-0.5 rounded border bg-gray-800 text-gray-400 border-gray-700">
              trading
            </span>
          </DetailItem>
          <DetailItem label="Assets">
            {(account.supported_asset_types ?? []).join(", ") || "—"}
          </DetailItem>
          <DetailItem label="Opt Lvl">
            {account.options_level !== null ? account.options_level : "—"}
          </DetailItem>
          <DetailItem label="Features">
            {(account.account_features ?? []).join(", ") || "—"}
          </DetailItem>
          <DetailItem label="In Overview">
            {account.show_in_overview ? "Yes" : "No"}
          </DetailItem>
          {account.locked_by && (
            <DetailItem label="Locked by">{account.locked_by}</DetailItem>
          )}
        </div>
      </div>

      {/* Positions */}
      <CollapsibleSection title="Positions" count={positions.length}>
        {brokerInfoError ? (
          <p className="text-sm text-gray-500">—</p>
        ) : (
          <DataTable
            data={positions}
            columns={positionColumns}
            isLoading={loadingBrokerInfo}
            emptyMessage="No open positions."
            enableSorting
          />
        )}
      </CollapsibleSection>

      <ConfirmDialog
        open={!!closeTarget}
        title="Close position"
        message={
          closeTarget
            ? `Close ${closeTarget.quantity} ${closeTarget.side} ${closeTarget.symbol} at market? ` +
              `This will submit a ${closeTarget.side === "short" ? "buy" : "sell"} order ` +
              `to ${account?.broker_type ?? "the broker"}.` +
              (closeError ? `\n\nError: ${closeError}` : "")
            : ""
        }
        confirmLabel={closePos.isPending ? "Closing…" : "Close position"}
        cancelLabel="Cancel"
        onConfirm={onConfirmClose}
        onCancel={() => { setCloseTarget(null); setCloseError(null); }}
      />

      {/* Trades */}
      <CollapsibleSection title="Trades" count={trades.length} maxHeight="400px">
        <DataTable
          data={trades}
          columns={tradeColumns}
          isLoading={loadingTrades}
          emptyMessage="No trades. Click Sync from Broker to import recent fills."
          enableSorting
        />
      </CollapsibleSection>

      {/* Cash Flows */}
      <CollapsibleSection
        title="Cash Flows"
        count={cashFlows.length}
        maxHeight="400px"
        actions={
          <button
            className="px-2.5 py-1 rounded text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-500 transition-colors"
            onClick={(e) => {
              e.stopPropagation();
              setCashFlowOpen(true);
            }}
          >
            Record
          </button>
        }
      >
        <DataTable
          data={cashFlows}
          columns={cashFlowColumns}
          isLoading={loadingCashFlows}
          emptyMessage="No cash flows recorded."
          enableSorting
        />
      </CollapsibleSection>

      {/* Instances */}
      <CollapsibleSection title="Instances" count={accountInstances.length} defaultOpen={false}>
        {accountInstances.length === 0 ? (
          <p className="text-sm text-gray-500">No instances running on this account.</p>
        ) : (
          <DataTable
            data={accountInstances}
            columns={instanceColumns}
            emptyMessage="No instances running on this account."
            onRowClick={(row) => navigate(`/deployments/${row.id}`)}
            enableSorting
          />
        )}
      </CollapsibleSection>

      {/* Edit Account Modal */}
      <FormModal
        open={editOpen}
        onClose={() => setEditOpen(false)}
        title="Edit Account"
        schema={accountEditSchema}
        defaultValues={{
          name: account.name,
          environment: account.environment,
          supported_asset_types: (account.supported_asset_types ?? []).join(", "),
          pdt_mode: account.pdt_mode,
          show_in_overview: account.show_in_overview,
        }}
        onSubmit={handleEdit}
        submitLabel="Save Changes"
        isSubmitting={updateAccount.isPending}
      >
        {(form) => {
          const env = form.watch("environment");
          return (
            <>
              <FormField label="Name" error={form.formState.errors.name?.message}>
                <input className={INPUT_CLASS} {...form.register("name")} />
              </FormField>

              <FormField label="Broker">
                <input
                  className={`${INPUT_CLASS} opacity-60 cursor-not-allowed`}
                  value={account.broker_type}
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

              <FormField
                label="Supported Asset Types"
                error={form.formState.errors.supported_asset_types?.message}
              >
                <input className={INPUT_CLASS} {...form.register("supported_asset_types")} />
              </FormField>

              <FormField label="PDT Mode" error={form.formState.errors.pdt_mode?.message}>
                <select className={INPUT_CLASS} {...form.register("pdt_mode")}>
                  <option value="off">Off</option>
                  <option value="warn">Warn</option>
                  <option value="block">Block</option>
                </select>
              </FormField>

              <FormField label="Overview">
                <label className="flex items-center gap-2 cursor-pointer text-sm text-gray-300">
                  <input
                    type="checkbox"
                    className="accent-indigo-500"
                    {...form.register("show_in_overview")}
                  />
                  Show in Overview
                </label>
              </FormField>

              <p className="text-xs text-gray-500 pt-1">
                To rotate credentials, edit this account from the Accounts list.
              </p>
            </>
          );
        }}
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

      {/* Open Position Modal */}
      {account && (
        <OpenPositionModal
          open={openPositionOpen}
          onClose={() => setOpenPositionOpen(false)}
          accountId={account.id}
          allowedAssetTypes={account.supported_asset_types ?? []}
        />
      )}
    </div>
  );
}
