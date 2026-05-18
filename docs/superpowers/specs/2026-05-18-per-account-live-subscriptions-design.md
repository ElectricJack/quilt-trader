# Per-Account Live Subscriptions — Design

## Problem

The unified-live-subscriptions feature (commit `ce5990c`..) keyed each `LiveSubscription` row on `(broker, symbol)` and resolved which account's credentials to use via a single `Setting(key="live_feed_account.<broker>")` per broker. That works fine for one account per broker but breaks two real cases the user has hit:

1. **Alpaca free-tier WS connection limit (1)**. Equities and crypto each need their own WS, but Alpaca's free tier allows only one concurrent connection per account. With one Alpaca account, you can't run an equities subscription and a crypto subscription at the same time — the second auth handshake gets rejected with `connection limit exceeded`. The user has a second Alpaca account specifically to side-step this.
2. **Ambiguity in the UI** — subscriptions display as `alpaca_live SPY` with no indication of which account is doing the streaming. The consumer list shows `algo: deployment-abc12345` (a hash) rather than the human-readable algorithm name.

Today, even if the user adds a second account to the DB, all subscriptions share the same connection pool keyed on broker. There's no way to route SPY through account A and BTCUSD through account B.

## Goal

- Each `LiveSubscription` is tied to a specific `account_id`. Two subscriptions to the same symbol on two different accounts produce two separate rows + two separate broker WS connections.
- Algorithm manifests declare `assets:` without a `broker` field. The deployment's chosen account determines which broker + credentials the subscription uses.
- The dashboard surfaces the account name (linked) and the algorithm name (linked) instead of broker labels and ID hashes.

## Non-goals

- Cross-account fail-over (if account A's connection drops, don't auto-fall-back to account B). Each subscription is bound to one account for its lifetime; the user has to subscribe again on a different account if they want to migrate.
- Surfacing the Alpaca auth-error properly on stream-thread crash. Fix (A) discussed earlier — separate follow-up (still useful but orthogonal).
- A roadmap for routing the *same* (account_id, asset_class) stream's symbols across multiple connections (sharding). Sub-project 2's multi-symbol packing already covers up to the broker's per-connection cap; sharding is a follow-up if a single account ever needs more than 30 symbols.

## Design

### Algorithm-side contract

Manifest's `assets:` list drops the `broker` field. Each entry is `{symbol, asset_class}`. Example:

```yaml
assets:
  - { symbol: SPY, asset_class: equities }
  - { symbol: BTCUSD, asset_class: crypto }
```

The same algorithm package can deploy on any account that supports the declared asset classes. The deployment's account decides which broker + credentials the subscriptions use.

Backwards-compat: the parser still accepts entries with a `broker:` field but ignores it. The migration of existing `Algorithm.assets` rows strips the field.

### Data model

**`live_subscriptions`** (modified):
- Add `account_id: str NOT NULL` FK to `accounts.id` (`ON DELETE CASCADE` — if the user deletes the account, drop its subscriptions).
- Change unique key from `(broker, symbol)` to `(account_id, symbol)`. Asset_class is determined by `(account_id, symbol)` at create time; same symbol on the same account in two asset classes (e.g. BTCUSD-as-equity vs BTCUSD-as-crypto) is not a v1 concern.
- Keep `broker` column as denormalized convenience (always `= account.broker_type` at insertion time). Useful for grep / debugging; the system never resolves accounts via `broker` again.

**`subscription_consumers`** (no schema change). API response on each consumer is augmented with `algorithm_id` + `algorithm_name` via a join when `consumer_type='algo'`.

**`Algorithm.assets`** JSON content shape changes to `[{symbol, asset_class}]`. Existing rows with `broker` fields are migrated to drop the field.

**`Setting(key="live_feed_account.*")`** rows are deleted (obsolete).

### Lifecycle

- **Manual subscribe** (Data page): the Subscribe form requires an account selection. The account must support the asset_class. Insert `LiveSubscription(account_id, symbol, asset_class, broker=account.broker_type)` + `SubscriptionConsumer(consumer_type='manual')`.
- **Deploy start**: `pre_start_checks` reads `algorithm.assets` (list of `{symbol, asset_class}`) and uses `instance.account_id` for each entry. Upserts the per-account `LiveSubscription`, inserts an `algo` consumer row.
- **Deploy stop / delete**: unchanged structurally — deletes the algo consumer rows; auto-deletes the subscription if no consumers remain.
- **Account compatibility**: when an algorithm declares `asset_class: crypto` and the deployment's account doesn't have `crypto` in `supported_asset_types`, fail the deploy at `pre_start_checks` with a 422.

### Streaming

`LiveFeedAggregator` opens one stream per `(account_id, asset_class)` instead of `(broker, asset_class)`. The credentials come from the account row directly — no Setting lookup.

When `start_subscription(account_id, symbol, asset_class)` is called:
- If a stream already exists for `(account_id, asset_class)`, add the symbol to its subscribe set (multi-symbol packing from sub-project 2 still applies).
- Else open a new connection using `account.credentials`.

`MAX_SYMBOLS_PER_STREAM` stays keyed on `(broker, asset_class)` — the cap is per Alpaca account-class endpoint, not per-account-id.

### API surface

**Modified endpoints:**
- `POST /api/live-subscriptions` body: `{account_id, symbol, asset_class, tick_retention_hours?}`. The `broker` field is removed from the request body — derived from `account.broker_type`.
- `GET /api/live-subscriptions` response: each row gains `account_id` + `account_name`. Each consumer gains `algorithm_id` + `algorithm_name` when `consumer_type='algo'`.

**No change**: `unsubscribe`, `delete`, `estimate`, `patch`.

### Frontend

`LiveSubscriptionsSection`:
- Row label: `<a href="/accounts/{account_id}">{account_name}</a>` instead of `{broker}_live`.
- The `{asset_class}` and `{symbol}` badges stay as-is.
- Consumer list: each `algo` consumer renders as `<a href="/algorithms/{algorithm_id}">{algorithm_name}</a>`. Manual consumer stays as plain `manual`.

Subscribe form:
- Replace the `Broker` selector with an `Account` selector populated from `useAccounts()`. The asset_class options filter to those supported by the picked account.

### Migration (one Alembic revision)

1. Add `account_id: str` column to `live_subscriptions` (nullable initially).
2. Backfill: for each existing row, set `account_id` from `Setting(key="live_feed_account.<broker>").value` if present, else from the first `Account` whose `broker_type` matches. If neither exists → delete the row (no account, no subscription).
3. Make `account_id` NOT NULL with FK to `accounts.id` ON DELETE CASCADE.
4. Drop unique constraint `(broker, symbol)`, add unique constraint `(account_id, symbol)`.
5. Delete all `Setting` rows with `key LIKE 'live_feed_account.%'`.
6. For each `Algorithm.assets` row in JSON: drop the `broker` field from each entry.

The simple-ma-crossover local manifest under `data/packages/quilt-trader-test-algo/quilt.yaml` is updated to remove `broker:` from its asset entry.

## Tests

**Backend:**
- POST with `account_id` creates the row + manual consumer; account_name appears in response.
- Two subscriptions to the same symbol on two different Alpaca accounts → two separate `LiveSubscription` rows + two stream connections opened.
- Algorithm deploy on account A and the same algorithm on account B → two separate algo consumer rows, each pointing at its account's subscription.
- Asset class incompatibility (algorithm needs crypto, account doesn't support it) → 422 at deploy.
- API response includes `algorithm_name` + `algorithm_id` on each `algo` consumer.
- Migration: dev DB has one subscription with `live_feed_account.alpaca` setting → after migration, subscription has `account_id` set and the setting is gone.

**Frontend:**
- Row header renders account name as a link to `/accounts/<id>`.
- Consumer list renders algo name as a link to `/algorithms/<id>`.
- Subscribe form lists accounts (not brokers); asset_class options filter to the account's `supported_asset_types`.

**Manual smoke:**
- Add a second Alpaca account in the dashboard. Subscribe SPY equities on account A, BTCUSD crypto on account B. Within ~10s both subscriptions show ticks (no `connection limit exceeded`).
- Deploy simple-ma-crossover on account A and on account B simultaneously. Both deployments appear as `algo` consumers on the respective subscriptions; algorithm name is shown linked.

## Out-of-scope follow-ups (for backlog)

- Cross-account fail-over (auto-migrate symbol to a different account when its stream errors).
- Multi-asset-class on the same account_id sharing a single WS (Alpaca's protocol forces separate endpoints, so this needs middleware).
- `Algorithm.assets` JSON Schema validation at install time (lifted from the existing backlog item — should be addressed alongside this work since the shape is changing).
- Surface stream auth errors on the subscription row + emit `stream_disconnect` immediately on auth failure (fix (A) — still pending, orthogonal to this).
