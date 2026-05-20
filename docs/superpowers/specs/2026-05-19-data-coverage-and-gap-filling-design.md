# Data Coverage, Gap-Filling, and Timeframe Unification — Design

## Problem

Three related issues converge on how the system manages historical market data on disk:

1. **Timeframes treated as separate datasets.** The Data page shows SPY-1min, SPY-5min, SPY-15min, SPY-1hour, SPY-1day as five independent rows. In reality, 1-min is the canonical source — everything else is derivable. Downloading and storing all five wastes bandwidth, disk, and UI space.

2. **No coverage visibility.** There's no way to see what date ranges are cached per asset. The user downloads SPY 1-min for April, then later needs January–May for a backtest — but can't see the gap or fill just the missing months.

3. **Backtest coverage check is broken.** `_has_coverage(source, symbol, timeframe, start, end)` checks that the cached data's first/last timestamps bracket the requested range — but doesn't verify there are no gaps WITHIN the range. A short backtest downloads April 1–15; a later backtest requesting January–May sees "first=Apr 1, last=Apr 15" and says "covered" even though Jan–Mar and Apr 16–May are missing.

## Goal

A unified data-coverage layer that:
- Stores 1-min bars as the canonical source; derives higher timeframes on read.
- Tracks what date ranges are cached per (provider, symbol) with day-level granularity.
- Surfaces coverage visually (timeline with gaps).
- Downloads only what's missing, with 1-day overlap at edges for reconciliation.
- Is used by both the manual download UI and the backtest runner's pre-flight check.

## Design

### Core primitive: coverage index

A lightweight in-memory index (rebuilt from disk on startup) that answers:

```
coverage.get_ranges("polygon", "SPY") → [(date(2026,1,2), date(2026,1,31)), (date(2026,4,1), date(2026,4,15))]
coverage.get_gaps("polygon", "SPY", date(2026,1,1), date(2026,5,18)) → [(date(2026,2,1), date(2026,3,31)), (date(2026,4,16), date(2026,5,18))]
```

**Implementation:** scan the 1-min parquet's `timestamp` column, extract unique dates, find contiguous runs. Cache the result; invalidate when new data is written.

**Storage:** no new DB table. The parquet IS the source of truth. The index is a `dict[(provider, symbol), list[tuple[date, date]]]` rebuilt lazily.

### Timeframe unification

**On disk:** only 1-min bars are stored as the primary download target. The `DataService.load_market_data(provider, symbol, timeframe)` path already exists — extend it:

- If `timeframe == "1min"`: return the parquet directly (current behavior).
- If `timeframe` in `("5min", "15min", "1h", "1d")`: load the 1-min parquet and call `DataService.aggregate_bars(df, timeframe)` (already implemented in Task 10 of sub-project 2).
- If the user explicitly requests provider-computed bars (e.g., "polygon-native 1day"): download and store in a separate file (`polygon/SPY/1day_native.parquet`). This is opt-in for comparison; the default path always derives from 1-min.

**In the UI:** group all timeframes under one asset row. Instead of showing:
```
SPY  1min   polygon
SPY  5min   polygon
SPY  1day   polygon
```
Show:
```
▸ SPY  (polygon)   coverage: Jan 2–Apr 15   [timeline bar]
```
Expanding shows the available timeframes (1min stored, 5min/15min/1h/1d derived). The timeline bar is a horizontal line with green segments for covered dates and gaps between.

### Gap-filling downloads

When the user (or the backtest runner) needs data for a range:

1. Call `coverage.get_gaps(provider, symbol, start, end)`.
2. For each gap, expand by 1 day on each edge (overlap for reconciliation).
3. Submit a download job per gap via the existing `DownloadManager`.
4. On download completion, merge into the existing 1-min parquet via `DataService.save_market_data` (which already deduplicates by timestamp).
5. Invalidate the coverage index for that (provider, symbol).

**Unification:** both the Data page's "Download missing" button and the backtest runner's `_has_coverage` + download logic use the same function:

```python
async def ensure_coverage(
    provider: str, symbol: str, start: date, end: date,
    download_manager: DownloadManager, data_service: DataService,
) -> list[str]:
    """Return download IDs for any gaps. Empty list if fully covered."""
    gaps = coverage_index.get_gaps(provider, symbol, start, end)
    download_ids = []
    for gap_start, gap_end in gaps:
        # Expand by 1 day for edge reconciliation
        dl_start = gap_start - timedelta(days=1)
        dl_end = gap_end + timedelta(days=1)
        dl = await download_manager.create_download(
            symbols=[symbol],
            date_range_start=dl_start,
            date_range_end=dl_end,
            provider=provider,
            timeframe="1min",
        )
        download_ids.append(dl["id"])
    return download_ids
```

### Backtest coverage fix

Replace `_has_coverage` in `backtest_runner.py` with a call to `ensure_coverage`. The current check is:

```python
if not _has_coverage(ds, source, symbol, timeframe, start, end):
    # download the whole range
```

Replace with:

```python
gaps = coverage_index.get_gaps(source, symbol, start, end)
if gaps:
    for gap in gaps:
        await download_manager.create_download(...)
```

This fixes the bug: short backtests no longer poison the coverage check for longer backtests.

### UI: asset tree with timeline

The Data page's "AVAILABLE DATA" section becomes:

```
▸ Polygon                                    12 assets
  ▸ SPY   ██████░░░░██████████   Jan 2 – Apr 15 (gap: Feb 1 – Mar 31)
  ▸ QQQ   ████████████████████   Jan 2 – May 18 (complete)
  ▸ AAPL  ██████████░░░░░░░░░░   Jan 2 – Feb 28
▸ Alpaca_live                                 2 assets
  ▸ SPY   ░░░░░░░░░░████████░░   Apr 18 – May 19 (live)
```

Each row:
- Asset name
- A colored timeline bar (green = have data, gray = gap)
- Date range label
- Click to expand: shows timeframe breakdown, download actions

The "Download missing" action on any asset: computes gaps relative to a user-specified date range (or defaults to "fill all gaps from the earliest data to today"), submits downloads via `ensure_coverage`.

### Default download behavior

When the user clicks "Download" for a new asset+range:
- Default timeframe: `1min` (no timeframe selector — always 1min).
- If the user toggles "Also download provider-native bars": downloads 5min/15min/1h/1d as separate `_native.parquet` files for comparison.
- The timeframe selector goes away from the main download form. It only appears in an "Advanced" section or comparison mode.

### Migration of existing data

Existing data on disk stays as-is. The coverage index reads from whatever parquet files exist. If a user has `polygon/SPY/5min.parquet` but no `1min.parquet`, the 5-min data still loads fine — it just can't derive sub-5-min timeframes. The system doesn't delete existing higher-timeframe files; it just stops creating new ones by default.

## Non-goals

- Deleting existing higher-timeframe parquet files (they're harmless and may be useful for comparison).
- Real-time coverage tracking for live data (live feeds write directly; the coverage index only tracks historical downloads).
- Cross-provider coverage merging (each provider's coverage is independent).
- Sub-day gap detection (gaps are tracked at day granularity, not per-bar).

## Tests

- Coverage index: given a parquet with known timestamps, returns correct ranges and gaps.
- `ensure_coverage`: with a gap in the middle, produces exactly the right download jobs with 1-day edge overlap.
- Backtest runner: a short backtest followed by a longer backtest correctly downloads only the missing portion.
- `load_market_data` for derived timeframes: returns correctly aggregated bars from 1-min source.
- UI: asset tree renders with timeline bars (frontend smoke test).

## Out-of-scope follow-ups

- Sub-day gap detection (bar-level completeness checking within a day).
- Automatic nightly gap-fill (cron job that ensures all tracked assets are up to date).
- Coverage comparison across providers (e.g., "Polygon has Jan–May but Alpaca only has Apr–May").
