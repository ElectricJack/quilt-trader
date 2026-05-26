import { useState, useRef, useEffect } from "react";
import { api } from "../api/client";
import { useProviders } from "../api/hooks";
import { Search, X, Plus } from "lucide-react";

interface Props {
  value: string;
  onChange: (value: string) => void;
}

interface SearchResult {
  symbol: string;
  name: string;
  type: string;
}

const TYPE_LABELS: Record<string, string> = {
  CS: "Stock",
  ETF: "ETF",
  ADRC: "ADR",
  INDEX: "Index",
  CRYPTO: "Crypto",
};

export function SymbolSearch({ value, onChange }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();
  const { data: providersData } = useProviders();
  const providerNames = providersData?.providers?.map((p) => p.name) ?? [];

  const selectedSymbols = value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  useEffect(() => {
    if (!query || query.length < 1) {
      setResults([]);
      return;
    }
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        // Search using the first provider that supports search (polygon)
        const searchProvider = providerNames.find((p) =>
          ["polygon"].includes(p)
        ) ?? "polygon";
        const data = await api.searchSymbols(query, searchProvider, 15);
        setResults(data.results ?? []);
      } catch {
        setResults([]);
      }
      setLoading(false);
    }, 300);
    return () => clearTimeout(debounceRef.current);
  }, [query]);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node) &&
        inputRef.current &&
        !inputRef.current.contains(e.target as Node)
      ) {
        setShowDropdown(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const addSymbol = (sym: string) => {
    if (selectedSymbols.includes(sym)) return;
    const next = [...selectedSymbols, sym].join(", ");
    onChange(next);
    setQuery("");
    setResults([]);
    inputRef.current?.focus();
  };

  const removeSymbol = (sym: string) => {
    const next = selectedSymbols.filter((s) => s !== sym).join(", ");
    onChange(next);
  };

  return (
    <div className="space-y-2">
      {/* Selected symbols as chips */}
      {selectedSymbols.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {selectedSymbols.map((sym) => (
            <span
              key={sym}
              className="inline-flex items-center gap-1 px-2 py-0.5 bg-indigo-900/40 text-indigo-300 text-xs rounded font-mono"
            >
              {sym}
              <button
                type="button"
                onClick={() => removeSymbol(sym)}
                className="hover:text-white"
              >
                <X size={10} />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Search input */}
      <div className="relative">
        <Search
          size={14}
          className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-500"
        />
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setShowDropdown(true);
          }}
          onFocus={() => results.length > 0 && setShowDropdown(true)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              if (query.trim()) {
                addSymbol(query.trim().toUpperCase());
              }
            }
          }}
          placeholder="Search symbols or type directly…"
          className="w-full bg-gray-800 border border-gray-700 rounded pl-8 pr-3 py-2 text-sm text-gray-100"
        />
        {loading && (
          <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-gray-500">
            searching…
          </span>
        )}
      </div>

      {/* Results dropdown */}
      {showDropdown && results.length > 0 && (
        <div
          ref={dropdownRef}
          className="bg-gray-800 border border-gray-700 rounded max-h-56 overflow-y-auto"
        >
          {results.map((r) => {
            const already = selectedSymbols.includes(r.symbol);
            return (
              <button
                key={r.symbol}
                type="button"
                onClick={() => !already && addSymbol(r.symbol)}
                disabled={already}
                className={`w-full flex items-center gap-3 px-3 py-2 text-left text-sm hover:bg-gray-700/50 transition-colors ${
                  already ? "opacity-40" : ""
                }`}
              >
                <span className="font-mono text-gray-100 w-16 shrink-0">
                  {r.symbol}
                </span>
                <span className="text-gray-400 truncate flex-1">
                  {r.name}
                </span>
                <span className="text-[10px] text-gray-500 shrink-0">
                  {TYPE_LABELS[r.type] ?? r.type}
                </span>
                {!already && (
                  <Plus size={12} className="text-gray-500 shrink-0" />
                )}
              </button>
            );
          })}
        </div>
      )}

      {/* Hidden input to keep form value in sync */}
      <input type="hidden" value={value} />
    </div>
  );
}
