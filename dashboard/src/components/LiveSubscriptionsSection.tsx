import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import {
  useLiveSubscriptions,
  useCreateLiveSubscription,
  useUnsubscribeLiveSubscription,
  useLiveSubStorageEstimate,
  useAccounts,
} from "../api/hooks";
import { useUIStore } from "../stores/ui";

type AssetClass = "equities" | "crypto" | "options";

// Source selector values encode whether we're using an account or a provider.
// Format: "account:<id>" or "provider:<type>".
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

/** Human-readable label for a source value like "provider:polygon". */
function providerLabel(type: string): string {
  if (type === "polygon") return "Polygon.io";
  if (type === "thetadata") return "ThetaData";
  if (type === "coinbase") return "Coinbase";
  return type;
}

export function LiveSubscriptionsSection() {
  const { data: subs = [], isLoading } = useLiveSubscriptions();
  const { data: accounts = [] } = useAccounts();
  const create = useCreateLiveSubscription();
  const unsub = useUnsubscribeLiveSubscription();
  const addAlert = useUIStore((s) => s.addAlert);

  const [adding, setAdding] = useState(false);
  // sourceValue is "account:<id>" or "provider:<type>" or ""
  const [sourceValue, setSourceValue] = useState<string>("");
  const [assetClass, setAssetClass] = useState<AssetClass>("equities");
  const [symbol, setSymbol] = useState("");
  const [retention, setRetention] = useState(168);

  const trimmedSymbol = symbol.trim();

  // Derive accountId / providerType from the compound selector value.
  const isProviderSource = sourceValue.startsWith("provider:");
  const selectedAccountId = !isProviderSource && sourceValue.startsWith("account:")
    ? sourceValue.slice("account:".length)
    : null;
  const selectedProviderType = isProviderSource
    ? sourceValue.slice("provider:".length)
    : null;

  // Storage estimate: pass broker string for provider, or account id for account.
  const estimateBroker = selectedProviderType ?? selectedAccountId ?? null;
  const { data: estimate } = useLiveSubStorageEstimate(
    adding && trimmedSymbol && estimateBroker ? estimateBroker : null,
    adding && trimmedSymbol ? trimmedSymbol : null,
    retention
  );

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
      addAlert({
        message: `Subscribed to ${label}.`,
        severity: "success",
      });
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
        const remaining = (after as { consumers: { consumer_type: string }[] }).consumers
          .filter((c) => c.consumer_type === "algo").length;
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
          {subs.map((s) => {
            // Source label: account link or provider name.
            const isAccountSub = s.account_id != null;
            const label = isAccountSub
              ? `${s.account_name ?? s.account_id}:${s.symbol}`
              : `${providerLabel(s.provider_type ?? s.broker)}:${s.symbol}`;
            const stale = !s.last_tick_at ||
              (Date.now() - new Date(s.last_tick_at).getTime()) > 60_000;
            return (
              <div
                key={s.id}
                className="bg-gray-900 border border-gray-800 rounded px-3 py-2"
              >
                <div className="flex items-center justify-between text-sm">
                  <div className="flex items-center gap-3 flex-wrap min-w-0">
                    {isAccountSub ? (
                      <Link
                        to={`/accounts/${s.account_id}`}
                        className="text-indigo-400 font-mono hover:underline"
                      >
                        {s.account_name}
                      </Link>
                    ) : (
                      <span className="text-indigo-400 font-mono">
                        {providerLabel(s.provider_type ?? s.broker)}
                      </span>
                    )}
                    <span className="font-mono text-gray-200">{s.symbol}</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded border bg-gray-800 text-gray-400 border-gray-700">
                      {s.asset_class}
                    </span>
                    {s.tick_rate_per_min != null && (
                      <span className="text-xs text-gray-500">
                        {s.tick_rate_per_min >= 1
                          ? `~${Math.round(s.tick_rate_per_min)}/min`
                          : `~${Math.round(s.tick_rate_per_min * 60)}/hr`}
                      </span>
                    )}
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded border ${
                        stale
                          ? "bg-red-900/40 text-red-300 border-red-800"
                          : "bg-green-900/40 text-green-300 border-green-800"
                      }`}
                    >
                      last tick: {timeSince(s.last_tick_at)}
                    </span>
                  </div>
                  <button
                    onClick={() => handleDelete(s.id, label)}
                    className="text-gray-400 hover:text-red-400 transition-colors"
                    title="Unsubscribe"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
                {s.consumers.length > 0 && (
                  <div className="mt-1.5 text-xs text-gray-500">
                    Consumers:{" "}
                    {s.consumers.map((c, i) => (
                      <span key={c.id}>
                        {i > 0 && ", "}
                        {c.consumer_type === "manual"
                          ? "manual"
                          : c.algorithm_id && c.algorithm_name
                          ? (
                            <Link
                              to={`/algorithms/${c.algorithm_id}`}
                              className="text-indigo-400 hover:underline"
                            >
                              {c.algorithm_name}
                            </Link>
                          )
                          : `algo: ${c.consumer_id?.slice(0, 8) ?? "?"}`}
                      </span>
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
                // Coinbase only supports crypto — force the asset class.
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
            onClick={() => { setAdding(false); setSymbol(""); setSourceValue(""); }}
            className="px-3 py-1.5 rounded text-sm text-gray-300 bg-gray-700 hover:bg-gray-600 transition-colors"
          >
            Cancel
          </button>
        </div>
      )}
    </section>
  );
}
