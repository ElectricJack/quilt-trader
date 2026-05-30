// src/components/DatasetsFilterBar.tsx
import { Search } from "lucide-react";

const PROVIDER_COLORS: Record<string, { bg: string }> = {
  fmp: { bg: "bg-violet-500" },
};

interface Props {
  search: string;
  onSearch: (s: string) => void;
  provider: string | null;
  onProvider: (p: string | null) => void;
  providers: string[];
}

export function DatasetsFilterBar({ search, onSearch, provider, onProvider, providers }: Props) {
  return (
    <div className="flex items-center gap-3 flex-wrap">
      {/* Search */}
      <div className="relative">
        <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-500" />
        <input
          type="text"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="Search datasets…"
          className="bg-gray-800 border border-gray-700 text-gray-100 rounded pl-8 pr-3 py-1.5 text-sm w-48"
        />
      </div>

      {/* Provider chips */}
      {providers.length > 0 && (
        <div className="flex items-center gap-1">
          <button
            onClick={() => onProvider(null)}
            className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
              !provider
                ? "bg-gray-700 text-gray-100"
                : "bg-gray-900 text-gray-500 hover:text-gray-400"
            }`}
          >
            All
          </button>
          {providers.map((p) => {
            const active = provider === p;
            const colors = PROVIDER_COLORS[p] ?? { bg: "bg-gray-400" };
            return (
              <button
                key={p}
                onClick={() => onProvider(active ? null : p)}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                  active
                    ? "bg-gray-700 text-gray-100"
                    : "bg-gray-900 text-gray-500 hover:text-gray-400"
                }`}
              >
                <span className={`w-2 h-2 rounded-full ${colors.bg} ${active ? "" : "opacity-30"}`} />
                <span className="capitalize">{p}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
