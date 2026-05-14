# Polygon REST API endpoints — survey

Status as of 2026-05-13. Base URL: `https://api.polygon.io`. Free Starter tier is what we target.

## Free tier limits (key facts)
- **5 API requests per minute** — we proactively pace via `min_request_interval_s=13.0` in coordinator startup
- **2 years of historical data** — earlier dates return empty results
- **End-of-day data only** — no real-time; intraday bars have ~15-minute delay
- **Stocks aggregates work end-to-end** — verified
- **Trades / quotes endpoints are paid** — Starter does not include tick-level trades or quotes
- Polygon does NOT return X-RateLimit-* headers; we rely on 429 + `Retry-After` to back off

## Currently implemented
| Endpoint | Path | Returns | Free? | Status |
|---|---|---|---|---|
| Aggregates (bars) | `/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}` | OHLCV bars at custom timeframe | Yes (2yr history) | ✅ `PolygonProvider.fetch_bars` |

The aggregates endpoint paginates via `next_url` (cursor-based). Our code follows it correctly.

## High-value additions (in rough priority order)

### 1. Daily open-close — quick win
- Path: `/v1/open-close/{ticker}/{date}`
- Returns: open, high, low, close, volume, after-hours and pre-market prices for a single day
- Free tier: yes
- Effort: small (~half day) — new `fetch_open_close(symbol, date)` method, new `data_type` value `"open_close"`, simple storage as a row per date
- Use case: backtests that need daily anchor prices

### 2. Splits — needed for backtests
- Path: `/v3/reference/splits`
- Returns: historical splits with execution date and ratio (e.g., NVDA 10-for-1 on 2024-06-10)
- Free tier: yes
- Effort: small (~half day) — add `fetch_splits(symbol)` method; store as a separate `splits` table or under `data/reference/splits/{symbol}.parquet`
- Use case: corporate-action-adjusted backtesting

### 3. Dividends — needed for backtests
- Path: `/v3/reference/dividends`
- Returns: ex-dividend date, pay date, cash amount, frequency
- Free tier: yes
- Effort: small (~half day) — mirror splits
- Use case: total-return calculations, accurate P&L

### 4. Ticker reference / details
- Path: `/v3/reference/tickers/{ticker}` (single) or `/v3/reference/tickers` (list)
- Returns: company name, market cap, primary exchange, SIC code, market status
- Free tier: yes
- Effort: small — populate a sidebar on AlgorithmDetail / a future symbol-detail page
- Use case: UI enrichment, instrument metadata

### 5. Previous day's bar — already covered by aggregates, skip
- Aggregates with `from=yesterday, to=yesterday` gives the same thing

### 6. Snapshot (last quote/trade)
- Path: `/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}`
- Returns: most recent quote + trade + minute bar + day bar
- Free tier: ⚠️ partially — last quote/trade restricted in some configurations
- Effort: medium — confirm coverage before implementing

## Tier-locked (skip until plan upgrade)

| Endpoint | Path | Why blocked |
|---|---|---|
| Trades | `/v3/trades/{ticker}` | Tick-level trade data is paid |
| Quotes | `/v3/quotes/{ticker}` | NBBO quotes are paid |
| Real-time WebSocket | `wss://socket.polygon.io/...` | Real-time streaming is paid |
| Options chain snapshots | `/v3/snapshot/options/{underlying}` | Options data is paid |

## Notes on what we shipped
- We expose `quotes` and `trades` as **disabled checkboxes** in the download dialog with a "(not yet supported)" label. Backend rejects them with a clear error if anyone bypasses the UI. Re-enable when (a) the user upgrades to a paid plan AND (b) we implement `fetch_quotes`/`fetch_trades` on `PolygonProvider`.
- The 13-second proactive pacing accommodates the 5-per-minute limit with margin. If the user upgrades, drop `min_request_interval_s` accordingly in `coordinator/main.py` (or wire it through a setting — TODO).
- Polygon's free-tier API responses don't include the user's plan or daily usage. We can't surface "you've used 4/5 calls in the last minute" without a separate tracking layer.

## Additional research notes

### Pagination behavior (observed)
- Observed during a real 2yr SPY 1hour download: even though our code requests `limit=50000`, the free tier paginates earlier on long ranges and returns many `next_url` pages of ~90-100 bars each.
- At 13s/page this is the dominant wall-clock cost on long backfills. Worth communicating to users that long downloads are slow on free.

### Pre/post-market bars
- The aggregates endpoint returns pre- and post-market hours by default for intraday spans.
- 1hour bars for a year exceed the "6.5 × 252" textbook count substantially — expect ~3500-4000 bars/yr, not 1638.

### Dropped fields worth adding
- The `vw` (VWAP) and `n` (trade count) fields on aggregates are useful and currently dropped by our provider. Cheap schema addition when needed.

### Domain migration
- polygon.io docs appear to be migrating to `massive.com`. The actual API host (`api.polygon.io`) still serves requests — our provider does not need changes. Worth monitoring.
