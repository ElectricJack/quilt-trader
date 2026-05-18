# Data-Provider Accounts (Polygon + ThetaData) — Design

## Problem

Live data streaming only works through broker accounts (Alpaca, Tradier). The user wants higher-quality BTC data than Alpaca's thin free-tier feed. Polygon and ThetaData are already configured for historical data but have no live-streaming path.

## Goal

Add Polygon and ThetaData as "data-only" account types. They appear in the Subscribe dropdown alongside broker accounts but cannot be used as deployment targets (no trading). The aggregator opens streams through their respective protocols.

## Design

### Account model

New column: `can_trade: bool = True`. Data-only providers set this to `False`. The deploy flow checks `account.can_trade` and refuses algorithms on data-only accounts.

### Polygon stream adapter

WebSocket connection to `wss://socket.polygon.io/crypto` (crypto) or `wss://socket.polygon.io/stocks` (equities). Auth via `{"action":"auth","params":"<api_key>"}`. Subscribe: `{"action":"subscribe","params":"XT.<symbol>"}` for trades, `XQ.<symbol>` for quotes. Crypto symbols use `X:BTCUSD` format on Polygon.

**Requires a paid Polygon plan** — free tier blocks real-time WS. The adapter handles auth rejection gracefully with a clear error message on the LiveSubscription row.

### ThetaData adapter

ThetaData's live streaming requires their Terminal application running locally. For v1, implement a REST-polling adapter that fetches the latest bars from their historical endpoint every 60 seconds. Not true tick-level streaming, but provides fresher data than manual downloads. True streaming via Terminal TCP is a follow-up.

### Adapter factory

`worker/adapter_factory.py` gains entries for `polygon` and `thetadata`. Both return adapters that implement `start_market_data_stream` but raise `NotImplementedError` on `submit_order`, `get_positions`, `get_account_info`.

### UI

- Account creation form: when `broker_type` is `polygon` or `thetadata`, auto-set `can_trade=False`.
- Subscribe form: data-only accounts appear in the dropdown.
- Deploy form: data-only accounts are hidden from the account selector.

## Non-goals

- ThetaData Terminal TCP streaming (follow-up).
- Polygon options streaming.
- Multi-exchange crypto aggregation.
