# Data Coverage, Gap-Filling, and Timeframe Unification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A coverage index that tracks cached date ranges per (provider, symbol), gap-filling downloads that fetch only what's missing, timeframe unification (store 1min, derive the rest), and a backtest coverage fix that properly diffs ranges.

**Architecture:** New `CoverageIndex` class scans 1-min parquet timestamps → contiguous date ranges. `ensure_coverage()` diffs requested range vs cached → downloads gaps with 1-day edge overlap via existing `DownloadManager`. `DataService.load_market_data` derives higher timeframes from 1-min on read. Backtest runner replaces `_has_coverage` with `ensure_coverage`. Frontend groups assets by provider with timeline bars.

**Tech Stack:** Python + pandas + parquet on backend; React + Tailwind on frontend.

**Spec:** `docs/superpowers/specs/2026-05-19-data-coverage-and-gap-filling-design.md`

---

## Task 1: CoverageIndex — core primitive

**Files:**
- Create: `coordinator/services/coverage_index.py`
- Test: `tests/coordinator/services/test_coverage_index.py`

Build the `CoverageIndex` class:

```python
class CoverageIndex:
    def __init__(self, data_service: DataService):
        self._ds = data_service
        self._cache: dict[tuple[str, str], list[tuple[date, date]]] = {}

    def get_ranges(self, provider: str, symbol: str) -> list[tuple[date, date]]:
        """Return sorted list of contiguous date ranges on disk."""
        key = (provider, symbol)
        if key not in self._cache:
            self._cache[key] = self._scan(provider, symbol)
        return self._cache[key]

    def get_gaps(self, provider: str, symbol: str, start: date, end: date) -> list[tuple[date, date]]:
        """Return date ranges within [start, end] NOT covered by cached data."""
        ranges = self.get_ranges(provider, symbol)
        # Diff [start, end] against ranges → gaps
        ...

    def invalidate(self, provider: str, symbol: str) -> None:
        self._cache.pop((provider, symbol), None)

    def _scan(self, provider: str, symbol: str) -> list[tuple[date, date]]:
        """Load 1-min parquet, extract unique dates, find contiguous runs."""
        df = self._ds.load_market_data(provider, symbol, "1min")
        if df is None or df.empty:
            # Fall back to any available timeframe
            for tf in ("1day", "5min", "15min", "1hour"):
                df = self._ds.load_market_data(provider, symbol, tf)
                if df is not None and not df.empty:
                    break
        if df is None or df.empty:
            return []
        # Extract unique dates, sort, find contiguous runs
        ...
```

Tests:
- Given a parquet with dates [Jan 2-5, Jan 8-10], `get_ranges` returns `[(Jan 2, Jan 5), (Jan 8, Jan 10)]`
- `get_gaps(Jan 1, Jan 12)` returns `[(Jan 1, Jan 1), (Jan 6, Jan 7), (Jan 11, Jan 12)]`
- `invalidate` clears the cache for that key
- Empty parquet returns empty ranges

Commit: `feat(coord): CoverageIndex for tracking cached data date ranges`

---

## Task 2: ensure_coverage — unified gap-fill function

**Files:**
- Create: `coordinator/services/coverage_utils.py`
- Test: `tests/coordinator/services/test_coverage_utils.py`

```python
async def ensure_coverage(
    provider: str, symbol: str, start: date, end: date,
    download_manager: DownloadManager,
    coverage_index: CoverageIndex,
    timeframe: str = "1min",
) -> list[str]:
    """Download only what's missing. Returns download IDs."""
    gaps = coverage_index.get_gaps(provider, symbol, start, end)
    download_ids = []
    for gap_start, gap_end in gaps:
        dl_start = gap_start - timedelta(days=1)  # 1-day overlap
        dl_end = gap_end + timedelta(days=1)
        dl = await download_manager.create_download(
            symbols=[symbol],
            date_range_start=dl_start,
            date_range_end=dl_end,
            provider=provider,
            timeframe=timeframe,
        )
        download_ids.append(dl["id"])
    # Invalidate cache after downloads complete
    coverage_index.invalidate(provider, symbol)
    return download_ids
```

Tests:
- Full coverage → returns empty list, no downloads
- One gap in the middle → one download with 1-day edge overlap
- No data at all → one download spanning the whole range + overlap
- Multiple gaps → multiple downloads

Commit: `feat(coord): ensure_coverage for unified gap-fill downloads`

---

## Task 3: Backtest runner uses ensure_coverage

**Files:**
- Modify: `coordinator/services/backtest_runner.py`
- Modify: `coordinator/main.py` (wire CoverageIndex into container)

Replace the backtest runner's `_has_coverage` + full-range download with `ensure_coverage`:

In `backtest_runner.py`:
- Remove `_has_coverage` function
- In the deps loop, replace:
  ```python
  if not _has_coverage(self._ds, source, symbol, timeframe, start, end):
      dl = await self._dm.create_download(...)
  ```
  With:
  ```python
  from coordinator.services.coverage_utils import ensure_coverage
  dl_ids = await ensure_coverage(source, symbol, start.date(), end.date(),
      self._dm, self._coverage_index)
  for dl_id in dl_ids:
      await self._wait_for_download(dl_id)
  ```
- Add `coverage_index` to `BacktestRunner.__init__`

In `coordinator/main.py`:
- Create `CoverageIndex(data_svc)` in lifespan
- Pass it to `BacktestRunner`
- Store on container for API access

Tests:
- Short backtest downloads April 1-15. Longer backtest for Jan-May downloads only Jan 1–Mar 31 and Apr 16–May 18 (not April 1-15 again).

Commit: `fix(coord): backtest uses ensure_coverage for proper gap-diff downloads`

---

## Task 4: DataService derives higher timeframes from 1-min

**Files:**
- Modify: `coordinator/services/data_service.py`

Update `load_market_data(provider, symbol, timeframe)`:

```python
def load_market_data(self, provider, symbol, timeframe):
    # Try the exact file first (backwards compat + native bars)
    path = self.market_data_path(provider, symbol, timeframe)
    if os.path.exists(path):
        return pd.read_parquet(path)
    
    # Derive from 1-min if available
    if timeframe in ("5min", "15min", "1h", "1hour", "1d", "1day"):
        one_min = self.market_data_path(provider, symbol, "1min")
        if os.path.exists(one_min):
            df = pd.read_parquet(one_min)
            return self.aggregate_bars(df, timeframe)
    
    return None
```

Tests:
- Request "5min" when only "1min" exists → returns correctly aggregated bars
- Request "1min" directly → returns as-is
- Request "5min" when "5min.parquet" exists → returns native file (not derived)

Commit: `feat(coord): derive higher timeframes from 1-min on read`

---

## Task 5: Default downloads to 1-min only

**Files:**
- Modify: `coordinator/api/routes/data.py` (download endpoint default)
- Modify: `dashboard/src/pages/Data.tsx` (remove timeframe selector or default to 1min)

Backend: change the `DownloadRequest` default from `timeframe: str = "1day"` to `timeframe: str = "1min"`.

Frontend: in the download form, remove the timeframe dropdown or hide it behind an "Advanced" toggle. Default value: `"1min"`.

Commit: `feat: default downloads to 1-min; timeframe selector moved to advanced`

---

## Task 6: Coverage API endpoint

**Files:**
- Modify: `coordinator/api/routes/data.py`

Add `GET /api/data/coverage`:

```python
@router.get("/coverage")
async def get_coverage():
    """Return coverage ranges for all assets on disk."""
    svc = get_data_service()
    coverage = get_coverage_index()
    available = svc.list_available_market_data()
    result = {}
    for item in available:
        provider = item["provider"]
        symbol = item["symbol"]
        key = f"{provider}/{symbol}"
        if key not in result:
            ranges = coverage.get_ranges(provider, symbol)
            result[key] = {
                "provider": provider,
                "symbol": symbol,
                "ranges": [{"start": str(s), "end": str(e)} for s, e in ranges],
                "timeframes_on_disk": item.get("timeframes", []),
            }
    # Group by provider
    grouped = {}
    for v in result.values():
        grouped.setdefault(v["provider"], []).append(v)
    return {"providers": grouped}
```

Add `POST /api/data/fill-gaps`:

```python
@router.post("/fill-gaps")
async def fill_gaps(body: FillGapsRequest):
    """Download only what's missing for a given asset+range."""
    dl_ids = await ensure_coverage(
        body.provider, body.symbol, body.start, body.end,
        get_download_manager(), get_coverage_index(),
    )
    return {"download_ids": dl_ids, "gap_count": len(dl_ids)}
```

Commit: `feat(coord): coverage + fill-gaps API endpoints`

---

## Task 7: Frontend — asset tree with timeline bars

**Files:**
- Create: `dashboard/src/components/CoverageTimeline.tsx`
- Modify: `dashboard/src/pages/Data.tsx`

Replace the flat "AVAILABLE DATA" section with a grouped asset tree:

```
▸ Polygon                                12 assets
  ▸ SPY   ██████░░░░██████████   Jan 2 – Apr 15
  ▸ QQQ   ████████████████████   Jan 2 – May 18
▸ Alpaca_live                             2 assets
  ▸ SPY   ░░░░░░░░░░████████░░   Apr 18 – May 19
```

`CoverageTimeline` component:
- Takes `ranges: {start: string, end: string}[]` and renders a horizontal bar
- Green segments for covered dates, gray for gaps
- The bar spans from the earliest range start to today (or the latest range end)

Each row:
- Collapsible provider group
- Asset name + timeline bar + date range label
- "Fill gaps" button that calls `POST /api/data/fill-gaps`

Hook: `useCoverage()` → `GET /api/data/coverage`

Commit: `feat(dashboard): asset tree with coverage timeline bars`

---

## Task 8: Build + smoke test

Build dashboard, restart coord, verify:
1. Coverage API returns correct ranges
2. Downloading only downloads gaps
3. Backtest for a longer range after a shorter one only fetches the diff
4. Higher timeframes derive from 1-min on read
5. Timeline bars render correctly

Commit: build only (no code changes)

---

## Self-review

| Spec requirement | Task |
|---|---|
| Coverage index (date ranges + gaps) | 1 |
| ensure_coverage (unified gap-fill) | 2 |
| Backtest coverage fix | 3 |
| Derive higher timeframes from 1-min | 4 |
| Default downloads to 1-min | 5 |
| Coverage + fill-gaps API | 6 |
| Asset tree with timeline bars UI | 7 |
| Smoke test | 8 |
