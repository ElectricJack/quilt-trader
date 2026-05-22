import { useState, useMemo, useCallback } from "react";
import { Plus, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import {
  useLiveSubscriptions,
  useCreateLiveSubscription,
  useUnsubscribeLiveSubscription,
  useLiveSubStorageEstimate,
  useAccounts,
} from "../api/hooks";
import { useUIStore } from "../stores/ui";
import type { LiveSubscription } from "../api/client";

type AssetClass = "equities" | "crypto" | "options";

const PROVIDER_OPTIONS = [
  { value: "provider:polygon", label: "Polygon.io" },
  { value: "provider:thetadata", label: "ThetaData" },
  { value: "provider:coinbase", label: "Coinbase (free, crypto only)" },
] as const;

function formatBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function timeSince(iso: string | null): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60_000) return `${Math.floor(ms / 1000)}s ago`;
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`;
  return `${Math.floor(ms / 3_600_000)}h ago`;
}

function providerLabel(type: string): string {
  if (type === "polygon") return "Polygon.io";
  if (type === "thetadata") return "ThetaData";
  if (type === "coinbase") return "Coinbase";
  return type;
}

interface SubGroup {
  key: string;
  label: string;
  linkTo: string | null;
  subs: LiveSubscription[];
}

function groupSubscriptions(subs: LiveSubscription[]): SubGroup[] {
  const groups = new Map<string, SubGroup>();
  for (const s of subs) {
    let key: string;
    let label: string;
    let linkTo: string | null;
    if (s.account_id) {
      key = `account:${s.account_id}`;
      label = s.account_name ?? s.account_id;
      linkTo = `/accounts/${s.account_id}`;
    } else {
      const ptype = s.provider_type ?? s.broker;
      key = `provider:${ptype}`;
      label = providerLabel(ptype);
      linkTo = null;
    }
    let group = groups.get(key);
    if (!group) {
      group = { key, label, linkTo, subs: [] };
      groups.set(key, group);
    }
    group.subs.push(s);
  }
  return Array.from(groups.values());
}

function SubscriptionChip({
  sub,
  onDelete,
  onView,
}: {
  sub: LiveSubscription;
  onDelete: (id: string, label: string) => void;
  onView: (sub: LiveSubscription) => void;
}) {
  const stale =
    !sub.last_tick_at ||
    Date.now() - new Date(sub.last_tick_at).getTime() > 60_000;

  return (
    <div
      className="inline-flex items-center gap-2 bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm cursor-pointer hover:bg-gray-700 transition-colors"
      onClick={() => onView(sub)}
    >
      <span className="font-mono font-medium text-gray-200">{sub.symbol}</span>
      <span className="text-[10px] px-1 py-0.5 rounded border bg-gray-900 text-gray-400 border-gray-700">
        {sub.asset_class}
      </span>
      {sub.tick_rate_per_min != null && (
        <span className="text-[10px] text-gray-500">
          {sub.tick_rate_per_min >= 1
            ? `~${Math.round(sub.tick_rate_per_min)}/min`
            : `~${Math.round(sub.tick_rate_per_min * 60)}/hr`}
        </span>
      )}
      <span
        className={`w-1.5 h-1.5 rounded-full shrink-0 ${
          stale ? "bg-red-400" : "bg-green-400"
        }`}
        title={`last tick: ${timeSince(sub.last_tick_at)}`}
      />
      <button
        onClick={(e) => {
          e.stopPropagation();
          onDelete(sub.id, `${sub.symbol}`);
        }}
        className="text-gray-500 hover:text-red-400 transition-colors"
        title="Unsubscribe"
      >
        <Trash2 size={12} />
      </button>
    </div>
  );
}

export function LiveSubscriptionsSection() {
  const { data: subs = [], isLoading } = useLiveSubscriptions();
  const { data: accounts = [] } = useAccounts();
  const create = useCreateLiveSubscription();
  const unsub = useUnsubscribeLiveSubscription();
  const addAlert = useUIStore((s) => s.addAlert);
  const navigate = useNavigate();

  const handleViewSubscription = useCallback((sub: LiveSubscription) => {
    const source = sub.provider_type
      ? `${sub.provider_type}_live`
      : sub.account_id
      ? `${sub.broker}_live`
      : sub.broker;
    const tf = "1min";
    navigate(`/data?tab=available&search=${encodeURIComponent(sub.symbol)}&preview=${encodeURIComponent(`${source}:${sub.symbol}:${tf}`)}`);
  }, [navigate]);

  const [adding, setAdding] = useState(false);
  const [sourceValue, setSourceValue] = useState<string>("");
  const [assetClass, setAssetClass] = useState<AssetClass>("equities");
  const [symbol, setSymbol] = useState("");
  const [retention, setRetention] = useState(168);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const trimmedSymbol = symbol.trim();

  const isProviderSource = sourceValue.startsWith("provider:");
  const selectedAccountId =
    !isProviderSource && sourceValue.startsWith("account:")
      ? sourceValue.slice("account:".length)
      : null;
  const selectedProviderType = isProviderSource
    ? sourceValue.slice("provider:".length)
    : null;

  const estimateBroker = selectedProviderType ?? selectedAccountId ?? null;
  const { data: estimate } = useLiveSubStorageEstimate(
    adding && trimmedSymbol && estimateBroker ? estimateBroker : null,
    adding && trimmedSymbol ? trimmedSymbol : null,
    retention,
  );

  const groups = useMemo(() => groupSubscriptions(subs), [subs]);

  const toggleCollapsed = useCallback((key: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  async function handleAdd() {
    if (!trimmedSymbol || !sourceValue) return;
    try {
      let label: string;
      if (selectedProviderType) {
        label = `${providerLabel(selectedProviderType)}:${trimmedSymbol}`;
        await create.mutateAsync({
          provider_type: selectedProviderType,
          symbol: trimmedSymbol,
          asset_class: assetClass,
          tick_retention_hours: retention,
        });
      } else if (selectedAccountId) {
        const acct = accounts.find((a) => a.id === selectedAccountId);
        label = `${acct?.name ?? selectedAccountId}:${trimmedSymbol}`;
        await create.mutateAsync({
          account_id: selectedAccountId,
          symbol: trimmedSymbol,
          asset_class: assetClass,
          tick_retention_hours: retention,
        });
      } else {
        return;
      }
      addAlert({ message: `Subscribed to ${label}.`, severity: "success" });
      setAdding(false);
      setSymbol("");
    } catch (e) {
      addAlert({
        message: `Subscribe failed: ${(e as Error).message}`,
        severity: "error",
      });
    }
  }

  async function handleDelete(id: string, label: string) {
    try {
      const after = await unsub.mutateAsync(id);
      if ("deleted" in after && after.deleted) {
        addAlert({ message: `Unsubscribed from ${label}.`, severity: "success" });
      } else {
        const remaining = (after as { consumers: { consumer_type: string }[] })
          .consumers.filter((c) => c.consumer_type === "algo").length;
        addAlert({
          message: `Unsubscribed from ${label}; ${remaining} algorithm consumer(s) still holding the feed.`,
          severity: "info",
        });
      }
    } catch (e) {
      addAlert({
        message: `Unsubscribe failed: ${(e as Error).message}`,
        severity: "error",
      });
    }
  }

  return (
    <section>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-400 uppercase">
          Live Subscriptions{" "}
          {subs.length > 0 && (
            <span className="font-normal text-gray-500">({subs.length})</span>
          )}
        </h2>
        <button
          onClick={() => setAdding(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 transition-colors"
        >
          <Plus size={14} /> Subscribe
        </button>
      </div>

      {isLoading ? (
        <p className="text-gray-400 text-sm">Loading…</p>
      ) : subs.length === 0 ? (
        <p className="text-gray-500 text-sm">No live subscriptions.</p>
      ) : (
        <div className="space-y-2">
          {groups.map((group) => {
            const isCollapsed = collapsed.has(group.key);
            return (
              <div
                key={group.key}
                className="bg-gray-900 border border-gray-800 rounded"
              >
                <button
                  onClick={() => toggleCollapsed(group.key)}
                  className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-gray-800/50 transition-colors rounded"
                >
                  {isCollapsed ? (
                    <ChevronRight size={14} className="text-gray-500 shrink-0" />
                  ) : (
                    <ChevronDown size={14} className="text-gray-500 shrink-0" />
                  )}
                  {group.linkTo ? (
                    <Link
                      to={group.linkTo}
                      onClick={(e) => e.stopPropagation()}
                      className="text-sm font-medium text-indigo-400 hover:underline"
                    >
                      {group.label}
                    </Link>
                  ) : (
                    <span className="text-sm font-medium text-indigo-400">
                      {group.label}
                    </span>
                  )}
                  <span className="text-xs text-gray-500">
                    {group.subs.length} subscription{group.subs.length !== 1 ? "s" : ""}
                  </span>
                </button>

                {!isCollapsed && (
                  <div className="px-3 pb-2.5 flex flex-wrap gap-1.5">
                    {group.subs.map((s) => (
                      <SubscriptionChip
                        key={s.id}
                        sub={s}
                        onDelete={handleDelete}
                        onView={handleViewSubscription}
                      />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {adding && (
        <div className="mt-3 bg-gray-900 border border-gray-700 rounded p-3 flex gap-2 items-end flex-wrap">
          <label className="flex flex-col gap-1 text-xs text-gray-400">
            <span>Source</span>
            <select
              value={sourceValue}
              onChange={(e) => {
                setSourceValue(e.target.value);
                if (e.target.value === "provider:coinbase") {
                  setAssetClass("crypto");
                }
              }}
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
            >
              <option value="">— select source —</option>
              {accounts.length > 0 && (
                <optgroup label="Accounts">
                  {accounts.map((a) => (
                    <option key={a.id} value={`account:${a.id}`}>
                      {a.name} [{a.broker_type}]
                    </option>
                  ))}
                </optgroup>
              )}
              <optgroup label="Data Providers">
                {PROVIDER_OPTIONS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </optgroup>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-gray-400">
            <span>Asset class</span>
            <select
              value={assetClass}
              onChange={(e) => setAssetClass(e.target.value as AssetClass)}
              disabled={selectedProviderType === "coinbase"}
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 disabled:opacity-50"
            >
              <option value="equities">equities</option>
              <option value="crypto">crypto</option>
              <option value="options">options</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-gray-400">
            <span>Symbol</span>
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              placeholder="SPY"
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 w-28 font-mono"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-gray-400">
            <span>Tick retention</span>
            <select
              value={retention}
              onChange={(e) => setRetention(Number(e.target.value))}
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
            >
              <option value={24}>24h</option>
              <option value={168}>7d</option>
              <option value={720}>30d</option>
              <option value={8760}>1y</option>
            </select>
          </label>
          {estimate && (
            <span className="text-xs text-gray-500 self-center ml-2">
              ~{formatBytes(estimate.projected_bytes)}
            </span>
          )}
          <button
            onClick={handleAdd}
            disabled={!trimmedSymbol || !sourceValue || create.isPending}
            className="px-3 py-1.5 rounded text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 transition-colors"
          >
            Add
          </button>
          <button
            onClick={() => {
              setAdding(false);
              setSymbol("");
              setSourceValue("");
            }}
            className="px-3 py-1.5 rounded text-sm text-gray-300 bg-gray-700 hover:bg-gray-600 transition-colors"
          >
            Cancel
          </button>
        </div>
      )}
    </section>
  );
}
