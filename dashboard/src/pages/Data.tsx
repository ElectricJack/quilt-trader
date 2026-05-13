import { useAvailableData } from "../api/hooks";

export function Data() {
  const { data: available, isLoading } = useAvailableData();

  const providers = available ? Object.entries(available) : [];

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-white">Market Data</h1>

      {isLoading ? (
        <p className="text-gray-400 text-sm">Loading…</p>
      ) : providers.length === 0 ? (
        <p className="text-gray-500 text-sm">No data sources available.</p>
      ) : (
        <div className="space-y-4">
          {providers.map(([provider, symbols]) => (
            <div
              key={provider}
              className="bg-gray-900 border border-gray-800 rounded p-4"
            >
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-lg font-semibold text-gray-200 capitalize">
                  {provider}
                </h2>
                <span className="text-xs text-gray-500">
                  {symbols.length} symbol{symbols.length !== 1 ? "s" : ""}
                </span>
              </div>
              {symbols.length === 0 ? (
                <p className="text-sm text-gray-500">No symbols available.</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {symbols.map((symbol) => (
                    <span
                      key={symbol}
                      className="bg-gray-800 border border-gray-700 rounded px-2 py-0.5 text-xs text-gray-300 font-mono"
                    >
                      {symbol}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
