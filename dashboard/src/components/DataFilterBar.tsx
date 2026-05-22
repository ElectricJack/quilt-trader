// src/components/DataFilterBar.tsx
import { Search } from "lucide-react";
import type { AssetType } from "../hooks/useProcessedCoverage";

const PROVIDER_COLORS: Record<string, { bg: string; ring: string }> = {
  polygon: { bg: "bg-indigo-500", ring: "ring-indigo-500" },
  tradier: { bg: "bg-emerald-500", ring: "ring-emerald-500" },
  coinbase: { bg: "bg-amber-500", ring: "ring-amber-500" },
  alpaca: { bg: "bg-sky-500", ring: "ring-sky-500" },
};

const ASSET_TYPE_LABELS: Record<AssetType, string> = {
  equities: "Equities",
  options: "Options",
  crypto: "Crypto",
};

interface DataFilterBarProps {
  searchText: string;
  onSearchChange: (text: string) => void;
  assetTypes: Set<AssetType>;
  onToggleAssetType: (type: AssetType) => void;
  providers: string[];
  activeProviders: Set<string>;
  onToggleProvider: (provider: string) => void;
}

export function DataFilterBar({
  searchText,
  onSearchChange,
  assetTypes,
  onToggleAssetType,
  providers,
  activeProviders,
  onToggleProvider,
}: DataFilterBarProps) {
  return (
    <div className="flex items-center gap-3 flex-wrap">
      {/* Search */}
      <div className="relative">
        <Search
          size={14}
          className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-500"
        />
        <input
          type="text"
          value={searchText}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="Filter symbols…"
          className="bg-gray-800 border border-gray-700 text-gray-100 rounded pl-8 pr-3 py-1.5 text-sm w-48"
        />
      </div>

      {/* Asset type chips */}
      <div className="flex items-center gap-1">
        {(Object.keys(ASSET_TYPE_LABELS) as AssetType[]).map((type) => {
          const active = assetTypes.has(type);
          return (
            <button
              key={type}
              onClick={() => onToggleAssetType(type)}
              className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                active
                  ? "bg-gray-700 text-gray-100"
                  : "bg-gray-900 text-gray-500 hover:text-gray-400"
              }`}
            >
              {ASSET_TYPE_LABELS[type]}
            </button>
          );
        })}
      </div>

      {/* Divider */}
      <div className="w-px h-5 bg-gray-700" />

      {/* Provider chips (colored as legend) */}
      <div className="flex items-center gap-1">
        {providers.map((p) => {
          const active = activeProviders.has(p);
          const colors = PROVIDER_COLORS[p] ?? {
            bg: "bg-gray-400",
            ring: "ring-gray-400",
          };
          return (
            <button
              key={p}
              onClick={() => onToggleProvider(p)}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                active
                  ? "bg-gray-700 text-gray-100"
                  : "bg-gray-900 text-gray-500 hover:text-gray-400"
              }`}
            >
              <span
                className={`w-2 h-2 rounded-full ${colors.bg} ${
                  active ? "" : "opacity-30"
                }`}
              />
              <span className="capitalize">{p}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
