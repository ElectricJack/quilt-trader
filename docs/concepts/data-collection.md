# Data Collection

> Quilt manages market data and custom datasets so your algorithms have one source of truth for both live trading and backtests.

## What you'll learn

- The on-disk storage layout under `data/market/`, `data/custom/`, and `data/datasets/`.
- How the bundled providers differ (asset classes, history depth, paid vs free, live streaming).
- The split between live **subscriptions** and historical **downloads**, and how both land in the same Parquet store.
- How the **coverage index** decides what to refetch.
- The **datasets framework** for non-bar time series (FMP fundamentals, disclosures, earnings calendar).
- Where custom-scraped, non-price data fits.

## The problem this solves

Free market-data APIs are full of holes. Yahoo Finance silently truncates intraday history to the last seven days. The official broker REST APIs (Alpaca, Tradier) only return data when you have the right plan, and they return it at *request time* — five years from now, a backtest against today's market would be replaying whatever data the API happens to surface that day, not what was actually visible to a strategy back then. Paid APIs (Polygon's higher tiers, ThetaData) close some of those gaps, but a backtest that has to make a network call per symbol per day to a metered API is not a backtest you can run repeatedly.

Quilt resolves this by treating its local Parquet store as the system of record. Every bar that comes in — whether from a historical download run yesterday, a live subscription streaming right now, or a one-off REPL fetch — is persisted to `data/market/{provider}/{symbol}/{timeframe}.parquet`. The backtest engine reads from the same files the live algorithm reads from. There is no "live data" code path and "backtest data" code path; there is one data path with two writers.

The coverage index tracks what's already on disk by contiguous date range, so a download request for "AAPL 2024-01-01 → 2024-12-31" against a symbol you already have from March through August will only fetch the two missing gaps. Storage is local and append-only; you can rebuild it but you can't quietly lose it the way a cloud API tier change can.

## How Quilt does it

### Storage layout

All market data lives under `data/market/`. The layout is provider-segmented, symbol-keyed, and timeframe-sliced:

```
data/
├── market/
│   ├── polygon/
│   │   ├── AAPL/
│   │   │   ├── 1min.parquet
│   │   │   ├── 5min.parquet
│   │   │   ├── 15min.parquet
│   │   │   ├── 30min.parquet
│   │   │   ├── 1hour.parquet
│   │   │   └── 1day.parquet
│   │   └── SPY/
│   │       └── ...
│   ├── yfinance/
│   │   ├── VIX/
│   │   │   └── 1day.parquet
│   │   └── BTCUSD/
│   │       └── 1day.parquet
│   ├── tradier/
│   │   └── BTCUSD/
│   ├── alpaca_live/
│   │   └── SPY/
│   │       ├── 1min.parquet
│   │       └── ticks/
│   │           ├── trades-2026-06-02.parquet
│   │           └── quotes-2026-06-02.parquet
│   ├── tradier_live/
│   │   └── ...
│   └── coinbase_live/
│       └── BTCUSD/
├── custom/
│   └── alpha-picks-scraper.csv
└── datasets/
    └── fmp/
        ├── earnings_calendar.parquet
        └── house_disclosures.parquet
```

Why Parquet: columnar, compressed, typed, and pandas/pyarrow can memory-map it. A year of 1-minute SPY bars is a few megabytes. The path is computed in one place — `DataService.market_data_path` in `coordinator/services/data_service.py:15` — and every reader and writer goes through it.

The `_live` provider suffix (e.g. `alpaca_live`, `tradier_live`, `coinbase_live`) is what live subscriptions write under. `DataService.load_market_data` checks both the bare provider directory and its `_live` sibling when resolving a read, so an algorithm asking for `polygon` AAPL bars transparently picks up `polygon_live` ticks that have been folded into 1-minute bars. Under each live symbol directory you'll also see a `ticks/` subdirectory holding raw daily trade and quote captures (`trades-YYYY-MM-DD.parquet`, `quotes-YYYY-MM-DD.parquet`) that get aggregated into the standard 1min parquet on a rolling basis.

`data/custom/` is where the **scraper engine** drops the output of arbitrary user-defined scraper packages — typically a single CSV file per scraper, swapped atomically when the scraper re-runs. `data/datasets/` is the home for the **datasets framework** (see below), partitioned by provider then dataset name.

### Provider comparison

| Provider | Asset classes | History depth (free tier) | Live stream? | Paid? |
|---|---|---|---|---|
| **Polygon** (`polygon.py`) | Equities, options, index aggregates | 2 years (free Starter); deeper with paid plan | Tick stream is paid; bars are end-of-day on free | Free Starter works; real-time chain snapshots paid |
| **Tradier** (`tradier.py`) | Equities (daily only via `/markets/history`) | Multi-year, gated on a brokerage account | Separate live stream surface (`tradier_live`) | Free with a Tradier brokerage account |
| **Alpaca** (`alpaca.py`) | Equities (1min/5min/15min/1hour/1day) | Full coverage gated by data plan | Yes — `alpaca_live` writes ticks + bars | Paid data plan for serious historical depth |
| **ThetaData** (`theta.py`) | Equities EOD + intraday trades | Plan-dependent | No live stream wired in | Paid (username/password auth) |
| **yfinance** (`yfinance_provider.py`) | Equities, indices (VIX, SPX, NDX, RUT, DJI), crypto (BTC/ETH/SOL) | Long for daily; **1min capped at last 7 days** | No — 15–20 min delayed | Free, no API key |

The supported timeframe strings — `1min`, `5min`, `15min`, `1hour`, `1day` — are uniform across providers, with the exception that Tradier's historical bars endpoint is daily-only and will raise on anything else.

Per the recent audit of Polygon's REST surface (`docs/notes/polygon-endpoints.md`), Polygon's free Starter tier covers `/v3/reference/tickers`, `/v3/reference/options/contracts`, and the `/v2/aggs/...` aggregates endpoint used for bars. The provider surfaces `bid`/`ask` on each bar row when Polygon includes them (rare for equities, common for option contracts). Real-time option chain snapshots remain a paid endpoint.

### Subscriptions vs downloads

There are two ways data lands on disk, and they are deliberately separated:

- **Subscriptions** are long-running live streams. You tell the coordinator "subscribe to Alpaca for AAPL," and from that moment forward every trade and quote tick flows in over WebSocket and is appended to `data/market/alpaca_live/AAPL/`. Subscriptions accumulate data over wall-clock time. A subscription started today will give you 30 days of 1-minute bars 30 days from now. Subscriptions live in the `live_subscriptions` table; their lifecycle is owned by the coordinator (`/api/live-subscriptions`).

- **Downloads** are bounded historical fetches. You tell the coordinator "download AAPL from 2024-01-01 through 2024-12-31 from Polygon," and a `MarketDataDownload` job is queued, run by `DownloadManager` (`coordinator/services/download_manager.py:18`), and persisted via the provider's `fetch_bars` (paginated for Polygon, single-shot for the others). The result lands in the same Parquet files a subscription would write to, just under the historical provider directory rather than `_live`.

Both writers go through `DataService.save_market_data`, which de-duplicates by `timestamp` (last write wins) so you can re-run a download over a range you already have without corrupting it.

### Coverage index

`CoverageIndex` (`coordinator/services/coverage_index.py:45`) is the lightweight in-memory index of what date ranges live on disk per `(provider, symbol)`. It scans the 1-minute parquet (or falls back to a coarser timeframe) on first read, splits the timestamps into contiguous business-day runs, and caches the result.

It exists so the coordinator can answer two questions cheaply: "what date ranges do I already have?" (`get_ranges`) and "given a requested window, what's missing?" (`get_gaps`). The goal processor and data download job both use `get_gaps` to skip work — a re-download request for 2024 against a symbol that already has Q1–Q3 will only fetch Q4. The index is invalidated whenever a new parquet write lands, so it stays in sync without manual upkeep.

Three or more consecutive missing business days break a run into two ranges — that's deliberate, so a single missing holiday or a one-off API hiccup doesn't artificially split coverage.

### The datasets framework

Bars-per-symbol covers most market data needs, but not everything is a price bar. Earnings calendars, congressional trading disclosures, insider transactions, and quarterly income statements all have their own event-date and knowledge-date semantics. The **datasets framework** (`coordinator/services/datasets/`) handles them as first-class registered specs.

A dataset is described by a `DatasetSpec` (`coordinator/services/datasets/registry.py:13`):

```python
DatasetSpec(
    name="fmp.earnings_calendar",
    provider="fmp",
    endpoint_path="/stable/earnings-calendar",
    event_date_column="date",
    knowledge_date_column=None,
    symbol_keyed=False,
    id_columns=("date", "symbol"),
    columns={"date": "date", "symbol": "str", "eps": "float", ...},
    pagination=Pagination.DATE_RANGE,
    date_chunk_days=365,
)
```

The spec declares the upstream endpoint, the columns the dataset has and their dtypes, which column is the event date (when the thing happened) and which is the knowledge date (when you could have known about it — important for survivorship-bias-free backtests), and how the API paginates. The framework handles fetching, chunking, deduping by `id_columns`, and writing to `data/datasets/{provider}/{name}.parquet`.

The currently bundled provider is **FMP** (Financial Modeling Prep), with specs in `coordinator/services/datasets/providers/fmp_datasets.py`:

- `fmp.house_disclosures` — U.S. House congressional trading filings
- `fmp.senate_disclosures` — U.S. Senate trading filings
- `fmp.insider_trading` — corporate insider Form 4 transactions
- `fmp.income_statement` — quarterly/annual income statements per ticker
- `fmp.earnings_calendar` — date-range earnings calendar

Algorithms read datasets the same way they read market bars: through the SDK, with the framework handling the storage layout for you. Adding a new dataset means writing a `DatasetSpec` and an adapter for the new provider — the storage layer, scheduling, and quota tracking come for free.

### Custom data via scrapers

For data that doesn't fit a clean REST-and-pagination model — anything you'd want to scrape from a webpage, RSS feed, or PDF — Quilt uses a separate **scraper engine** that runs scraper packages from `data/packages/` on a cron, dropping the output under `data/custom/`. See [scrapers.md](scrapers.md) for the full treatment.

## Worked example

Start a live stream, watch the parquet appear, then backfill the rest of the year. All three commands assume `quilt` is on your PATH and the coordinator is running.

**1. Subscribe to live Alpaca data for AAPL** with a 30-day tick-retention window:

```bash
quilt data subscribe alpaca AAPL --retention-hours 720
```

This `POST`s to `/api/live-subscriptions` and starts the stream. The coordinator hands the work to an Alpaca worker; ticks begin landing in `data/market/alpaca_live/AAPL/`. The `broker` and `symbol` are positional; `--retention-hours` controls how long raw ticks stay before they're collapsed into bars.

**2. After a minute or two, look at what's on disk:**

```bash
ls data/market/alpaca_live/AAPL/
# 1min.parquet
# ticks/
```

You'll see the 1-minute aggregate parquet plus a `ticks/` subdirectory holding daily raw trade and quote files (`trades-YYYY-MM-DD.parquet`, `quotes-YYYY-MM-DD.parquet`).

**3. Backfill the rest of 2024 from Polygon:**

```bash
quilt data download --symbol AAPL --start 2024-01-01 --end 2024-12-31 \
  --provider polygon --timeframe 1day
```

The coordinator queues a `MarketDataDownload`, dispatches it through `BarsJobDispatcher` (`coordinator/services/download_job.py:30`), Polygon paginates back-to-back pages with rate-limit-aware retries, and the result lands under `data/market/polygon/AAPL/1day.parquet`. You can track progress with `quilt data downloads`. Re-running the same command after it succeeds will be a no-op for whatever the coverage index already considers covered.

`--symbol` accepts repetition (`--symbol AAPL --symbol MSFT`) to queue a multi-symbol download in one call. `--provider` defaults to `polygon`, `--timeframe` to `1day`, `--data-type` to `bars`.

## Limits & sharp edges

- **Polygon free-tier history is 2 years.** Earlier dates return empty results — you'll get a successful "0 bars" download, not an error. Older data needs a paid plan.
- **Tradier historical data requires a brokerage account** (free to open, but you have to fund it eventually to keep API access). Tradier's `/markets/history` endpoint is daily-only; minute and hourly history is not supported by this provider.
- **Alpaca historical depth depends on your data plan.** The free tier gets you recent bars; full history needs a paid plan. The live stream is independent of that plan.
- **yfinance intraday is capped at 7 days** by Yahoo. 1-minute requests beyond that window silently return less data than asked. Daily history is fine.
- **No automatic data backup.** Everything under `data/` lives on the coordinator host's local disk. If you care about durability, snapshot the directory yourself (it's just files; rsync or btrfs send works).
- **Storage grows linearly with subscriptions.** A 1-minute SPY subscription is a few MB/year, but a few hundred symbols add up. Archival is not auto-managed; you can manually delete provider directories you no longer want without breaking anything else (the coverage index re-scans).
- **Symbol naming is provider-canonical, not user-canonical.** The asset registry resolves user-facing symbols (e.g. `VIX`) to provider-specific tickers (e.g. `^VIX` for yfinance). Going around it by writing files under a non-canonical directory will leave them invisible to most reads.

## See also

- [backtest-accuracy.md](backtest-accuracy.md) — how this stored data feeds the backtest engine, and why same-data is half the accuracy story.
- [scrapers.md](scrapers.md) — adding custom, non-price data via the scraper engine.
- [../notes/polygon-endpoints.md](../notes/polygon-endpoints.md) — Polygon REST endpoint quirks and free-tier limits in detail.
