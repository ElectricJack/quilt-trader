# Unified Live Subscriptions + Algorithm Data Flow — Design

## Problem

The live-data subscription system today has structural gaps that surface as a cluster of related symptoms:

- Algorithms and the dashboard's "Subscribe" UI take two different paths to the same end. The algorithm path uses `data_dependencies` parsed by `coordinator/services/lifecycle.py`; the manual path uses `LiveSubscription` rows created in `coordinator/api/routes/live_subscriptions.py`. The two paths increment a shared `LiveSubscription.dependent_count` column but never converge on a single registry. When state drifts between them, the count gets stuck — the user observed two subs with `dependent_count=1` from a manual subscribe and no way to see what was holding the reference.
- Subscriptions track only a `dependent_count: int`, never the *identity* of consumers. When delete is blocked with "1 active dependent", the user can't tell what to stop.
- Deploying an algorithm doesn't auto-create the subscriptions it needs. A crypto algo deployed cleanly, emitted `instance_started`, and then did nothing because no live feed was attached.
- Subscriptions only really work for SPY-shaped equity symbols. Crypto has a different WS endpoint on Alpaca (`v1beta3/crypto/us`) and the current code doesn't route to it.
- One WS connection per symbol when Alpaca's free tier allows up to 30 symbols on a single connection — wasteful and limits scale.
- All bars share one retention policy (`tick_retention_hours`), which conflates "ticks I want to keep for short-term debugging" with "minute bars I want to keep forever for backtesting". Long-term history can't be preserved without also keeping every tick forever.
- The user has to pick a timeframe when subscribing. There's no good reason — for any asset under subscription, ticks + 1-min bars are the canonical inputs; everything else is derivable.
- Stream disconnects are invisible in the UI. The Tradier stream silently dying for two hours produced no surface signal — the user only noticed when bars stopped landing.
- Quote-only events get bucketed as bars with `vol=0` and `OHLC` all-equal, polluting the data view with non-trades that look like real bars.

## Goal

A single subscription registry that:
- Is the only place subscriptions live, whether they were created by a manual UI click or auto-created from an algorithm's manifest.
- Tracks consumer identity (not just count) and surfaces it in the UI.
- Auto-creates subscriptions when an algorithm deploys and releases them when it stops/deletes, while keeping manual subscriptions independent.
- Multiplexes many assets onto a single broker WS connection up to the broker's published cap, opening a second connection only when the cap is reached.
- Routes per asset class to the right broker stream endpoint (Alpaca crypto and equities are different).
- Retains 1-minute bars forever and ticks for a configurable bounded window.
- Always pulls the highest practical frequency (ticks + 1-min bars) — algorithms request *which* assets they need, never *what timeframe*.
- Surfaces stream disconnects + per-subscription freshness in the UI.

## Non-goals

- Eager precomputation of 5m/15m/1h/1d bars. Lazy aggregation on read from 1m is the default; precompute is a follow-up if read latency becomes a real problem.
- Options chain bulk subscription. v1 treats each option contract as a separate symbol. Subscribing to a whole chain expansion is a follow-up.
- Replacing the existing tick-storage parquet format. Keep the file layout; just change retention semantics.
- Sub-second / nanosecond tick resolution. Stick with whatever the broker provides natively (typically millisecond).
- A runtime "subscribe dynamically from inside the algorithm" API. All subscriptions come from the manifest.
- Migrating data already on disk to a new layout. Existing files stay where they are; the new layout applies to new writes only.

## Design

### Algorithm-side contract

Algorithms declare data needs in `quilt.yaml` via a structured list, replacing the current `data_dependencies` field:

```yaml
name: simple-ma-crossover
entry_point: algorithm.py
class_name: SimpleMaCrossover
assets:
  - { broker: alpaca, symbol: SPY, asset_class: equities }
  - { broker: alpaca, symbol: BTCUSD, asset_class: crypto }
```

Each entry produces exactly one `LiveSubscription`. There is no per-asset timeframe field — the system always pulls ticks + 1-min bars; aggregation to higher timeframes happens at read time.

Optional per-asset overrides (none required for v1 but the slot exists for the dial):

```yaml
assets:
  - broker: alpaca
    symbol: BTCUSD
    asset_class: crypto
    tick_retention_hours: 720   # 30 days, override the default
```

Within the algorithm code, there is no `subscribe()` API. The algo's `on_bar` / `on_tick` handlers receive data for the assets it declared, keyed by symbol.

### Data model

**`live_subscriptions`** (existing table, modified):
- Drop `dependent_count` (replaced by COUNT() over the new consumers table).
- Add `asset_class: str` (NOT NULL, default `equities`).
- Keep `tick_retention_hours: int` — controls only ticks now, not bars. Default 168 (1 week).
- Bars are retained forever; no column.

**`subscription_consumers`** (new table):
```
id              PK
subscription_id FK -> live_subscriptions.id (ON DELETE CASCADE)
consumer_type   str NOT NULL  -- 'manual' | 'algo'
consumer_id     str NULL      -- NULL for 'manual'; deployment_id for 'algo'
created_at      datetime
```
Unique index on `(subscription_id, consumer_type, consumer_id)` so a given consumer can hold at most one row per subscription (idempotent ref counting).

**`algorithms`** table:
- Rename `data_dependencies` → `assets`, reshape JSON content per the new manifest format (see Migration).

### Lifecycle

**Manual subscribe** (Data page → "Subscribe" button):
- Insert `LiveSubscription` if no row exists for `(broker, symbol)`.
- Insert `subscription_consumers(subscription_id, consumer_type='manual', consumer_id=NULL)`.
- Start the broker stream for `(broker, asset_class)` if not already running; add this symbol to its subscribe set.

**Manual unsubscribe** (Data page → "Unsubscribe" button):
- Delete the `manual` consumer row.
- If no consumers remain on the subscription, drop the symbol from the broker stream's subscribe set. Stop the stream connection only if no symbols remain on it. Delete the `LiveSubscription` row.
- If consumers remain, leave the subscription alive and show in the UI "still held by N algorithm consumer(s)".

**Deploy start** (algorithm instance transitions to `running`):
- For each entry in the algorithm's `assets`:
  - Upsert the `LiveSubscription` row for `(broker, symbol)` — create if missing, including `asset_class` and `tick_retention_hours` from the manifest entry.
  - Insert (or no-op if exists) a `subscription_consumers(consumer_type='algo', consumer_id=deployment_id)` row.
  - Ensure the broker stream is running for `(broker, asset_class)` and the symbol is on its subscribe set.
- If any step fails (e.g., broker cap exceeded with no room on a second connection), the deploy start fails atomically — no partial subscription state.

**Deploy stop / delete**:
- Delete all `subscription_consumers` rows where `consumer_type='algo' AND consumer_id=deployment_id`.
- For each affected subscription, after the delete, count remaining consumers. If zero, drop the symbol from the broker stream's subscribe set and delete the `LiveSubscription` row.

**Symmetric auto-delete rule**: when consumer count on a subscription hits zero, the row is deleted (and the symbol is removed from the broker stream). A manual consumer row holds the subscription alive against algo lifecycle and vice versa. The user can't end up with a subscription they didn't ask for — if they never subscribed manually, their consumer row was never there, and the row goes away when the last algo using it stops.

### Streaming

**One connection per `(broker, asset_class)`**, multiplexing symbols up to a broker-defined cap. Broker adapters expose `MAX_SYMBOLS_PER_STREAM: int` so the connection manager knows the cap. For Alpaca free tier this is 30. For Tradier the cap is whatever they document; if unspecified, default to a conservative 100.

When a subscription is added:
- If a stream for `(broker, asset_class)` exists with room under the cap, send a `subscribe` message extending its symbol set.
- Else open a second connection. Track each connection's symbol set independently.

When a subscription is dropped:
- Send an `unsubscribe` message to the connection holding that symbol.
- If a connection ends up holding zero symbols, close it.

**Crypto routing**: the Alpaca adapter's `start_market_data_stream` chooses endpoint based on asset_class:
- `equities` → `wss://stream.data.alpaca.markets/v2/iex` (or whichever tier is configured)
- `crypto` → `wss://stream.data.alpaca.markets/v1beta3/crypto/us`

Tradier doesn't trade crypto; a `crypto` asset_class on Tradier is a manifest error caught at deploy time.

### Storage and aggregation

**File layout per asset** (extends the current layout):
```
data/live/{broker}/{symbol}/
  ticks/
    2026-05-18.parquet       # one file per day, deleted after tick_retention_hours
    2026-05-19.parquet
  1min.parquet               # append-only, forever
```

The 1min parquet is a single file partitioned internally by day for read efficiency. As new bars are emitted by the aggregator, they get appended.

**Aggregation is lazy**: when an algorithm or the UI requests a 5m/15m/1h/1d bar series, the read path is:
1. Read the relevant slice of the 1m parquet (already partitioned by day so this is cheap).
2. Group by the target interval (`floor(timestamp, '5min')` etc.) — open / max / min / close / sum-volume per group.
3. Return the result.

Parquet group-by on a few months of 1-min data is fast enough (sub-100ms typical for one symbol). If reads exceed an acceptable latency at our scale, a precompute layer can be added later without changing the source-of-truth file.

**Ghost-bar policy**: the aggregator's "emit a bar" step skips bars where `volume == 0 AND high == low`. These are quote-only or no-activity buckets and convey no real signal. Bars where actual trades happened (volume > 0) are emitted normally regardless of whether OHLC are equal. This rule applies at the aggregator (write side) — once a bar is in `1min.parquet` it's trusted.

### Observability

**Stream disconnect/reconnect** (extends the Tradier reconnect work already shipped in `6c0f92c`):
- When a stream connection drops, emit `worker_activity` with `event_type='stream_disconnect'`, severity=`warn`, payload `{ broker, asset_class, symbols, reason }`.
- On successful reconnect, emit `event_type='stream_reconnect'`, severity=`info`, payload `{ broker, asset_class, symbols, downtime_seconds }`.
- Both events surface in the activity stream and in the live-subscriptions page header.

**"Last tick at" column** on `/data` (live-subscriptions section):
- Each row gets `last_tick_at` (already exists on the model — surface it on the UI).
- A row hasn't received a tick in > 60s during market hours → red-tinted status badge.
- Outside market hours, no warning (expected silence).

**Consumer list** on each row:
- Replace the "Dependents: N" number with an expandable list:
  - `manual (you)` — if a manual consumer exists.
  - `algo: simple-ma-crossover (deployment abc-123)` — one row per algo consumer, linkable to the deployment detail page.

### Migration (one Alembic revision)

1. Add `asset_class` column to `live_subscriptions` (default `equities`).
2. Create `subscription_consumers` table.
3. For each existing `live_subscriptions` row with `dependent_count >= 1`, insert a `subscription_consumers` row with `consumer_type='manual', consumer_id=NULL` (existing rows are all user-initiated under today's behavior).
4. Drop `dependent_count` column from `live_subscriptions`.
5. Rename `algorithms.data_dependencies` → `algorithms.assets`. Parse existing rows: every entry in the old `broker_live` list becomes `{ broker, symbol, asset_class: 'equities' }` (existing algos in this repo are equities-only).
6. Migrate the simple-ma-crossover manifest (`packages/simple-ma-crossover/quilt.yaml`) to the new `assets:` format.

The migration is forward-only — no rollback path. The data shape is convergent (single registry); rolling back would require regenerating the dropped column from the new table, which is straightforward but not implemented for v1.

### API surface changes

- `GET /api/live-subscriptions` — response items grow a `consumers: [{type, id, name}]` array. `dependent_count` field removed.
- `POST /api/live-subscriptions` — accepts `asset_class` (required). `tick_retention_hours` stays.
- `POST /api/live-subscriptions/{id}/unsubscribe` — deletes the `manual` consumer row (instead of decrementing a count). Auto-deletes the subscription if no consumers remain AND `created_by='manual'`.
- `DELETE /api/live-subscriptions/{id}` — removes the row outright; still refuses when any consumer remains (now: any row in `subscription_consumers`). Force-delete is not a thing in v1.
- New `GET /api/deployments/{id}/subscriptions` — returns the subscriptions a deployment currently holds, for the deployment-detail UI.

## Tests

**Backend (new tests in `tests/coordinator/`):**
- Deploying an algorithm with two assets in its manifest creates two `LiveSubscription` rows + two `subscription_consumers` rows.
- Stopping a deployment removes those algo consumer rows; subscription is auto-deleted iff `created_by='algo' AND consumer_count==0`.
- A manual subscribe + algo deploy on the same symbol produces one subscription with two consumer rows. Manual unsubscribe leaves the subscription alive with one (algo) consumer.
- Delete subscription with consumers returns 409 with the consumer list in the response body.
- Multiplexing: opening N subscriptions on the same `(broker, asset_class)` keeps the broker stream count at 1 until cap; opens a second at cap+1.
- Crypto subscription routes to the crypto stream endpoint, not the equity endpoint.
- Bars where `vol==0 AND high==low` are skipped at the aggregator; the 1min parquet does not contain them.
- 1m → 5m read path returns expected aggregated values for a known 1m fixture.
- Tick retention sweeper deletes tick parquet files older than `tick_retention_hours`; never deletes `1min.parquet`.

**Frontend (new tests in `dashboard/src/components/`):**
- The live-subscriptions row shows a consumer list (`manual (you)` + `algo: <name> (<deployment-id>)`).
- The "last tick at" column shows a red badge when stale during market hours, normal outside.
- The Unsubscribe / Delete button text + behavior matches the new contract (unsubscribe removes manual ref; delete only allowed when no consumers).

**Manual smoke test:**
- Redeploy simple-ma-crossover. Confirm one `LiveSubscription` row + one `subscription_consumers` row appear automatically.
- Deploy a crypto algo declaring BTCUSD. Confirm the Alpaca crypto stream connects to the crypto WS endpoint and bars start landing.
- Force-disconnect a stream (e.g., revoke API key briefly). Confirm `stream_disconnect` event appears in the activity stream and reconnect happens within backoff window.
- Verify the SPY 1-min parquet has no `vol==0 AND OHLC-all-equal` rows after a fresh stream session.

## Out-of-scope follow-ups

Tracked in `docs/superpowers/backlog.md`:
- Eager precompute of 5m/15m/1h/1d bars (if lazy read latency ever becomes a real problem).
- Options chain bulk subscription (one manifest entry → all contracts under an expiry).
- Force-delete a subscription (admin override for stuck consumer rows).
- Migrating existing tick parquet files to the new daily-partitioned layout (current files stay where they are; new layout applies to new writes).
- Per-tier broker cap discovery (Alpaca's actual cap depends on subscription level; v1 hardcodes 30 for the free tier and is conservative).
