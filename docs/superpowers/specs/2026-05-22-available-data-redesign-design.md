# Available Data Tab Redesign

**Date:** 2026-05-22
**Status:** Approved

## Problem

The Available Data tab shows a flat list of assets grouped by provider accordion. Key issues:

- Options contracts (177 under Tradier alone) mixed unsorted with equities
- No search or filtering by symbol, asset type, or provider
- `_live` suffixed providers appear as separate sections from their historical counterparts
- Per-row date labels are noisy; no shared time axis makes cross-asset comparison hard
- Coverage bars render in isolation with no way to zoom into a time window
- No hover/click interaction on bars

## Design

### 1. Global Time Window

Two date inputs at the top of the tab showing start and end dates. Defaults are computed client-side from the min/max of all `CoverageAsset.ranges` across every provider.

Below the date inputs, a **draggable range slider** mapped to the full extent of all data. The slider thumbs and date inputs stay bidirectionally synced — changing one updates the other.

A **time axis** with tick marks spans the full content width below the slider. Tick density adapts to the window size (days for short windows, months/years for long ones). This axis provides scale for every bar below it.

All coverage bars render proportionally within this time window. Data outside the window is clipped.

### 2. Filter Bar

Directly below the time axis:

- **Text input** — filters rows by symbol substring match (case-insensitive). For options groups, matches against the underlying symbol and individual contract names.
- **Asset type chips** — Equities, Options, Crypto. Multi-select toggles, all active by default. Chips are styled as toggleable pill buttons.
- **Provider chips** — One per provider (after `_live` merging), colored with the provider's bar color. Multi-select toggles, all active by default. These double as the color legend.

### 3. Provider Merging

Remove the provider accordion grouping entirely. Flatten all assets into a single list.

Merge `_live` suffixed providers with their base name:
- `tradier_live` → `tradier`
- `alpaca_live` → `alpaca`
- `coinbase_live` → `coinbase`

The provider identity is conveyed by bar color instead of grouping.

**Provider colors** (on dark background):
| Provider | Tailwind color | CSS class prefix |
|----------|---------------|-----------------|
| polygon | indigo-500 | `bg-indigo-500` |
| tradier | emerald-500 | `bg-emerald-500` |
| coinbase | amber-500 | `bg-amber-500` |
| alpaca | sky-500 | `bg-sky-500` |

Unknown providers fall back to `gray-400`.

### 4. Row Layout

Each row in the flat list:

```
[checkbox] [symbol label] [coverage bar ~~~~~~~~~~~~~~~~~~~~~~] [timeframe badges] [fill gaps]
```

- **Checkbox** — existing compare selection, unchanged.
- **Symbol label** — font-mono, fixed width. For options groups: "SPY Options (23)" with expand/collapse toggle.
- **Coverage bar** — colored by provider, rendered within the global time window. No per-row date labels.
- **Timeframe badges** — small pills showing timeframes on disk (e.g., `1min`, `1day`).
- **Fill gaps** — existing button, unchanged.

### 5. Options Contract Grouping

**OCC symbol parsing:** Standard OCC options symbols follow the format `XXXYYMMDDCNNNNNNN` where:
- `XXX` = underlying (variable length, up to 6 chars)
- `YYMMDD` = expiration date
- `C` = call/put indicator (C or P)
- `NNNNNNN` = strike price × 1000 (8 digits, zero-padded)

Detection heuristic: a symbol is an options contract if it matches the regex pattern for OCC format (letters followed by 6 digits, C/P, then 8 digits).

**Grouping:** All options contracts sharing the same underlying are grouped into a single parent row. The parent row displays:
- Label: `{underlying} Options ({count})`
- Bar: union of all child contract ranges (merge overlapping segments)
- Timeframe badges: union of all child timeframes

**Expansion:** Clicking the parent row toggles inline expansion. Child rows appear indented below the parent with human-readable labels:

`SPY241029C00450000` → `SPY $450 Call 10/29/24`
`GLD250117P00185000` → `GLD $185 Put 01/17/25`

Format: `{underlying} ${strike} {Call|Put} {MM/DD/YY}`

Each child row has its own coverage bar, checkbox, and fill-gaps button.

SPY (equity) and SPY Options (group) are **separate rows** — they are not nested.

### 6. Bar Interactions

**Hover:** A vertical crosshair line follows the mouse cursor across the bar. A tooltip positioned near the cursor shows the date at that x-position, computed from the bar's proportion within the global time window.

**Click:** Opens the existing `DatasetPreviewModal` for that row's provider/symbol/timeframe. The modal receives the clicked date so it can center the data view around that point. A persistent vertical marker line appears on the bar at the clicked position until the modal closes or the user clicks elsewhere.

Implementation: the `DatasetPreviewModal` props are extended with an optional `targetDate: string | null` field. The modal's initial data fetch uses this date to center pagination.

### 7. Sorting

Default sort: alphabetical by symbol name. Options group rows sort by their underlying symbol (among other rows), with the group row appearing after the underlying equity row if both exist.

### 8. Data Flow

**Existing API, no backend changes:**
- `GET /api/data/coverage` → `CoverageResponse { providers: Record<string, CoverageAsset[]> }`
- All transformation (merging, grouping, filtering) happens client-side

**Client-side processing pipeline:**
1. Receive `CoverageResponse` from `useCoverage()`
2. Flatten all providers into a single `CoverageAsset[]`
3. Normalize provider names (strip `_live` suffix)
4. Detect options contracts via OCC regex
5. Group options by underlying → create parent rows with merged ranges
6. Compute global min/max dates from all ranges
7. Apply filters (text, asset type, provider) → filtered display list
8. Sort alphabetically

### 9. Component Structure

The current Available Data section in `Data.tsx` will be extracted into a new component `AvailableDataTab.tsx` to keep Data.tsx manageable.

Internal components (can be local to the file):
- `TimeWindowControls` — date inputs + range slider + time axis
- `FilterBar` — text input + asset type chips + provider chips
- `AssetRow` — single row (equity, crypto, or options group parent)
- `OptionsChildRow` — indented child row for expanded options group
- `InteractiveCoverageBar` — the bar with hover crosshair and click handling (replaces `CoverageTimeline` usage on this tab)

### 10. What Stays the Same

- Compare checkboxes and compare modal flow
- Fill gaps button behavior
- Download Market Data button in the tab header
- `DatasetPreviewModal` (extended with optional `targetDate`)
- `CoverageTimeline` component itself (still used elsewhere if needed)

### 11. Scope Exclusions

- No backend API changes
- No changes to other tabs (Data Acquisition, Download History)
- No changes to the compare view
- Options grouping is display-only — fill-gaps and compare still operate on individual contracts
