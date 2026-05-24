import { useMemo } from "react";
import type { CoverageResponse, CoverageAsset, CoverageRange } from "../api/client";
import { isOCCSymbol, parseOCC, formatOCCReadable } from "../lib/occ";

export type AssetType = "equities" | "options" | "crypto";

const CRYPTO_SYMBOLS = new Set(["BTCUSD", "ETHUSD", "BTCUSDT", "ETHUSDT", "SOLUSD", "DOGEUSD"]);

function detectAssetType(symbol: string): AssetType {
  if (isOCCSymbol(symbol)) return "options";
  if (CRYPTO_SYMBOLS.has(symbol) || symbol.endsWith("USD") || symbol.endsWith("USDT")) return "crypto";
  return "equities";
}

function normalizeProvider(provider: string): string {
  return provider.replace(/_live$/, "");
}

function mergeRanges(ranges: CoverageRange[]): CoverageRange[] {
  if (ranges.length === 0) return [];
  const sorted = [...ranges].sort((a, b) => a.start.localeCompare(b.start));
  const merged: CoverageRange[] = [{ ...sorted[0] }];
  for (let i = 1; i < sorted.length; i++) {
    const prev = merged[merged.length - 1];
    if (sorted[i].start <= prev.end) {
      prev.end = sorted[i].end > prev.end ? sorted[i].end : prev.end;
    } else {
      merged.push({ ...sorted[i] });
    }
  }
  return merged;
}

export interface AssetRow {
  kind: "asset";
  symbol: string;
  provider: string;
  ranges: CoverageRange[];
  timeframes: string[];
  assetType: AssetType;
  sortKey: string;
}

export interface OptionsGroupChild {
  symbol: string;
  readableLabel: string;
  provider: string;
  ranges: CoverageRange[];
  timeframes: string[];
}

export interface OptionsGroupRow {
  kind: "options-group";
  underlying: string;
  label: string;
  provider: string;
  ranges: CoverageRange[];
  timeframes: string[];
  children: OptionsGroupChild[];
  optionExpirations?: string[];
  assetType: "options";
  sortKey: string;
}

export interface ProviderChild {
  provider: string;
  sourceProvider: string;
  timeframe: string;
  ranges: CoverageRange[];
}

export interface MultiProviderGroupRow {
  kind: "multi-provider";
  symbol: string;
  label: string;
  provider: string;
  ranges: CoverageRange[];
  timeframes: string[];
  children: ProviderChild[];
  assetType: AssetType;
  sortKey: string;
}

export type DisplayRow = AssetRow | OptionsGroupRow | MultiProviderGroupRow;

export interface ProcessedCoverage {
  rows: DisplayRow[];
  globalMin: string;
  globalMax: string;
  providers: string[];
}

export function processCoverage(data: CoverageResponse): ProcessedCoverage {
  // 1. Flatten and normalize providers
  const allAssets: (CoverageAsset & { normalizedProvider: string })[] = [];
  for (const [provider, assets] of Object.entries(data.providers)) {
    const norm = normalizeProvider(provider);
    for (const asset of assets) {
      allAssets.push({ ...asset, normalizedProvider: norm });
    }
  }

  // 1b. Deduplicate: merge assets with same symbol+provider after normalization
  const deduped = new Map<string, (typeof allAssets)[0]>();
  for (const asset of allAssets) {
    const key = `${asset.normalizedProvider}\0${asset.symbol}`;
    const existing = deduped.get(key);
    if (existing) {
      existing.ranges = [...existing.ranges, ...asset.ranges];
      existing.timeframes_on_disk = [...new Set([...existing.timeframes_on_disk, ...asset.timeframes_on_disk])];
      if (asset.option_expirations) {
        existing.option_expirations = [...new Set([...(existing.option_expirations ?? []), ...asset.option_expirations])].sort();
      }
    } else {
      deduped.set(key, { ...asset, ranges: [...asset.ranges], timeframes_on_disk: [...asset.timeframes_on_disk] });
    }
  }
  const mergedAssets = Array.from(deduped.values());
  // Merge overlapping ranges within each asset
  for (const asset of mergedAssets) {
    asset.ranges = mergeRanges(asset.ranges);
  }

  // 2. Compute global min/max
  let globalMin = "9999-12-31";
  let globalMax = "0000-01-01";
  for (const asset of mergedAssets) {
    for (const r of asset.ranges) {
      if (r.start < globalMin) globalMin = r.start;
      if (r.end > globalMax) globalMax = r.end;
    }
  }

  // 3. Collect unique providers
  const providerSet = new Set<string>();
  for (const a of mergedAssets) providerSet.add(a.normalizedProvider);
  const providers = Array.from(providerSet).sort();

  // 4. Separate options from non-options
  const optionsByUnderlying = new Map<string, (CoverageAsset & { normalizedProvider: string })[]>();
  const nonOptions: (CoverageAsset & { normalizedProvider: string })[] = [];

  for (const asset of mergedAssets) {
    const parsed = parseOCC(asset.symbol);
    if (parsed) {
      const list = optionsByUnderlying.get(parsed.underlying) ?? [];
      list.push(asset);
      optionsByUnderlying.set(parsed.underlying, list);
    } else {
      nonOptions.push(asset);
    }
  }

  // 5. Build display rows
  const rows: DisplayRow[] = [];

  // Non-options assets: group by symbol, split children by provider+timeframe
  // Use allAssets (pre-dedup) so each original provider entry with its own
  // timeframe becomes a separate child row.
  const nonOptOriginals = allAssets.filter((a) => !parseOCC(a.symbol));
  const childBySymbol = new Map<string, ProviderChild[]>();
  for (const asset of nonOptOriginals) {
    const list = childBySymbol.get(asset.symbol) ?? [];
    for (const tf of asset.timeframes_on_disk) {
      const existing = list.find((c) => c.provider === asset.normalizedProvider && c.timeframe === tf);
      if (existing) {
        existing.ranges = mergeRanges([...existing.ranges, ...asset.ranges]);
      } else {
        list.push({ provider: asset.normalizedProvider, sourceProvider: asset.provider, timeframe: tf, ranges: [...asset.ranges] });
      }
    }
    childBySymbol.set(asset.symbol, list);
  }

  const bySymbol = new Map<string, (CoverageAsset & { normalizedProvider: string })[]>();
  for (const asset of nonOptions) {
    const list = bySymbol.get(asset.symbol) ?? [];
    list.push(asset);
    bySymbol.set(asset.symbol, list);
  }
  for (const [symbol, assets] of bySymbol) {
    const children = (childBySymbol.get(symbol) ?? [])
      .sort((a, b) => a.provider.localeCompare(b.provider) || a.timeframe.localeCompare(b.timeframe));
    if (children.length <= 1) {
      rows.push({
        kind: "asset",
        symbol,
        provider: assets[0].normalizedProvider,
        ranges: assets[0].ranges,
        timeframes: assets[0].timeframes_on_disk,
        assetType: detectAssetType(symbol),
        sortKey: `${symbol}\x00`,
      });
    } else {
      const allRanges = assets.flatMap((a) => a.ranges);
      const allTimeframes = [...new Set(assets.flatMap((a) => a.timeframes_on_disk))];
      rows.push({
        kind: "multi-provider",
        symbol,
        label: `${symbol} (${children.length} datasets)`,
        provider: assets[0].normalizedProvider,
        ranges: mergeRanges(allRanges),
        timeframes: allTimeframes,
        children,
        assetType: detectAssetType(symbol),
        sortKey: `${symbol}\x00`,
      });
    }
  }

  // Options groups (OCC-style individual contracts)
  for (const [underlying, contracts] of optionsByUnderlying) {
    const allRanges = contracts.flatMap((c) => c.ranges);
    const allTimeframes = [...new Set(contracts.flatMap((c) => c.timeframes_on_disk))];
    const providerForGroup = contracts[0].normalizedProvider;

    const children: OptionsGroupChild[] = contracts
      .map((c) => ({
        symbol: c.symbol,
        readableLabel: formatOCCReadable(c.symbol),
        provider: c.normalizedProvider,
        ranges: c.ranges,
        timeframes: c.timeframes_on_disk,
      }))
      .sort((a, b) => a.readableLabel.localeCompare(b.readableLabel));

    rows.push({
      kind: "options-group",
      underlying,
      label: `${underlying} Options (${contracts.length})`,
      provider: providerForGroup,
      ranges: mergeRanges(allRanges),
      timeframes: allTimeframes,
      children,
      assetType: "options",
      sortKey: `${underlying}\x01`,
    });
  }

  // Option chain groups (underlying symbols with cached chain snapshots)
  for (const asset of mergedAssets) {
    const expirations = (asset as any).option_expirations as string[] | undefined;
    if (!expirations || expirations.length === 0) continue;
    const underlying = asset.symbol;
    if (optionsByUnderlying.has(underlying)) continue;
    const children: OptionsGroupChild[] = expirations.map((exp) => ({
      symbol: `${underlying}/options/${exp}`,
      readableLabel: `Chain ${exp}`,
      provider: asset.normalizedProvider,
      ranges: [{ start: exp, end: exp }],
      timeframes: ["chain"],
    }));
    rows.push({
      kind: "options-group",
      underlying,
      label: `${underlying} Options (${expirations.length} chains)`,
      provider: asset.normalizedProvider,
      ranges: expirations.length > 0
        ? [{ start: expirations[0], end: expirations[expirations.length - 1] }]
        : [],
      timeframes: ["options"],
      children,
      optionExpirations: expirations,
      assetType: "options",
      sortKey: `${underlying}\x01`,
    });
  }

  // 6. Sort alphabetically
  rows.sort((a, b) => a.sortKey.localeCompare(b.sortKey));

  return { rows, globalMin, globalMax, providers };
}

export function useProcessedCoverage(data: CoverageResponse | undefined) {
  return useMemo(() => {
    if (!data || Object.keys(data.providers).length === 0) return null;
    return processCoverage(data);
  }, [data]);
}
