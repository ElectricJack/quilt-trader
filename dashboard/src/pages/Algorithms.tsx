import { useAlgorithms } from "../api/hooks";

export function Algorithms() {
  const { data: algorithms, isLoading } = useAlgorithms();

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-white">
        Algorithms{" "}
        {!isLoading && (
          <span className="text-gray-400 text-base font-normal">
            ({algorithms?.length ?? 0})
          </span>
        )}
      </h1>

      {isLoading ? (
        <p className="text-gray-400 text-sm">Loading…</p>
      ) : (
        <div className="space-y-2">
          {(algorithms ?? []).map((a) => (
            <div
              key={a.id}
              className="flex items-center justify-between bg-gray-900 border border-gray-800 rounded p-3"
            >
              <div>
                <span className="text-sm text-gray-200">{a.name}</span>
                {a.version && (
                  <span className="ml-2 text-xs text-gray-500">
                    v{a.version}
                  </span>
                )}
              </div>
              <span className="text-xs text-gray-400">{a.install_status}</span>
            </div>
          ))}
          {(algorithms ?? []).length === 0 && (
            <p className="text-gray-500 text-sm">No algorithms installed.</p>
          )}
        </div>
      )}
    </div>
  );
}
