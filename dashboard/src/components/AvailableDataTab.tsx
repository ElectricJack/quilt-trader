// src/components/AvailableDataTab.tsx
import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { ChevronDown, ChevronRight, X } from "lucide-react";
import { useCoverage, useAvailableData, useFillGaps, useDownloads } from "../api/hooks";
import { useProcessedCoverage, type AssetType } from "../hooks/useProcessedCoverage";
import { TimeWindowControls } from "./TimeWindowControls";
import { DataFilterBar } from "./DataFilterBar";
import { InteractiveCoverageBar } from "./InteractiveCoverageBar";
import { DatasetPreviewModal } from "./DatasetPreviewModal";
import {
  CompareView,
  encodeCompareDataset,
  decodeCompareDataset,
  type CompareDataset,
  type CompareMode,
} from "./CompareView";
import { useUIStore } from "../stores/ui";

function readCompareFromUrl(): { datasets: CompareDataset[]; mode: CompareMode; open: boolean } {
  if (typeof window === "undefined") return { datasets: [], mode: "overlay", open: false };
  const params = new URLSearchParams(window.location.search);
  const compareRaw = params.get("compare");
  const modeRaw = params.get("mode");
  const datasets: CompareDataset[] = compareRaw
    ? compareRaw.split(",").map(decodeCompareDataset).filter((d): d is CompareDataset => d != null)
    : [];
  const mode: CompareMode = modeRaw === "stacked" ? modeRaw : "overlay";
  return { datasets, mode, open: datasets.length > 0 };
}

function writeCompareToUrl(datasets: CompareDataset[], mode: CompareMode, open: boolean): void {
  if (typeof window === "undefined") return;
  const params = new URLSearchParams(window.location.search);
  if (open && datasets.length > 0) {
    params.set("compare", datasets.map(encodeCompareDataset).join(","));
    params.set("mode", mode);
  } else {
    params.delete("compare");
    params.delete("mode");
  }
  const qs = params.toString();
  const next = `${window.location.pathname}${qs ? `?${qs}` : ""}`;
  window.history.replaceState({}, "", next);
}

const ACTIVE_STATUSES = new Set(["pending", "running", "queued"]);

export function AvailableDataTab() {
  const { data: coverageData, isLoading: coverageLoading, refetch: refetchCoverage } = useCoverage();
  const { isLoading: availableLoading } = useAvailableData();
  const fillGapsMutation = useFillGaps();
  const { data: downloads, refetch: refetchDownloads } = useDownloads();
  const addAlert = useUIStore((s) => s.addAlert);

  const activeDownloadCount = useMemo(
    () => (downloads ?? []).filter((d) => ACTIVE_STATUSES.has(d.status)).length,
    [downloads],
  );
  const prevActiveRef = useRef(0);

  // Poll downloads while any are active; refetch coverage when they finish
  useEffect(() => {
    if (activeDownloadCount === 0) {
      if (prevActiveRef.current > 0) {
        void refetchCoverage();
      }
      prevActiveRef.current = 0;
      return;
    }
    prevActiveRef.current = activeDownloadCount;
    const id = setInterval(() => void refetchDownloads(), 3000);
    return () => clearInterval(id);
  }, [activeDownloadCount, refetchDownloads, refetchCoverage]);

  const processed = useProcessedCoverage(coverageData);

  // Time window state
  const [windowStart, setWindowStart] = useState<string | null>(null);
  const [windowEnd, setWindowEnd] = useState<string | null>(null);
  const effectiveStart = windowStart ?? processed?.globalMin ?? "";
  const effectiveEnd = windowEnd ?? processed?.globalMax ?? "";

  const handleWindowChange = useCallback((start: string, end: string) => {
    setWindowStart(start);
    setWindowEnd(end);
  }, []);

  // Filter state
  const [searchText, setSearchText] = useState("");
  const [assetTypes, setAssetTypes] = useState<Set<AssetType>>(new Set(["equities", "options", "crypto"]));
  const [activeProviders, setActiveProviders] = useState<Set<string> | null>(null);
  const effectiveProviders = activeProviders ?? new Set(processed?.providers ?? []);

  const toggleAssetType = useCallback((type: AssetType) => {
    setAssetTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  }, []);

  const toggleProvider = useCallback((provider: string) => {
    setActiveProviders((prev) => {
      const base = prev ?? new Set(processed?.providers ?? []);
      const next = new Set(base);
      if (next.has(provider)) next.delete(provider);
      else next.add(provider);
      return next;
    });
  }, [processed]);

  // Expanded options groups
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const toggleGroup = useCallback((underlying: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(underlying)) next.delete(underlying);
      else next.add(underlying);
      return next;
    });
  }, []);

  // Selection state (compare + bulk actions)
  const initialCompare = useMemo(readCompareFromUrl, []);
  const [selected, setSelected] = useState<CompareDataset[]>(initialCompare.datasets);
  const [compareOpen, setCompareOpen] = useState(initialCompare.open);
  const [compareMode, setCompareMode] = useState<CompareMode>(initialCompare.mode);

  useEffect(() => {
    writeCompareToUrl(selected, compareMode, compareOpen);
  }, [selected, compareMode, compareOpen]);

  const selectionKey = useCallback(
    (d: { provider: string; symbol: string; timeframe: string }) =>
      `${d.provider}:${d.symbol}:${d.timeframe}`,
    [],
  );
  const selectedKeys = useMemo(() => new Set(selected.map(selectionKey)), [selected, selectionKey]);
  const toggleSelected = useCallback(
    (d: CompareDataset) => {
      const k = selectionKey(d);
      setSelected((prev) =>
        prev.some((p) => selectionKey(p) === k)
          ? prev.filter((p) => selectionKey(p) !== k)
          : [...prev, d],
      );
    },
    [selectionKey],
  );
  const clearSelection = useCallback(() => setSelected([]), []);

  // Preview state
  const [preview, setPreview] = useState<{
    provider: string; symbol: string; timeframe: string; targetDate?: string;
  } | null>(null);
  const [markerDate, setMarkerDate] = useState<string | null>(null);

  // Fill-gaps bulk action
  const [fillGapsOpen, setFillGapsOpen] = useState(false);
  const [fillStart, setFillStart] = useState("");
  const [fillEnd, setFillEnd] = useState("");

  const handleBulkFillGaps = useCallback(async () => {
    const start = fillStart || effectiveStart;
    const end = fillEnd || effectiveEnd;
    let total = 0;
    for (const ds of selected) {
      try {
        const result = await fillGapsMutation.mutateAsync({
          provider: ds.provider,
          symbol: ds.symbol,
          start,
          end,
          timeframe: ds.timeframe,
        });
        total += result.gap_count;
      } catch (e) {
        addAlert({ message: `Fill gaps failed for ${ds.symbol}: ${(e as Error).message}`, severity: "error" });
      }
    }
    if (total > 0) {
      addAlert({ message: `Queued ${total} download${total === 1 ? "" : "s"} to fill gaps.`, severity: "success" });
    } else {
      addAlert({ message: "All selected assets are fully covered.", severity: "success" });
    }
    setFillGapsOpen(false);
  }, [selected, fillStart, fillEnd, effectiveStart, effectiveEnd, fillGapsMutation, addAlert]);

  // Filter rows
  const filteredRows = useMemo(() => {
    if (!processed) return [];
    const search = searchText.toLowerCase();
    return processed.rows.filter((row) => {
      if (!assetTypes.has(row.assetType)) return false;
      if (row.kind === "multi-provider") {
        if (!row.children.some((c) => effectiveProviders.has(c.provider))) return false;
      } else if (!effectiveProviders.has(row.provider)) return false;
      if (search) {
        if (row.kind === "options-group") {
          return (
            row.underlying.toLowerCase().includes(search) ||
            row.children.some((c) => c.symbol.toLowerCase().includes(search) || c.readableLabel.toLowerCase().includes(search))
          );
        }
        if (row.kind === "multi-provider") {
          return row.symbol.toLowerCase().includes(search);
        }
        return row.symbol.toLowerCase().includes(search);
      }
      return true;
    });
  }, [processed, searchText, assetTypes, effectiveProviders]);

  function makeCompareDataset(provider: string, symbol: string, timeframes: string[]): CompareDataset {
    const tf = timeframes.includes("1min") ? "1min" : timeframes[0] ?? "1min";
    return { provider, symbol, timeframe: tf };
  }

  function handleBarClick(provider: string, symbol: string, timeframes: string[], date: string) {
    const tf = timeframes.includes("1min") ? "1min" : timeframes[0] ?? "1min";
    setMarkerDate(date);
    setPreview({ provider, symbol, timeframe: tf, targetDate: date });
  }

  if (coverageLoading || availableLoading) {
    return <p className="text-gray-400 text-sm">Loading…</p>;
  }
  if (!processed) {
    return <p className="text-gray-500 text-sm">No data sources available.</p>;
  }

  return (
    <div className="space-y-4">
      <TimeWindowControls
        globalMin={processed.globalMin}
        globalMax={processed.globalMax}
        windowStart={effectiveStart}
        windowEnd={effectiveEnd}
        onWindowChange={handleWindowChange}
      />

      <DataFilterBar
        searchText={searchText}
        onSearchChange={setSearchText}
        assetTypes={assetTypes}
        onToggleAssetType={toggleAssetType}
        providers={processed.providers}
        activeProviders={effectiveProviders}
        onToggleProvider={toggleProvider}
      />

      {/* Bulk action bar */}
      {selected.length > 0 && (
        <div className="flex items-center gap-3 bg-gray-900 border border-gray-800 rounded px-3 py-2">
          <span className="text-sm text-gray-300">{selected.length} selected</span>
          <button onClick={clearSelection} className="text-xs text-gray-400 hover:text-gray-200 transition-colors">Clear</button>
          <div className="w-px h-4 bg-gray-700" />
          <button
            onClick={() => setCompareOpen(true)}
            disabled={selected.length < 2}
            className="px-2.5 py-1 rounded text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            Compare
          </button>
          <button
            onClick={() => {
              setFillStart(effectiveStart);
              setFillEnd(effectiveEnd);
              setFillGapsOpen(!fillGapsOpen);
            }}
            className="px-2.5 py-1 rounded text-xs font-medium text-gray-200 bg-gray-700 hover:bg-gray-600 transition-colors"
          >
            Fill Gaps
          </button>
          {fillGapsOpen && (
            <div className="flex items-center gap-2 ml-2">
              <input type="date" value={fillStart} onChange={(e) => setFillStart(e.target.value)} className="bg-gray-800 border border-gray-700 text-gray-100 rounded px-2 py-1 text-xs" />
              <span className="text-xs text-gray-500">to</span>
              <input type="date" value={fillEnd} onChange={(e) => setFillEnd(e.target.value)} className="bg-gray-800 border border-gray-700 text-gray-100 rounded px-2 py-1 text-xs" />
              <button onClick={handleBulkFillGaps} disabled={fillGapsMutation.isPending} className="px-2.5 py-1 rounded text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 transition-colors">Go</button>
            </div>
          )}
        </div>
      )}

      {/* Asset rows */}
      <div className="space-y-px">
        {filteredRows.length === 0 ? (
          <p className="text-gray-500 text-sm py-4">No matching assets.</p>
        ) : (
          filteredRows.map((row) => {
            if (row.kind === "options-group") {
              const isExpanded = expandedGroups.has(row.underlying);
              const ds = makeCompareDataset(row.provider, row.underlying, row.timeframes);
              const isSelected = selectedKeys.has(selectionKey(ds));
              return (
                <div key={`optgrp-${row.underlying}-${row.provider}`}>
                  <div className="flex items-center gap-3 px-3 py-2 bg-gray-900 hover:bg-gray-800/50 transition-colors rounded-sm">
                    <input type="checkbox" checked={isSelected} onChange={() => toggleSelected(ds)} className="accent-indigo-500 shrink-0" />
                    <button onClick={() => toggleGroup(row.underlying)} className="flex items-center gap-1.5 text-sm font-mono text-gray-200 hover:text-white shrink-0 w-44 text-left">
                      {isExpanded ? <ChevronDown size={12} className="text-gray-500" /> : <ChevronRight size={12} className="text-gray-500" />}
                      {row.label}
                    </button>
                    <InteractiveCoverageBar ranges={row.ranges} provider={row.provider} windowStart={effectiveStart} windowEnd={effectiveEnd} markerDate={markerDate} onClick={(date) => handleBarClick(row.provider, row.children[0]?.symbol ?? row.underlying, row.timeframes, date)} />
                    <div className="hidden sm:flex gap-1 shrink-0">
                      {row.timeframes.map((tf) => <span key={tf} className="text-[10px] font-mono text-gray-500 bg-gray-800 px-1 py-0.5 rounded">{tf}</span>)}
                    </div>
                  </div>
                  {isExpanded && (
                    <div className="ml-6">
                      {row.children.map((child) => {
                        const childDs = makeCompareDataset(child.provider, child.symbol, child.timeframes);
                        const childSelected = selectedKeys.has(selectionKey(childDs));
                        return (
                          <div key={child.symbol} className="flex items-center gap-3 px-3 py-1.5 bg-gray-950 hover:bg-gray-900/50 transition-colors">
                            <input type="checkbox" checked={childSelected} onChange={() => toggleSelected(childDs)} className="accent-indigo-500 shrink-0" />
                            <span className="text-xs font-mono text-gray-400 w-44 truncate shrink-0 cursor-pointer hover:text-gray-200" onClick={() => handleBarClick(child.provider, child.symbol, child.timeframes, effectiveStart)} title={child.symbol}>
                              {child.readableLabel}
                            </span>
                            <InteractiveCoverageBar ranges={child.ranges} provider={child.provider} windowStart={effectiveStart} windowEnd={effectiveEnd} markerDate={markerDate} onClick={(date) => handleBarClick(child.provider, child.symbol, child.timeframes, date)} />
                            <div className="hidden sm:flex gap-1 shrink-0">
                              {child.timeframes.map((tf) => <span key={tf} className="text-[10px] font-mono text-gray-500 bg-gray-800 px-1 py-0.5 rounded">{tf}</span>)}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            }
            if (row.kind === "multi-provider") {
              const isExpanded = expandedGroups.has(row.symbol);
              const ds = makeCompareDataset(row.provider, row.symbol, row.timeframes);
              const isSelected = selectedKeys.has(selectionKey(ds));
              return (
                <div key={`multi-${row.symbol}`}>
                  <div className="flex items-center gap-3 px-3 py-2 bg-gray-900 hover:bg-gray-800/50 transition-colors rounded-sm">
                    <input type="checkbox" checked={isSelected} onChange={() => toggleSelected(ds)} className="accent-indigo-500 shrink-0" />
                    <button onClick={() => toggleGroup(row.symbol)} className="flex items-center gap-1.5 text-sm font-mono font-semibold text-gray-200 hover:text-white shrink-0 w-44 text-left">
                      {isExpanded ? <ChevronDown size={12} className="text-gray-500" /> : <ChevronRight size={12} className="text-gray-500" />}
                      {row.label}
                    </button>
                    <InteractiveCoverageBar ranges={row.ranges} provider={row.provider} windowStart={effectiveStart} windowEnd={effectiveEnd} markerDate={markerDate} onClick={(date) => handleBarClick(row.provider, row.symbol, row.timeframes, date)} />
                    <div className="hidden sm:flex gap-1 shrink-0">
                      {row.timeframes.map((tf) => <span key={tf} className="text-[10px] font-mono text-gray-500 bg-gray-800 px-1 py-0.5 rounded">{tf}</span>)}
                    </div>
                  </div>
                  {isExpanded && (
                    <div className="ml-6">
                      {row.children.map((child) => {
                        const childDs: CompareDataset = { provider: child.provider, symbol: row.symbol, timeframe: child.timeframe };
                        const childSelected = selectedKeys.has(selectionKey(childDs));
                        return (
                          <div key={`${child.provider}-${child.timeframe}`} className="flex items-center gap-3 px-3 py-1.5 bg-gray-950 hover:bg-gray-900/50 transition-colors">
                            <input type="checkbox" checked={childSelected} onChange={() => toggleSelected(childDs)} className="accent-indigo-500 shrink-0" />
                            <span className="text-xs font-mono text-gray-400 w-44 truncate shrink-0 cursor-pointer hover:text-gray-200 capitalize" onClick={() => handleBarClick(child.provider, row.symbol, [child.timeframe], effectiveStart)}>
                              {child.provider} · {child.timeframe}
                            </span>
                            <InteractiveCoverageBar ranges={child.ranges} provider={child.provider} windowStart={effectiveStart} windowEnd={effectiveEnd} markerDate={markerDate} onClick={(date) => handleBarClick(child.provider, row.symbol, [child.timeframe], date)} />
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            }
            const ds = makeCompareDataset(row.provider, row.symbol, row.timeframes);
            const isSelected = selectedKeys.has(selectionKey(ds));
            return (
              <div key={`${row.provider}-${row.symbol}`} className="flex items-center gap-3 px-3 py-2 bg-gray-900 hover:bg-gray-800/50 transition-colors rounded-sm">
                <input type="checkbox" checked={isSelected} onChange={() => toggleSelected(ds)} className="accent-indigo-500 shrink-0" />
                <span className="text-sm font-mono font-semibold text-gray-200 hover:text-white w-44 text-left shrink-0 cursor-pointer truncate" onClick={() => handleBarClick(row.provider, row.symbol, row.timeframes, effectiveStart)} title={row.symbol}>
                  {row.symbol}
                </span>
                <InteractiveCoverageBar ranges={row.ranges} provider={row.provider} windowStart={effectiveStart} windowEnd={effectiveEnd} markerDate={markerDate} onClick={(date) => handleBarClick(row.provider, row.symbol, row.timeframes, date)} />
                <div className="hidden sm:flex gap-1 shrink-0">
                  {row.timeframes.map((tf) => <span key={tf} className="text-[10px] font-mono text-gray-500 bg-gray-800 px-1 py-0.5 rounded">{tf}</span>)}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Compare modal */}
      {compareOpen && (
        <div className="fixed inset-0 z-50 bg-black/70 flex flex-col" role="dialog" aria-modal="true">
          <div className="flex items-center justify-between px-6 py-3 bg-gray-900 border-b border-gray-800">
            <div className="flex items-center gap-3 min-w-0">
              <h3 className="text-base font-semibold text-white">Compare Datasets</h3>
              <span className="text-xs text-gray-400">{selected.length} selected</span>
            </div>
            <button onClick={() => setCompareOpen(false)} className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-800 rounded transition-colors" title="Close"><X size={18} /></button>
          </div>
          <div className="flex-1 overflow-hidden flex flex-col min-h-0 p-6">
            <CompareView datasets={selected} mode={compareMode} onModeChange={setCompareMode} />
          </div>
        </div>
      )}

      {/* Dataset Preview modal */}
      <DatasetPreviewModal
        open={!!preview}
        onClose={() => { setPreview(null); setMarkerDate(null); }}
        provider={preview?.provider ?? null}
        symbol={preview?.symbol ?? null}
        timeframe={preview?.timeframe ?? null}
      />
    </div>
  );
}
