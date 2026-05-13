import { useAccounts } from "../api/hooks";
import { useWorkers } from "../api/hooks";
import { MetricsCard } from "../components/MetricsCard";
import { StatusBadge } from "../components/StatusBadge";

export function Overview() {
  const { data: accounts, isLoading: loadingAccounts } = useAccounts();
  const { data: workers, isLoading: loadingWorkers } = useWorkers();

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">Overview</h1>

      {/* Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <MetricsCard
          label="Accounts"
          value={loadingAccounts ? "–" : (accounts?.length ?? 0)}
        />
        <MetricsCard
          label="Workers"
          value={loadingWorkers ? "–" : (workers?.length ?? 0)}
        />
        <MetricsCard label="Active Algorithms" value="–" />
        <MetricsCard label="Open Positions" value="–" />
      </div>

      {/* Workers list */}
      <section>
        <h2 className="text-lg font-semibold text-gray-200 mb-3">Workers</h2>
        {loadingWorkers ? (
          <p className="text-gray-400 text-sm">Loading…</p>
        ) : (
          <div className="space-y-2">
            {(workers ?? []).map((w) => (
              <div
                key={w.id}
                className="flex items-center justify-between bg-gray-900 border border-gray-800 rounded p-3"
              >
                <span className="text-sm text-gray-200">{w.name}</span>
                <StatusBadge status={w.status} />
              </div>
            ))}
            {(workers ?? []).length === 0 && (
              <p className="text-gray-500 text-sm">No workers registered.</p>
            )}
          </div>
        )}
      </section>

      {/* Accounts list */}
      <section>
        <h2 className="text-lg font-semibold text-gray-200 mb-3">Accounts</h2>
        {loadingAccounts ? (
          <p className="text-gray-400 text-sm">Loading…</p>
        ) : (
          <div className="space-y-2">
            {(accounts ?? []).map((a) => (
              <div
                key={a.id}
                className="flex items-center justify-between bg-gray-900 border border-gray-800 rounded p-3"
              >
                <div>
                  <span className="text-sm text-gray-200">{a.name}</span>
                  <span className="ml-2 text-xs text-gray-500">
                    {a.broker_type}
                  </span>
                </div>
                <span className="text-xs text-gray-400">{a.pdt_mode}</span>
              </div>
            ))}
            {(accounts ?? []).length === 0 && (
              <p className="text-gray-500 text-sm">No accounts configured.</p>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
