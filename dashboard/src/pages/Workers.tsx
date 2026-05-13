import { useWorkers } from "../api/hooks";

export function Workers() {
  const { data: workers, isLoading } = useWorkers();

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-white">
        Workers{" "}
        {!isLoading && (
          <span className="text-gray-400 text-base font-normal">
            ({workers?.length ?? 0})
          </span>
        )}
      </h1>

      {isLoading ? (
        <p className="text-gray-400 text-sm">Loading…</p>
      ) : (
        <div className="space-y-2">
          {(workers ?? []).map((w) => (
            <div
              key={w.id}
              className="flex items-center justify-between bg-gray-900 border border-gray-800 rounded p-3"
            >
              <div>
                <span className="text-sm text-gray-200">{w.name}</span>
                <span className="ml-2 text-xs text-gray-500">
                  {w.tailscale_ip}
                </span>
              </div>
              <span className="text-xs text-gray-400">{w.status}</span>
            </div>
          ))}
          {(workers ?? []).length === 0 && (
            <p className="text-gray-500 text-sm">No workers registered.</p>
          )}
        </div>
      )}
    </div>
  );
}
