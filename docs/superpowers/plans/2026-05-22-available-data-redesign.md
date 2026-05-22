# Available Data Tab Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the Available Data tab with a global time window, flat provider-colored list, options grouping, interactive bars, and bulk actions.

**Architecture:** Extract the available data section from `Data.tsx` into a new `AvailableDataTab` component. Build a data processing pipeline (`useProcessedCoverage`) that flattens providers, merges `_live` suffixes, parses OCC options symbols, and groups contracts by underlying. Build an interactive coverage bar component with hover crosshair and click-to-preview. Add a time window with synced date inputs and range slider, plus filter bar with text search and toggle chips.

**Tech Stack:** React 18, TypeScript, Tailwind CSS, TanStack React Query (existing `useCoverage` hook), existing `DatasetPreviewModal`, existing `useFillGaps` mutation.

---

### Task 1: OCC Options Symbol Utilities

**Files:**
- Create: `src/lib/occ.ts`
- Create: `src/lib/occ.test.ts`

- [ ] **Step 1: Write failing tests for OCC parsing**

```typescript
// src/lib/occ.test.ts
import { describe, it, expect } from "vitest";
import { parseOCC, isOCCSymbol, formatOCCReadable } from "./occ";

describe("isOCCSymbol", () => {
  it("detects standard OCC symbols", () => {
    expect(isOCCSymbol("SPY241029C00450000")).toBe(true);
    expect(isOCCSymbol("GLD250117P00185000")).toBe(true);
    expect(isOCCSymbol("AAPL250620C00200000")).toBe(true);
  });
  it("rejects equity/crypto symbols", () => {
    expect(isOCCSymbol("SPY")).toBe(false);
    expect(isOCCSymbol("BTCUSD")).toBe(false);
    expect(isOCCSymbol("UNFI")).toBe(false);
  });
});

describe("parseOCC", () => {
  it("parses call option", () => {
    const result = parseOCC("SPY241029C00450000");
    expect(result).toEqual({
      underlying: "SPY",
      expiration: "2024-10-29",
      side: "Call",
      strike: 450,
    });
  });
  it("parses put option", () => {
    const result = parseOCC("GLD250117P00185000");
    expect(result).toEqual({
      underlying: "GLD",
      expiration: "2025-01-17",
      side: "Put",
      strike: 185,
    });
  });
  it("parses fractional strike", () => {
    const result = parseOCC("AAPL250620C00197500");
    expect(result).toEqual({
      underlying: "AAPL",
      expiration: "2025-06-20",
      side: "Call",
      strike: 197.5,
    });
  });
  it("returns null for non-OCC symbol", () => {
    expect(parseOCC("SPY")).toBeNull();
  });
});

describe("formatOCCReadable", () => {
  it("formats call option", () => {
    expect(formatOCCReadable("SPY241029C00450000")).toBe("SPY $450 Call 10/29/24");
  });
  it("formats put option with fractional strike", () => {
    expect(formatOCCReadable("GLD250117P00185500")).toBe("GLD $185.50 Put 01/17/25");
  });
  it("returns original symbol if not OCC", () => {
    expect(formatOCCReadable("SPY")).toBe("SPY");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run src/lib/occ.test.ts`
Expected: FAIL — module `./occ` does not exist.

- [ ] **Step 3: Implement OCC utilities**

```typescript
// src/lib/occ.ts

// OCC format: 1-6 letter underlying + 6 digit date (YYMMDD) + C/P + 8 digit strike
const OCC_RE = /^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$/;

export interface ParsedOCC {
  underlying: string;
  expiration: string;  // ISO date YYYY-MM-DD
  side: "Call" | "Put";
  strike: number;
}

export function isOCCSymbol(symbol: string): boolean {
  return OCC_RE.test(symbol);
}

export function parseOCC(symbol: string): ParsedOCC | null {
  const m = symbol.match(OCC_RE);
  if (!m) return null;
  const [, underlying, yy, mm, dd, cp, strikeRaw] = m;
  return {
    underlying,
    expiration: `20${yy}-${mm}-${dd}`,
    side: cp === "C" ? "Call" : "Put",
    strike: parseInt(strikeRaw, 10) / 1000,
  };
}

export function formatOCCReadable(symbol: string): string {
  const parsed = parseOCC(symbol);
  if (!parsed) return symbol;
  const { underlying, expiration, side, strike } = parsed;
  const [, mm, dd] = expiration.slice(2).split("-");
  const yy = expiration.slice(2, 4);
  const strikeStr = strike % 1 === 0 ? `$${strike}` : `$${strike.toFixed(2)}`;
  return `${underlying} ${strikeStr} ${side} ${mm}/${dd}/${yy}`;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/lib/occ.test.ts`
Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/occ.ts src/lib/occ.test.ts
git commit -m "feat(data): add OCC options symbol parser and formatter"
```

---

### Task 2: Coverage Data Processing Hook

**Files:**
- Create: `src/hooks/useProcessedCoverage.ts`
- Create: `src/hooks/useProcessedCoverage.test.ts`

This hook takes raw `CoverageResponse` and produces a flat, grouped, sorted list ready for rendering.

- [ ] **Step 1: Write failing tests**

```typescript
// src/hooks/useProcessedCoverage.test.ts
import { describe, it, expect } from "vitest";
import { processCoverage, type DisplayRow } from "./useProcessedCoverage";
import type { CoverageResponse } from "../api/client";

const MOCK_COVERAGE: CoverageResponse = {
  providers: {
    tradier: [
      {
        provider: "tradier",
        symbol: "SPY",
        ranges: [{ start: "2024-05-01", end: "2026-05-20" }],
        timeframes_on_disk: ["1day"],
      },
      {
        provider: "tradier",
        symbol: "SPY241029C00450000",
        ranges: [{ start: "2024-10-14", end: "2024-10-28" }],
        timeframes_on_disk: ["1day"],
      },
      {
        provider: "tradier",
        symbol: "SPY250221C00600000",
        ranges: [{ start: "2024-11-07", end: "2025-02-20" }],
        timeframes_on_disk: ["1day"],
      },
    ],
    tradier_live: [
      {
        provider: "tradier_live",
        symbol: "SPY",
        ranges: [{ start: "2026-05-17", end: "2026-05-20" }],
        timeframes_on_disk: ["1min"],
      },
    ],
    polygon: [
      {
        provider: "polygon",
        symbol: "AAPL",
        ranges: [{ start: "2024-01-01", end: "2026-05-20" }],
        timeframes_on_disk: ["1min"],
      },
    ],
  },
};

describe("processCoverage", () => {
  const result = processCoverage(MOCK_COVERAGE);

  it("returns globalMin and globalMax from all ranges", () => {
    expect(result.globalMin).toBe("2024-01-01");
    expect(result.globalMax).toBe("2026-05-20");
  });

  it("merges _live providers into base name", () => {
    const providers = new Set(result.rows.flatMap((r) =>
      r.kind === "options-group"
        ? r.children.map((c) => c.provider)
        : [r.provider]
    ));
    expect(providers.has("tradier_live")).toBe(false);
    expect(providers.has("tradier")).toBe(true);
  });

  it("groups OCC symbols into options-group rows", () => {
    const group = result.rows.find(
      (r) => r.kind === "options-group" && r.underlying === "SPY"
    );
    expect(group).toBeDefined();
    expect(group!.kind).toBe("options-group");
    if (group!.kind === "options-group") {
      expect(group!.children).toHaveLength(2);
      expect(group!.label).toBe("SPY Options (2)");
    }
  });

  it("keeps equity SPY as a separate row", () => {
    const equityRows = result.rows.filter(
      (r) => r.kind === "asset" && r.symbol === "SPY"
    );
    expect(equityRows.length).toBeGreaterThanOrEqual(1);
  });

  it("sorts rows alphabetically, options group after equity", () => {
    const labels = result.rows.map((r) =>
      r.kind === "options-group" ? r.label : r.symbol
    );
    const aaplIdx = labels.indexOf("AAPL");
    const spyIdx = labels.findIndex((l) => l === "SPY");
    const spyOptIdx = labels.findIndex((l) => l.startsWith("SPY Options"));
    expect(aaplIdx).toBeLessThan(spyIdx);
    expect(spyIdx).toBeLessThan(spyOptIdx);
  });

  it("collects unique provider names", () => {
    expect(result.providers.sort()).toEqual(["polygon", "tradier"]);
  });

  it("detects asset types", () => {
    const types = new Set(result.rows.map((r) => r.assetType));
    expect(types.has("equities")).toBe(true);
    expect(types.has("options")).toBe(true);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run src/hooks/useProcessedCoverage.test.ts`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the processing logic**

```typescript
// src/hooks/useProcessedCoverage.ts
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
  assetType: "options";
  sortKey: string;
}

export type DisplayRow = AssetRow | OptionsGroupRow;

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

  // 2. Compute global min/max
  let globalMin = "9999-12-31";
  let globalMax = "0000-01-01";
  for (const asset of allAssets) {
    for (const r of asset.ranges) {
      if (r.start < globalMin) globalMin = r.start;
      if (r.end > globalMax) globalMax = r.end;
    }
  }

  // 3. Collect unique providers
  const providerSet = new Set<string>();
  for (const a of allAssets) providerSet.add(a.normalizedProvider);
  const providers = Array.from(providerSet).sort();

  // 4. Separate options from non-options
  const optionsByUnderlying = new Map<string, (CoverageAsset & { normalizedProvider: string })[]>();
  const nonOptions: (CoverageAsset & { normalizedProvider: string })[] = [];

  for (const asset of allAssets) {
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

  // Non-options assets
  for (const asset of nonOptions) {
    rows.push({
      kind: "asset",
      symbol: asset.symbol,
      provider: asset.normalizedProvider,
      ranges: asset.ranges,
      timeframes: asset.timeframes_on_disk,
      assetType: detectAssetType(asset.symbol),
      sortKey: `${asset.symbol}\x00`,
    });
  }

  // Options groups
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
      // Sort key: underlying + \x01 so options group sorts after the equity row
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/hooks/useProcessedCoverage.test.ts`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hooks/useProcessedCoverage.ts src/hooks/useProcessedCoverage.test.ts
git commit -m "feat(data): add useProcessedCoverage hook with provider merging and options grouping"
```

---

### Task 3: InteractiveCoverageBar Component

**Files:**
- Create: `src/components/InteractiveCoverageBar.tsx`

This replaces `CoverageTimeline` usage on the Available Data tab. It renders coverage segments colored by provider, with hover crosshair + tooltip and click-to-preview.

- [ ] **Step 1: Create the InteractiveCoverageBar component**

```tsx
// src/components/InteractiveCoverageBar.tsx
import { useRef, useState, useCallback } from "react";
import type { CoverageRange } from "../api/client";

const PROVIDER_COLORS: Record<string, string> = {
  polygon: "bg-indigo-500",
  tradier: "bg-emerald-500",
  coinbase: "bg-amber-500",
  alpaca: "bg-sky-500",
};

function providerColor(provider: string): string {
  return PROVIDER_COLORS[provider] ?? "bg-gray-400";
}

function isoToMs(iso: string): number {
  return new Date(iso).getTime();
}

function formatDateShort(ms: number): string {
  return new Date(ms).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

interface InteractiveCoverageBarProps {
  ranges: CoverageRange[];
  provider: string;
  windowStart: string;
  windowEnd: string;
  markerDate: string | null;
  onClick: (date: string) => void;
}

export function InteractiveCoverageBar({
  ranges,
  provider,
  windowStart,
  windowEnd,
  markerDate,
  onClick,
}: InteractiveCoverageBarProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoverX, setHoverX] = useState<number | null>(null);
  const [hoverDate, setHoverDate] = useState<string | null>(null);

  const wStartMs = isoToMs(windowStart);
  const wEndMs = isoToMs(windowEnd);
  const wSpan = wEndMs - wStartMs;

  const posToDate = useCallback(
    (clientX: number): string | null => {
      if (!containerRef.current || wSpan <= 0) return null;
      const rect = containerRef.current.getBoundingClientRect();
      const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      const ms = wStartMs + pct * wSpan;
      return new Date(ms).toISOString().slice(0, 10);
    },
    [wStartMs, wSpan],
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      setHoverX(e.clientX - rect.left);
      setHoverDate(posToDate(e.clientX));
    },
    [posToDate],
  );

  const handleMouseLeave = useCallback(() => {
    setHoverX(null);
    setHoverDate(null);
  }, []);

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      const date = posToDate(e.clientX);
      if (date) onClick(date);
    },
    [posToDate, onClick],
  );

  if (wSpan <= 0) {
    return <div className="flex-1 h-3 bg-gray-700 rounded-full" />;
  }

  const segments = ranges.map((r) => {
    const segStart = Math.max(isoToMs(r.start), wStartMs);
    const segEnd = Math.min(isoToMs(r.end) + 86_400_000, wEndMs);
    const left = ((segStart - wStartMs) / wSpan) * 100;
    const width = Math.max(((segEnd - segStart) / wSpan) * 100, 0.3);
    return { left, width };
  });

  const markerPct =
    markerDate && wSpan > 0
      ? ((isoToMs(markerDate) - wStartMs) / wSpan) * 100
      : null;

  const colorClass = providerColor(provider);

  return (
    <div
      ref={containerRef}
      className="relative flex-1 h-3 bg-gray-800 rounded cursor-crosshair group"
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      onClick={handleClick}
    >
      {segments.map((seg, i) => (
        <div
          key={i}
          className={`absolute top-0 h-full ${colorClass} rounded-sm opacity-80`}
          style={{ left: `${seg.left}%`, width: `${seg.width}%` }}
        />
      ))}

      {/* Hover crosshair */}
      {hoverX != null && (
        <>
          <div
            className="absolute top-0 h-full w-px bg-white/50 pointer-events-none"
            style={{ left: `${hoverX}px` }}
          />
          {hoverDate && (
            <div
              className="absolute -top-7 px-1.5 py-0.5 bg-gray-900 border border-gray-700 rounded text-[10px] text-gray-200 whitespace-nowrap pointer-events-none z-10"
              style={{
                left: `${hoverX}px`,
                transform: "translateX(-50%)",
              }}
            >
              {formatDateShort(isoToMs(hoverDate))}
            </div>
          )}
        </>
      )}

      {/* Persistent marker from click */}
      {markerPct != null && markerPct >= 0 && markerPct <= 100 && (
        <div
          className="absolute top-0 h-full w-px bg-indigo-300 pointer-events-none"
          style={{ left: `${markerPct}%` }}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 2: Run typecheck**

Run: `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/components/InteractiveCoverageBar.tsx
git commit -m "feat(data): add InteractiveCoverageBar with hover crosshair and click handling"
```

---

### Task 4: TimeWindowControls Component

**Files:**
- Create: `src/components/TimeWindowControls.tsx`

Date inputs + dual-thumb range slider + time axis, all bidirectionally synced.

- [ ] **Step 1: Create the TimeWindowControls component**

```tsx
// src/components/TimeWindowControls.tsx
import { useCallback, useMemo, useRef } from "react";

interface TimeWindowControlsProps {
  globalMin: string;
  globalMax: string;
  windowStart: string;
  windowEnd: string;
  onWindowChange: (start: string, end: string) => void;
}

function isoToMs(iso: string): number {
  return new Date(iso).getTime();
}

function msToIso(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

function generateTicks(startMs: number, endMs: number, maxTicks: number): { ms: number; label: string }[] {
  const span = endMs - startMs;
  if (span <= 0) return [];

  const DAY = 86_400_000;
  const intervals = [
    { step: 7 * DAY, fmt: { month: "short" as const, day: "numeric" as const } },
    { step: 30 * DAY, fmt: { month: "short" as const, year: "numeric" as const } },
    { step: 90 * DAY, fmt: { month: "short" as const, year: "numeric" as const } },
    { step: 365 * DAY, fmt: { year: "numeric" as const } },
  ];

  const chosen = intervals.find((i) => span / i.step <= maxTicks) ?? intervals[intervals.length - 1];
  const ticks: { ms: number; label: string }[] = [];
  let cursor = startMs + chosen.step - (startMs % chosen.step);
  while (cursor < endMs) {
    ticks.push({
      ms: cursor,
      label: new Date(cursor).toLocaleDateString(undefined, chosen.fmt),
    });
    cursor += chosen.step;
  }
  return ticks;
}

export function TimeWindowControls({
  globalMin,
  globalMax,
  windowStart,
  windowEnd,
  onWindowChange,
}: TimeWindowControlsProps) {
  const gMinMs = isoToMs(globalMin);
  const gMaxMs = isoToMs(globalMax);
  const gSpan = gMaxMs - gMinMs;

  const wStartMs = isoToMs(windowStart);
  const wEndMs = isoToMs(windowEnd);

  const trackRef = useRef<HTMLDivElement>(null);

  const ticks = useMemo(
    () => generateTicks(wStartMs, wEndMs, 8),
    [wStartMs, wEndMs],
  );

  const handleDateChange = useCallback(
    (which: "start" | "end", value: string) => {
      if (which === "start") {
        onWindowChange(value, windowEnd);
      } else {
        onWindowChange(windowStart, value);
      }
    },
    [windowStart, windowEnd, onWindowChange],
  );

  const handleSliderThumb = useCallback(
    (which: "start" | "end", e: React.MouseEvent) => {
      if (!trackRef.current || gSpan <= 0) return;
      e.preventDefault();

      const onMove = (me: MouseEvent) => {
        const rect = trackRef.current!.getBoundingClientRect();
        const pct = Math.max(0, Math.min(1, (me.clientX - rect.left) / rect.width));
        const ms = gMinMs + pct * gSpan;
        const iso = msToIso(ms);

        if (which === "start" && iso < windowEnd) {
          onWindowChange(iso, windowEnd);
        } else if (which === "end" && iso > windowStart) {
          onWindowChange(windowStart, iso);
        }
      };

      const onUp = () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };

      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [gMinMs, gSpan, windowStart, windowEnd, onWindowChange],
  );

  const startPct = gSpan > 0 ? ((wStartMs - gMinMs) / gSpan) * 100 : 0;
  const endPct = gSpan > 0 ? ((wEndMs - gMinMs) / gSpan) * 100 : 100;

  return (
    <div className="space-y-2">
      {/* Date inputs */}
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-1.5 text-xs text-gray-400">
          From
          <input
            type="date"
            value={windowStart}
            min={globalMin}
            max={windowEnd}
            onChange={(e) => handleDateChange("start", e.target.value)}
            className="bg-gray-800 border border-gray-700 text-gray-100 rounded px-2 py-1 text-xs"
          />
        </label>
        <label className="flex items-center gap-1.5 text-xs text-gray-400">
          To
          <input
            type="date"
            value={windowEnd}
            min={windowStart}
            max={globalMax}
            onChange={(e) => handleDateChange("end", e.target.value)}
            className="bg-gray-800 border border-gray-700 text-gray-100 rounded px-2 py-1 text-xs"
          />
        </label>
        <button
          onClick={() => onWindowChange(globalMin, globalMax)}
          className="text-[10px] text-gray-500 hover:text-gray-300 transition-colors"
        >
          Reset
        </button>
      </div>

      {/* Range slider */}
      <div ref={trackRef} className="relative h-4 select-none">
        {/* Track background */}
        <div className="absolute top-1.5 left-0 right-0 h-1 bg-gray-700 rounded" />
        {/* Active range */}
        <div
          className="absolute top-1.5 h-1 bg-indigo-600 rounded"
          style={{ left: `${startPct}%`, width: `${endPct - startPct}%` }}
        />
        {/* Start thumb */}
        <div
          className="absolute top-0 w-3 h-3 bg-white rounded-full cursor-ew-resize border-2 border-indigo-600 -translate-x-1/2"
          style={{ left: `${startPct}%` }}
          onMouseDown={(e) => handleSliderThumb("start", e)}
        />
        {/* End thumb */}
        <div
          className="absolute top-0 w-3 h-3 bg-white rounded-full cursor-ew-resize border-2 border-indigo-600 -translate-x-1/2"
          style={{ left: `${endPct}%` }}
          onMouseDown={(e) => handleSliderThumb("end", e)}
        />
      </div>

      {/* Time axis */}
      <div className="relative h-4">
        {ticks.map((tick, i) => {
          const pct = gSpan > 0 ? ((tick.ms - wStartMs) / (wEndMs - wStartMs)) * 100 : 0;
          if (pct < 0 || pct > 100) return null;
          return (
            <span
              key={i}
              className="absolute text-[9px] text-gray-600 -translate-x-1/2 whitespace-nowrap"
              style={{ left: `${pct}%` }}
            >
              {tick.label}
            </span>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Run typecheck**

Run: `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/components/TimeWindowControls.tsx
git commit -m "feat(data): add TimeWindowControls with date inputs, range slider, and time axis"
```

---

### Task 5: FilterBar Component

**Files:**
- Create: `src/components/DataFilterBar.tsx`

Text search + asset type chips + provider chips (colored as legend).

- [ ] **Step 1: Create the DataFilterBar component**

```tsx
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
```

- [ ] **Step 2: Run typecheck**

Run: `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/components/DataFilterBar.tsx
git commit -m "feat(data): add DataFilterBar with search, asset type chips, and provider chips"
```

---

### Task 6: AvailableDataTab Component — Assembly

**Files:**
- Create: `src/components/AvailableDataTab.tsx`
- Modify: `src/pages/Data.tsx`

Wire all sub-components together into the tab, replace the old Available Data section in `Data.tsx`.

- [ ] **Step 1: Create AvailableDataTab component**

```tsx
// src/components/AvailableDataTab.tsx
import { useState, useCallback, useMemo } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useCoverage, useAvailableData, useFillGaps } from "../api/hooks";
import { useProcessedCoverage, type AssetType, type DisplayRow, type OptionsGroupChild } from "../hooks/useProcessedCoverage";
import { formatOCCReadable } from "../lib/occ";
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
import { X } from "lucide-react";
import { useEffect } from "react";

// ─── Compare URL helpers (moved from Data.tsx) ───────────────────────────────

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

// ─── Component ───────────────────────────────────────────────────────────────

export function AvailableDataTab() {
  const { data: coverageData, isLoading: coverageLoading } = useCoverage();
  const { isLoading: availableLoading } = useAvailableData();
  const fillGapsMutation = useFillGaps();
  const addAlert = useUIStore((s) => s.addAlert);

  const processed = useProcessedCoverage(coverageData);

  // Time window state
  const [windowStart, setWindowStart] = useState<string | null>(null);
  const [windowEnd, setWindowEnd] = useState<string | null>(null);

  // Initialize time window from global bounds once data loads
  const effectiveStart = windowStart ?? processed?.globalMin ?? "";
  const effectiveEnd = windowEnd ?? processed?.globalMax ?? "";

  const handleWindowChange = useCallback((start: string, end: string) => {
    setWindowStart(start);
    setWindowEnd(end);
  }, []);

  // Filter state
  const [searchText, setSearchText] = useState("");
  const [assetTypes, setAssetTypes] = useState<Set<AssetType>>(
    new Set(["equities", "options", "crypto"]),
  );
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
  const selectedKeys = useMemo(
    () => new Set(selected.map(selectionKey)),
    [selected, selectionKey],
  );
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
    provider: string;
    symbol: string;
    timeframe: string;
    targetDate?: string;
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
          timeframe: "1min",
        });
        total += result.gap_count;
      } catch (e) {
        addAlert({
          message: `Fill gaps failed for ${ds.symbol}: ${(e as Error).message}`,
          severity: "error",
        });
      }
    }
    if (total > 0) {
      addAlert({
        message: `Queued ${total} download${total === 1 ? "" : "s"} to fill gaps.`,
        severity: "success",
      });
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
      // Asset type filter
      if (!assetTypes.has(row.assetType)) return false;
      // Provider filter
      if (!effectiveProviders.has(row.provider)) return false;
      // Text search
      if (search) {
        if (row.kind === "options-group") {
          return (
            row.underlying.toLowerCase().includes(search) ||
            row.children.some((c) => c.symbol.toLowerCase().includes(search) || c.readableLabel.toLowerCase().includes(search))
          );
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
      {/* Time window */}
      <TimeWindowControls
        globalMin={processed.globalMin}
        globalMax={processed.globalMax}
        windowStart={effectiveStart}
        windowEnd={effectiveEnd}
        onWindowChange={handleWindowChange}
      />

      {/* Filter bar */}
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
          <span className="text-sm text-gray-300">
            {selected.length} selected
          </span>
          <button
            onClick={clearSelection}
            className="text-xs text-gray-400 hover:text-gray-200 transition-colors"
          >
            Clear
          </button>
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
              <input
                type="date"
                value={fillStart}
                onChange={(e) => setFillStart(e.target.value)}
                className="bg-gray-800 border border-gray-700 text-gray-100 rounded px-2 py-1 text-xs"
              />
              <span className="text-xs text-gray-500">to</span>
              <input
                type="date"
                value={fillEnd}
                onChange={(e) => setFillEnd(e.target.value)}
                className="bg-gray-800 border border-gray-700 text-gray-100 rounded px-2 py-1 text-xs"
              />
              <button
                onClick={handleBulkFillGaps}
                disabled={fillGapsMutation.isPending}
                className="px-2.5 py-1 rounded text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 transition-colors"
              >
                Go
              </button>
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
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => toggleSelected(ds)}
                      className="accent-indigo-500 shrink-0"
                    />
                    <button
                      onClick={() => toggleGroup(row.underlying)}
                      className="flex items-center gap-1.5 text-sm font-mono text-gray-200 hover:text-white shrink-0 w-44 text-left"
                    >
                      {isExpanded ? (
                        <ChevronDown size={12} className="text-gray-500" />
                      ) : (
                        <ChevronRight size={12} className="text-gray-500" />
                      )}
                      {row.label}
                    </button>
                    <InteractiveCoverageBar
                      ranges={row.ranges}
                      provider={row.provider}
                      windowStart={effectiveStart}
                      windowEnd={effectiveEnd}
                      markerDate={markerDate}
                      onClick={(date) => handleBarClick(row.provider, row.children[0]?.symbol ?? row.underlying, row.timeframes, date)}
                    />
                    <div className="hidden sm:flex gap-1 shrink-0">
                      {row.timeframes.map((tf) => (
                        <span key={tf} className="text-[10px] font-mono text-gray-500 bg-gray-800 px-1 py-0.5 rounded">
                          {tf}
                        </span>
                      ))}
                    </div>
                  </div>
                  {isExpanded && (
                    <div className="ml-6">
                      {row.children.map((child) => {
                        const childDs = makeCompareDataset(child.provider, child.symbol, child.timeframes);
                        const childSelected = selectedKeys.has(selectionKey(childDs));
                        return (
                          <div
                            key={child.symbol}
                            className="flex items-center gap-3 px-3 py-1.5 bg-gray-950 hover:bg-gray-900/50 transition-colors"
                          >
                            <input
                              type="checkbox"
                              checked={childSelected}
                              onChange={() => toggleSelected(childDs)}
                              className="accent-indigo-500 shrink-0"
                            />
                            <span
                              className="text-xs font-mono text-gray-400 w-44 truncate shrink-0 cursor-pointer hover:text-gray-200"
                              onClick={() => handleBarClick(child.provider, child.symbol, child.timeframes, effectiveStart)}
                              title={child.symbol}
                            >
                              {child.readableLabel}
                            </span>
                            <InteractiveCoverageBar
                              ranges={child.ranges}
                              provider={child.provider}
                              windowStart={effectiveStart}
                              windowEnd={effectiveEnd}
                              markerDate={markerDate}
                              onClick={(date) => handleBarClick(child.provider, child.symbol, child.timeframes, date)}
                            />
                            <div className="hidden sm:flex gap-1 shrink-0">
                              {child.timeframes.map((tf) => (
                                <span key={tf} className="text-[10px] font-mono text-gray-500 bg-gray-800 px-1 py-0.5 rounded">
                                  {tf}
                                </span>
                              ))}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            }

            // Regular asset row
            const ds = makeCompareDataset(row.provider, row.symbol, row.timeframes);
            const isSelected = selectedKeys.has(selectionKey(ds));
            return (
              <div
                key={`${row.provider}-${row.symbol}`}
                className="flex items-center gap-3 px-3 py-2 bg-gray-900 hover:bg-gray-800/50 transition-colors rounded-sm"
              >
                <input
                  type="checkbox"
                  checked={isSelected}
                  onChange={() => toggleSelected(ds)}
                  className="accent-indigo-500 shrink-0"
                />
                <span
                  className="text-sm font-mono font-semibold text-gray-200 hover:text-white w-44 text-left shrink-0 cursor-pointer truncate"
                  onClick={() => handleBarClick(row.provider, row.symbol, row.timeframes, effectiveStart)}
                  title={row.symbol}
                >
                  {row.symbol}
                </span>
                <InteractiveCoverageBar
                  ranges={row.ranges}
                  provider={row.provider}
                  windowStart={effectiveStart}
                  windowEnd={effectiveEnd}
                  markerDate={markerDate}
                  onClick={(date) => handleBarClick(row.provider, row.symbol, row.timeframes, date)}
                />
                <div className="hidden sm:flex gap-1 shrink-0">
                  {row.timeframes.map((tf) => (
                    <span key={tf} className="text-[10px] font-mono text-gray-500 bg-gray-800 px-1 py-0.5 rounded">
                      {tf}
                    </span>
                  ))}
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
            <button
              onClick={() => setCompareOpen(false)}
              className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-800 rounded transition-colors"
              title="Close"
            >
              <X size={18} />
            </button>
          </div>
          <div className="flex-1 overflow-hidden flex flex-col min-h-0 p-6">
            <CompareView datasets={selected} mode={compareMode} onModeChange={setCompareMode} />
          </div>
        </div>
      )}

      {/* Dataset Preview modal */}
      <DatasetPreviewModal
        open={!!preview}
        onClose={() => {
          setPreview(null);
          setMarkerDate(null);
        }}
        provider={preview?.provider ?? null}
        symbol={preview?.symbol ?? null}
        timeframe={preview?.timeframe ?? null}
      />
    </div>
  );
}
```

- [ ] **Step 2: Update Data.tsx — replace Available Data section with AvailableDataTab**

In `src/pages/Data.tsx`, make these changes:

a) Add import at top:
```typescript
import { AvailableDataTab } from "../components/AvailableDataTab";
```

b) Remove these imports that are no longer used by Data.tsx (they moved to AvailableDataTab):
- `useAvailableData`
- `useCoverage`
- `useFillGaps`
- `CoverageTimeline`
- `CompareView`, `encodeCompareDataset`, `decodeCompareDataset`, `type CompareDataset`, `type CompareMode`

c) Remove the `readCompareFromUrl` and `writeCompareToUrl` functions (moved to AvailableDataTab).

d) Remove state that was only used by Available Data:
- `preview` state
- `expandedProviders` state + `toggleProvider`
- `selected`, `compareOpen`, `compareMode` state
- `selectionKey`, `selectedKeys`, `toggleSelected`, `clearSelection`
- `initialCompare`
- `available`, `coverageData`, `coverageLoading`, `availableLoading`
- `fillGapsMutation`
- The `useEffect` for `writeCompareToUrl`
- `handleFillGaps` function

e) Replace the `{activeTab === "available" && (...)}` section (lines ~743-864) with:
```tsx
{activeTab === "available" && <AvailableDataTab />}
```

f) Remove the Compare modal JSX block (it's now inside AvailableDataTab).

g) Remove the `DatasetPreviewModal` instance for market data preview (moved to AvailableDataTab). Keep the `CustomDataPreviewModal` for scrapers.

- [ ] **Step 3: Run typecheck**

Run: `npx tsc --noEmit`
Expected: no errors. Fix any unused import warnings.

- [ ] **Step 4: Run full build**

Run: `npm run build`
Expected: successful build with no errors.

- [ ] **Step 5: Commit**

```bash
git add src/components/AvailableDataTab.tsx src/pages/Data.tsx
git commit -m "feat(data): extract AvailableDataTab with time window, filters, and options grouping"
```

---

### Task 7: Clean Up Data.tsx Unused Code

**Files:**
- Modify: `src/pages/Data.tsx`

After Task 6, Data.tsx should be significantly smaller. This task handles any remaining cleanup — removing unused imports, dead code, or leftover state.

- [ ] **Step 1: Remove all unused imports and dead code**

Run `npx tsc --noEmit` and fix any reported issues. Remove any imports that are no longer referenced: `CoverageTimeline`, `CompareView`, compare URL helpers, coverage hooks, etc.

Verify the `void available;` line is removed (it was a placeholder for the old available data usage).

- [ ] **Step 2: Run full test suite**

Run: `npx vitest run`
Expected: all previously passing tests still pass. The OCC and processing tests also pass.

- [ ] **Step 3: Run full build**

Run: `npm run build`
Expected: clean build, no errors.

- [ ] **Step 4: Commit**

```bash
git add src/pages/Data.tsx
git commit -m "refactor(data): clean up Data.tsx after AvailableDataTab extraction"
```

---

### Task 8: Visual Verification and Polish

**Files:**
- Possibly modify: `src/components/AvailableDataTab.tsx`, `src/components/InteractiveCoverageBar.tsx`, `src/components/TimeWindowControls.tsx`

- [ ] **Step 1: Start dev server and verify in browser**

Run: `npm run dev`

Navigate to `http://localhost:3000/data?tab=available` and verify:
1. Time window date inputs default to global min/max of all data
2. Range slider thumbs are draggable and sync with date inputs
3. Time axis shows tick labels that adapt to the window size
4. Filter bar text search filters rows as you type
5. Asset type chips toggle rows by type (equities/options/crypto)
6. Provider chips toggle rows by provider, colored correctly
7. Options contracts are grouped (e.g., "SPY Options (23)")
8. Expanding an options group shows indented child rows with readable labels
9. Hover on any coverage bar shows crosshair + date tooltip
10. Click on bar opens preview modal
11. Bulk action bar appears when rows are selected
12. Compare and Fill Gaps bulk actions work
13. Other tabs (Data Acquisition, Download History) still work correctly
14. `_live` providers are merged (no more "tradier_live" section)

- [ ] **Step 2: Fix any visual issues found during verification**

Address spacing, alignment, color, or interaction issues discovered in Step 1.

- [ ] **Step 3: Run full build one final time**

Run: `npm run build`
Expected: clean build.

- [ ] **Step 4: Commit any polish fixes**

```bash
git add -A
git commit -m "fix(data): polish Available Data tab after visual review"
```
