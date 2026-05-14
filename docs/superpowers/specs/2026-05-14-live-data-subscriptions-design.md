---
title: Live Data Subscriptions & Multi-Dataset Compare
status: design
date: 2026-05-14
---

# Live Data Subscriptions & Multi-Dataset Compare — Design

Add live market-data subscriptions sourced from broker WebSocket feeds (the same feeds live algorithms trade against), aggregate the streams into both raw-tick and bar storage organized by source, and provide a multi-dataset comparison view so the user can verify that broker live data lines up with historical data from providers like Polygon.

## Goals

1. Live broker feeds (Alpaca, Tradier) ingested into the project's existing `data/market` filesystem, organized by source.
2. Raw ticks (trades + quotes) stored with configurable retention (default 24h, per-source override).
3. Bars (1min onward) derived from the same tick stream, stored long-term in the existing layout.
4. Algorithms declare data needs in their manifest; the framework resolves `broker_live` to the account's broker; missing subscriptions block algo startup with a clear error (no silent fallback).
5. A multi-dataset compare view supporting three modes (overlay, stacked panels, diff) with shared zoom/pan state.
6. The whole live-feed subsystem runs inside the coordinator process and uses the existing dependent-count lifecycle pattern (mirrors `ScraperManager`).

## Non-Goals

- Tick-based backtesting (the storage layout supports this later, but the backtest harness change is out of scope here).
- Polygon-live or other non-broker live feeds. The live source for this spec is the broker the algo trades on; that's the validation story.
- Replacing or rewriting the historical download pipeline; it stays as-is.
- A general-purpose stream router. Subscriptions are flat `(broker, symbol)` pairs.

---

## 1. Sources, naming, storage layout

### Source names

Live sources are named `{broker}_live`:

- `alpaca_live` — Alpaca WebSocket trade + quote stream.
- `tradier_live` — Tradier WebSocket trade + quote stream.

Historical sources keep their current names (`polygon`, `theta`, etc.). `DataService.list_available_market_data()` already groups by source; the new live sources slot into that grouping with no schema change.

### Filesystem layout

```
data/market/
  alpaca_live/
    SPY/
      ticks/
        trades-2026-05-14.parquet      # one file per UTC day
        quotes-2026-05-14.parquet
      1min.parquet                     # existing layout
      5min.parquet
      1hour.parquet
      ...
    QQQ/
      ticks/...
      1min.parquet
  tradier_live/
    SPY/
      ...
  polygon/                             # unchanged
    SPY/
      1min.parquet
```

Per-day tick files make retention a `os.remove()` per expired day instead of a parquet rewrite. Trades and quotes are stored separately because their schemas differ.

### Schemas

**Trade tick parquet** (`trades-{YYYY-MM-DD}.parquet`):
- `timestamp` (datetime64[us, UTC]) — exchange timestamp, microsecond precision.
- `price` (float64)
- `size` (int64)
- `exchange` (string)
- `conditions` (list[string], nullable) — broker-specific trade conditions.
- `trade_id` (string, nullable) — for dedup on reconnect replay.

**Quote tick parquet** (`quotes-{YYYY-MM-DD}.parquet`):
- `timestamp` (datetime64[us, UTC])
- `bid` (float64)
- `bid_size` (int64)
- `ask` (float64)
- `ask_size` (int64)
- `bid_exchange` (string, nullable)
- `ask_exchange` (string, nullable)

**Bar parquet** (existing schema, unchanged): `timestamp, open, high, low, close, volume`.

### Bar derivation

Bars are computed from the **trade** tick stream (not quotes), in-memory by the aggregator service. At each bar close (e.g. `:00`, `:01`, … for 1min), the rolling buffer flushes one row to the appropriate parquet. The aggregator never re-reads the tick parquets to build bars — only the live in-memory buffer. This makes bar latency at most one bar-interval after the trades arrive.

Higher-timeframe bars (5min, 15min, 1hour, 1day) are computed on a separate schedule by resampling the saved 1min parquet: simple "rewrite the file" approach, same pattern the existing `DataService.save_market_data()` uses for overlap merges.

---

## 2. Subscription model

### Database

New table `live_subscriptions`:

```python
class LiveSubscription(Base):
    __tablename__ = "live_subscriptions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    broker: Mapped[str] = mapped_column(String, nullable=False)        # "alpaca" | "tradier"
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="stopped")  # stopped | running | error
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_tick_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    tick_rate_per_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # observed rate, sharpens over time
    tick_retention_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    dependent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # # of algo instances depending on it
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (UniqueConstraint("broker", "symbol", name="uq_live_subscription_broker_symbol"),)
```

The uniqueness constraint enforces broker-scoped (not account-scoped) subscriptions — one Alpaca SPY stream serves every Alpaca account.

### Settings

A per-broker setting picks the account whose creds the live feed uses:

- `live_feed_account.alpaca` → `account_id`
- `live_feed_account.tradier` → `account_id`

Stored in the existing `Setting` table. If unset for a broker, subscriptions for that broker can be created but won't start until the setting is configured (status stays `stopped`, `last_error` explains).

### Lifecycle (mirrors `ScraperManager`)

The new `LiveFeedManager` (`coordinator/services/live_feed_manager.py`) tracks running streams and their dependent counts. Methods follow the existing scraper-manager API for consistency:

```python
class LiveFeedManager:
    def register(self, broker: str, symbol: str) -> None
    def add_dependent(self, broker: str, symbol: str, instance_id: str) -> None
    def remove_dependent(self, broker: str, symbol: str, instance_id: str) -> None
    def dependent_count(self, broker: str, symbol: str) -> int
    def ensure_running(self, broker: str, symbol: str, instance_id: str) -> None
    def release(self, broker: str, symbol: str, instance_id: str) -> bool
```

- **Manual subscriptions** (created from the Data page UI) are registered with `add_dependent("__manual__", ...)` so they stay running even with no algo consumers. Releasing the manual dependent count from the UI's "Unsubscribe" button is what stops them.
- **Auto subscriptions** are added by `LifecycleService` when an algorithm instance starts: walk the manifest's `data_dependencies`, call `ensure_running(broker_of_account, symbol, instance_id)` for each `source: broker_live` entry. On instance stop, `release(...)`.

### Algo manifest changes

`quilt.yaml` `data_dependencies` entries gain a `source` field:

```yaml
requirements:
  data_dependencies:
    - symbol: SPY
      timeframe: 1min
      source: broker_live          # resolves to the account's broker at start time
    - symbol: VIX
      timeframe: 1day
      source: polygon              # specific historical source
```

`source` values:
- `broker_live` — uses `{account.broker_type}_live`.
- A specific source name (`polygon`, `theta`) — used as-is.
- Omitted: defaults to `broker_live` (the spec's stated guarantee that live algos use the broker feed they trade on).

`ManifestRequirements` in `sdk/manifest.py:14` gains nothing structurally — `data_dependencies` is already a `list[dict]`. The new validation enforces that `source` (if present) is recognized.

### Algo startup gating

When an algorithm instance starts (`LifecycleService.start_instance` or equivalent path):

1. Walk `manifest.data_dependencies`.
2. For each entry with `source: broker_live`: resolve to `{account.broker_type}_live`. Check that a `LiveSubscription(broker=account.broker_type, symbol=...)` exists AND `status == "running"`. If not, **refuse to start** with a clear error: `"Cannot start: no live subscription for SPY on alpaca. Subscribe on the Data page first."` The instance status flips to `error` with the message in `last_error` or equivalent.
3. For each entry with `source: <historical>`: existing check — confirm parquet exists.

No silent fallback; if a live subscription isn't running, the algo refuses to run.

---

## 3. Aggregator service

### Architecture

`LiveFeedAggregator` lives in `coordinator/services/live_feed_aggregator.py` and runs as part of the coordinator's lifespan (registered in `coordinator/services/lifecycle.py` like `DownloadManager`, `ScraperEngine`). One asyncio task per `(broker, symbol)` subscription that's `running`.

### Per-subscription task

For each running subscription:

1. Open a broker-specific WebSocket using the account creds resolved via the `live_feed_account.{broker}` setting. Reuse the existing broker adapter (extend with a `start_market_data_stream(symbols) -> AsyncIterator[Tick]` method).
2. Loop:
   - Receive a normalized `Tick(kind="trade"|"quote", payload=...)`.
   - Append the trade or quote to the in-memory daily buffer for its kind.
   - For trades: also feed into the 1min bar buffer; if the bar interval just rolled, flush the closed bar to `data/market/{source}/{symbol}/1min.parquet` (via the existing `DataService.save_market_data` overlap-merge path).
   - Every N seconds (tunable, default 5s), flush the buffered trades and quotes to today's tick parquets, append-mode, taking a per-file `asyncio.Lock` to prevent partial writes.
3. On WS disconnect: exponential-backoff reconnect (1s → 30s cap); status → `error` while disconnected, back to `running` on reconnect. `last_error` shows the latest disconnect reason.
4. Per-minute: update `tick_rate_per_min` on the row from observed counts (used by the storage estimator).
5. On `stop()`: cancel the task, flush pending buffers, close the WS.

### Higher-timeframe aggregation

A separate periodic job (`live_feed_aggregator.resample_higher_timeframes`) runs once per minute. For each running subscription:

- Read the latest 1min parquet for the symbol.
- For each target timeframe (5min, 15min, 1hour, 1day): aggregate the latest 1min rows into the next pending higher bar; if the higher bar's interval just closed, write the closed row to the corresponding parquet via the existing `DataService` save path.

This is "lazy resampling at the consumer's cadence" — daily bars get one new row per market-close, not real-time. Good enough for the comparison use case.

### Tick retention

Once per hour:

- For each subscription with `tick_retention_hours = N`: enumerate trade/quote parquets under its `ticks/` dir; `os.remove` any whose date is older than `(today_utc - N // 24) days` (rounded). With the default 24h, this means yesterday's tick files get deleted today.
- For retention windows finer than 1 day (e.g. 12h): not supported in v1 — the per-day file granularity sets the floor. Configurable in increments of 24h.

The setting on the subscription is `tick_retention_hours: int`, default 24, settable on creation and via PATCH. Validation: must be a positive multiple of 24 between 24 and 720 (30 days). Anything longer than 30 days is rejected with a "edit the limit in code if you really want this" message — the storage estimator is the better guardrail.

---

## 4. Storage estimator

Shown in the "Subscribe" modal so the user understands the disk-cost commitment before clicking subscribe. Computed both client- and server-side as:

```
projected_bytes = tick_rate_per_min * 60 * 24 * retention_days * BYTES_PER_TICK
```

Where:
- `BYTES_PER_TICK` is a tuned constant ≈ **80 bytes/trade tick + 90 bytes/quote tick** at parquet+snappy (verify empirically during implementation; document the measured value in the source).
- `tick_rate_per_min`:
  - **At subscribe time (no observation yet):** coarse estimate from Polygon trade-count or similar. As a first-cut hardcoded table: SPY/QQQ/popular ETFs ≈ 200 trades/min, mid-cap equities ≈ 20 trades/min, low-volume tickers ≈ 5 trades/min. Document that this is an estimate.
  - **After running:** the subscription's observed `tick_rate_per_min` field (updated each minute by the aggregator).

UI: the modal renders "Estimated storage: ~120 MB for 24h tick retention" with a tooltip explaining the estimate sharpens after running. The same estimator is used in the Settings UI when adjusting retention.

API: `GET /api/live-subscriptions/estimate?broker=alpaca&symbol=SPY&retention_hours=24` returns `{tick_rate_per_min, projected_bytes, projected_human, source: "estimated" | "observed"}`.

---

## 5. API surface

New router `coordinator/api/routes/live_subscriptions.py`, prefix `/api/live-subscriptions`:

| Method | Path | Body / Query | Notes |
|---|---|---|---|
| `GET` | `/` | — | List all subscriptions, joined with dependent_count, last_tick_at. |
| `POST` | `/` | `{broker, symbol, tick_retention_hours?}` | Create + start. 409 if already exists for `(broker, symbol)`. |
| `GET` | `/{id}` | — | Detail view. |
| `PATCH` | `/{id}` | `{tick_retention_hours?}` | Currently only retention is mutable. |
| `POST` | `/{id}/unsubscribe` | — | Releases the **manual** dependent only. The subscription stays running if algos still depend on it; stops only when the last consumer (manual or algo) is gone. 200 always. |
| `DELETE` | `/{id}` | — | Hard delete. Refused with 409 + the algo list if `dependent_count > 0`. On success, removes the row, deletes the `ticks/` dir, and leaves bar parquets in place (historical data preserved). |
| `POST` | `/{id}/restart` | — | Stop and re-start the underlying aggregator task — useful after fixing a creds setting. |
| `GET` | `/estimate` | `?broker=&symbol=&retention_hours=` | Storage estimator. |

Existing endpoints that also need awareness:

- `GET /api/data/available` already groups by source; the new `alpaca_live` / `tradier_live` sources show up automatically once parquets exist.
- New: `GET /api/data/market/{symbol}?source=alpaca_live&timeframe=1min&bars=100` — `source` query param replaces the current hardcoded `provider` default. Backward-compatible: omitting `source` falls back to the first available historical source for the symbol.

---

## 6. Algorithm consumption

`TickContext.market_data(symbol, timeframe, bars)` (`sdk/context.py:50`) gains an optional `source` parameter:

```python
@abstractmethod
def market_data(self, symbol: str, timeframe: str = "1min", bars: int = 100,
                source: str | None = None) -> pd.DataFrame: ...
```

Resolution rules at the worker side (`worker/data_client.py`):

1. If `source` is provided explicitly: use it.
2. Else: look up the algo's manifest `data_dependencies` for an entry matching `symbol` + `timeframe`. Use its declared `source` (resolving `broker_live` to `{account.broker_type}_live`).
3. Else: default to `broker_live` (resolved as above).

The startup gating in §2 guarantees that whatever `market_data()` resolves to has an active subscription (for `broker_live`) or saved parquet (for historical). No runtime "data not available" surprises during a tick.

---

## 7. Comparison UI

### Location

A new "Compare" tab on the existing `dashboard/src/pages/Data.tsx` page, next to the existing "Available Data" section. Multi-select happens by checkbox in the Available Data grid; a "Compare selected" button reveals when 2+ are checked.

The existing `DatasetPreviewModal` (single-dataset chart) stays — it's the single-select path. Compare is its multi-select counterpart.

### Modes

Three modes, switchable from a top-of-view toggle:

- **Overlay** — All selected series on one chart, distinct colors, shared y-scale.
- **Stacked panels** — One chart row per series; x-axes synchronized; each series keeps its own y-scale.
- **Diff** — Only enabled when exactly 2 series are selected. Renders the primary (left in the selection) as a chart on top, and `primary − comparison` (price delta) plus `% match per bar` as a second chart below. Bars whose timestamps don't align are marked as `N/A` and rendered as gaps.

### Shared viewport state

A `useChartViewport()` hook (lifted from a `ChartViewportContext` provider that wraps the compare view) holds:

```ts
{
  visibleRange: { from: UTCTimestamp; to: UTCTimestamp } | null,
  yScaleMode: "auto" | { min: number; max: number },
}
```

Mode-switch components subscribe to that context; on mount each chart instance calls `timeScale().setVisibleRange(visibleRange)` if non-null. On pan/zoom in any chart, the context updates; other charts get the new range on the next render. Lightweight-charts emits these events via `timeScale().subscribeVisibleTimeRangeChange()`.

### Diff alignment

Bar timestamps are aligned by **rounding each to its timeframe's interval boundary** (1min → strip seconds, 5min → strip minutes mod 5, etc.). After rounding, paired bars are looked up by timestamp key. Either side missing → `N/A` for that bar.

This is naive but adequate: live and historical providers nearly always close 1min bars at the same UTC minute boundary. If a real-world misalignment shows up empirically we can revisit.

### Routing & deep-linking

Compare state (selected datasets + mode + viewport) is encoded in the URL query string so a comparison can be bookmarked:

```
/data?compare=alpaca_live:SPY:1min,polygon:SPY:1min&mode=diff
```

---

## 8. Cross-cutting concerns

### Database migration

One alembic migration:

- Create `live_subscriptions` table per §2.
- Add `Setting` rows for `live_feed_account.{broker}` (optional — UI creates them on first save).

### Operational

- Coordinator process now holds N WebSockets to brokers (one per active subscription's broker). At small scale (≤ 2 brokers × ~10 symbols) this is fine. Document a soft limit of ~50 active subscriptions before reconsidering process model.
- Reconnect storms (e.g. all subscriptions on the same broker disconnect together when Alpaca pushes new WS endpoints) are mitigated by per-subscription random-jitter backoff.
- Disk pressure: tick retention default 24h × the storage estimator should keep typical (~10 active subs) within a few GB. The aggregator emits an Event when a subscription's storage projection exceeds 5 GB so the user gets a warning.

### Testing strategy

- **`LiveFeedManager`:** unit tests covering dependent-count add/release/should_stop, mirroring the existing `tests/coordinator/services/test_scraper_manager.py` pattern.
- **Tick → bar aggregation:** unit test feeding a sequence of synthetic trades into the aggregator, asserting the 1min bar flushed at the right boundary with correct OHLCV.
- **Algo startup gating:** API test that starting an instance with a `broker_live` dep but no active subscription returns the documented error and the instance ends up in `error` state.
- **Storage estimator:** unit test the math + the source-of-estimate switch (estimated vs observed).
- **Aggregator reconnect:** integration test with a mocked broker adapter whose stream raises mid-loop, asserting the task reconnects and the status row reflects `error → running`.
- **Compare view diff alignment:** component test feeding two intentionally misaligned series, asserting `N/A` rendering for unmatched bars.
- **End-to-end manual smoke:** subscribe to SPY on a paper Alpaca account during market hours, confirm ticks land in `data/market/alpaca_live/SPY/ticks/`, bars roll into `1min.parquet`, comparison view renders against `polygon` historical for the same date.

### Compatibility

- Existing algos with no `source` in their `data_dependencies` entries default to `broker_live` at startup. **Migration impact today: zero** — the only manifest in the repo is `packages/alpha-picks-scraper/quilt.yaml`, which is a scraper (no `data_dependencies` field). Future algo authors will need to declare `source: polygon` if they want pure-historical data; otherwise the spec's promise (live algos use broker live data) holds.
- `DataService.list_available_market_data()` requires no change; new sources appear because their parquets exist.
- `GET /api/data/market/{symbol}` gains a `source` query param defaulting to first available historical; existing callers are unaffected.

### Implementation order

1. Storage layout + schemas (no service code yet, just the parquet conventions written into `DataService`).
2. `LiveSubscription` model + migration + REST CRUD (paused subscriptions, no aggregator yet).
3. `LiveFeedManager` (dependent-count tracker, no I/O).
4. `LiveFeedAggregator` with one broker first (Alpaca) — tick ingest + 1min bar flush + retention sweeper.
5. Higher-timeframe resample.
6. Storage estimator endpoint + UI.
7. Algo manifest `source` field + startup gating + auto-subscribe wiring in `LifecycleService`.
8. Tradier adapter for the aggregator.
9. Compare UI with all three modes + shared viewport.

Each step lands in its own PR.
