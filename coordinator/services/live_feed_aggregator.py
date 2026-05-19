"""Live broker WebSocket -> tick parquet + 1-minute bar parquet.

Per Spec B §3, each ``LiveSubscription(account_id, symbol)`` row maintains
per-symbol state.  Stream connections are keyed on ``(account_id,
asset_class)``: one connection per account+asset_class pair carries all
subscribed symbols up to the broker cap (``_MAX_SYMBOLS_PER_STREAM``).
Symbols are added/removed from the connection's subscribe set as
subscriptions come and go.

Each symbol still gets its own asyncio flush-task that:

1. Buffers trades + quotes in memory and flushes them to per-day parquet
   files under ``data/market/{broker}_live/{symbol}/ticks/`` every 5
   seconds.
2. Aggregates trades into a 1-minute OHLCV bar. When the wall clock
   crosses a minute boundary, the closed bar is flushed via
   ``DataService.save_market_data`` to
   ``data/market/{broker}_live/{symbol}/1min.parquet``.
3. Updates the row's ``tick_rate_per_min`` and ``last_tick_at`` once a
   minute. A retention sweep deletes day-partitioned tick files older
   than the row's ``tick_retention_hours``.

If the account has no credentials configured, ``start_subscription`` logs
the situation and creates an idle flush-task — it does not crash the
lifespan. Tests inject a fake adapter via ``_adapter_for_account``
(monkeypatched) or ``adapter_factory`` (constructor arg).
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Deque, Optional

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from coordinator.database.models import Account, LiveSubscription
from coordinator.services.data_service import DataService
from coordinator.services.encryption import EncryptionService

logger = logging.getLogger(__name__)


# ``adapter_factory`` matches the (broker_type, environment, credentials) signature
# of ``worker.adapter_factory.make_broker_adapter``. The aggregator takes it as a
# constructor arg so tests can inject a fake adapter without monkey-patching.
AdapterFactory = Callable[[str, str, dict], object]


# Wall-clock provider so tests can inject a fast-forwarded clock.
NowFn = Callable[[], datetime]


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _BarBuilder:
    """Builds a 1-minute OHLCV bar from streaming trades."""
    minute_start: Optional[datetime] = None
    open_: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: float = 0.0

    def add(self, ts: datetime, price: float, size: float) -> None:
        minute = ts.replace(second=0, microsecond=0)
        if self.minute_start is None:
            self.minute_start = minute
        if minute != self.minute_start:
            # Caller should have called flush_if_needed before us; this
            # branch defends against out-of-order ticks.
            self.minute_start = minute
            self.open_ = price
            self.high = price
            self.low = price
            self.close = price
            self.volume = size
            return
        if self.open_ is None:
            self.open_ = price
            self.high = price
            self.low = price
        else:
            self.high = max(self.high, price)
            self.low = min(self.low, price)
        self.close = price
        self.volume += size

    def take_closed(self, now_minute: datetime) -> Optional[dict]:
        """If a bar exists for a strictly-earlier minute, return its row.

        Returns None for ghost bars (vol==0 AND high==low) — these come from
        quote-only events with no actual trades and pollute the data view.
        """
        if self.minute_start is None or self.minute_start >= now_minute:
            return None
        is_ghost = (
            (self.volume or 0) == 0
            and self.high is not None
            and self.low is not None
            and self.high == self.low
        )
        if is_ghost:
            # Reset state but emit nothing.
            self.minute_start = None
            self.open_ = None
            self.high = None
            self.low = None
            self.close = None
            self.volume = 0.0
            return None
        row = {
            "timestamp": self.minute_start,
            "open": self.open_,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }
        self.minute_start = None
        self.open_ = None
        self.high = None
        self.low = None
        self.close = None
        self.volume = 0.0
        return row


@dataclass
class _SubState:
    trades: Deque[dict] = field(default_factory=deque)
    quotes: Deque[dict] = field(default_factory=deque)
    bar: _BarBuilder = field(default_factory=_BarBuilder)
    pending_bars: Deque[dict] = field(default_factory=deque)
    tick_count_window: Deque[datetime] = field(default_factory=deque)
    handle: Optional[object] = None  # kept for legacy compat; stream is on _StreamConn
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_tick_at: Optional[datetime] = None


@dataclass
class _StreamConn:
    """One broker stream connection that can carry many symbols."""
    handle: Optional[object] = None
    symbols: set = field(default_factory=set)


# Broker cap: max symbols per (broker, asset_class) stream.
_MAX_SYMBOLS_PER_STREAM: dict[tuple[str, str], int] = {
    ("alpaca", "equities"): 30,
    ("alpaca", "crypto"): 30,
    ("tradier", "equities"): 100,
}


class LiveFeedAggregator:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        encryption: EncryptionService,
        data_service: Optional[DataService] = None,
        adapter_factory: Optional[AdapterFactory] = None,
        market_dir: str = "data/market",
        flush_interval_s: float = 5.0,
        now_fn: NowFn = _default_now,
    ) -> None:
        self._sf = session_factory
        self._data_service = data_service or DataService(
            market_data_dir=market_dir, custom_data_dir="data/custom"
        )
        self._market_dir = market_dir
        self._encryption = encryption
        self._flush_interval = flush_interval_s
        self._now = now_fn
        self._adapter_factory = adapter_factory or self._default_adapter_factory
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._states: dict[tuple[str, str], _SubState] = {}
        # Stream connections keyed on (account_id, asset_class).
        self._streams: dict[tuple[str, str], _StreamConn] = {}
        self._retention_task: Optional[asyncio.Task] = None
        self._sweep_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._bar_subscribers: dict[tuple[str, str, str], set[Callable]] = {}
        self._event_subscribers: dict[tuple[str, str], set[Callable]] = {}
        self._coord_worker_id: Optional[str] = None

    @staticmethod
    def _default_adapter_factory(broker_type: str, environment: str, credentials: dict):
        from worker.adapter_factory import make_broker_adapter

        return make_broker_adapter(broker_type, environment, credentials)

    # ------- subscriber API -------
    def subscribe_bars(self, broker: str, symbol: str, timeframe: str, callback: Callable) -> None:
        self._bar_subscribers.setdefault((broker, symbol, timeframe), set()).add(callback)

    def unsubscribe_bars(self, broker: str, symbol: str, timeframe: str, callback: Callable) -> None:
        s = self._bar_subscribers.get((broker, symbol, timeframe))
        if s:
            s.discard(callback)
            if not s:
                self._bar_subscribers.pop((broker, symbol, timeframe), None)

    def subscribe_events(self, broker: str, symbol: str, callback: Callable) -> None:
        self._event_subscribers.setdefault((broker, symbol), set()).add(callback)

    def unsubscribe_events(self, broker: str, symbol: str, callback: Callable) -> None:
        s = self._event_subscribers.get((broker, symbol))
        if s:
            s.discard(callback)
            if not s:
                self._event_subscribers.pop((broker, symbol), None)

    async def _dispatch_bar(self, broker: str, symbol: str, timeframe: str, bar: dict) -> None:
        for cb in list(self._bar_subscribers.get((broker, symbol, timeframe), ())):
            try:
                await cb(bar)
            except Exception:
                logger.exception("Bar subscriber failed for %s/%s/%s", broker, symbol, timeframe)

    async def _dispatch_event(self, broker: str, symbol: str, event: dict) -> None:
        for cb in list(self._event_subscribers.get((broker, symbol), ())):
            try:
                await cb(event)
            except Exception:
                logger.exception("Event subscriber failed for %s/%s", broker, symbol)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        # Resume any rows already marked as running.
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(LiveSubscription).where(LiveSubscription.status == "running")
                )
            ).scalars().all()
        for r in rows:
            # Route to provider-based path when account_id is None.
            if r.account_id is None and r.provider_type:
                await self.start_subscription(None, r.broker, r.symbol, r.asset_class)
            else:
                await self.start_subscription(r.account_id, r.broker, r.symbol, r.asset_class)
        self._retention_task = asyncio.create_task(self._retention_loop())
        self._sweep_task = asyncio.create_task(self._stale_stream_sweep())

    async def stop(self) -> None:
        if self._sweep_task:
            self._sweep_task.cancel()
            try:
                await self._sweep_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._retention_task:
            self._retention_task.cancel()
            try:
                await self._retention_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for key, t in list(self._tasks.items()):
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # Close all shared stream connections.
        for conn in list(self._streams.values()):
            if conn.handle is not None:
                close = getattr(conn.handle, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:  # noqa: BLE001
                        pass
        self._tasks.clear()
        self._states.clear()
        self._streams.clear()

    async def start_subscription(
        self, account_id: Optional[str], broker: str, symbol: str, asset_class: str = "equities",
    ) -> None:
        """Ensure a stream exists for (account_id|provider, asset_class), then add the
        symbol. Per-symbol _SubState is keyed on (broker, symbol) since ticks
        come back labeled by symbol regardless of which account's connection
        they arrived on.

        When account_id is None, broker is the provider_type ("polygon"|"thetadata")
        and credentials come from Settings via _adapter_for_provider.
        """
        # Stream key: provider-based subs use "provider:<broker>" to avoid
        # collision with account-based streams.
        if account_id is None:
            stream_key = (f"provider:{broker}", asset_class)
        else:
            stream_key = (account_id, asset_class)
        state_key = (broker, symbol)

        if state_key not in self._states:
            self._states[state_key] = _SubState()

        conn = self._streams.get(stream_key)
        if conn is None:
            if account_id is None:
                adapter = await self._adapter_for_provider(broker)
                error_label = f"provider:{broker}"
            else:
                adapter = await self._adapter_for_account(account_id)
                error_label = f"account:{account_id}"
            if adapter is None:
                logger.warning(
                    "No adapter for %s; aggregator idles for %s/%s",
                    error_label, broker, symbol,
                )
                if self._sf is not None:
                    await self._mark_subscription_error(
                        account_id, symbol,
                        f"No adapter for {error_label}",
                    )
            else:
                conn = _StreamConn()
                try:
                    conn.handle = adapter.start_market_data_stream(
                        [symbol], self._make_on_trade(broker),
                        self._make_on_quote(broker), asset_class=asset_class,
                    )
                    conn.symbols.add(symbol)
                    self._streams[stream_key] = conn
                except NotImplementedError:
                    logger.warning(
                        "Adapter for %s does not implement start_market_data_stream",
                        broker,
                    )
                except Exception:
                    logger.exception("Failed to start stream for %s/%s/%s",
                                     error_label, broker, symbol)
        else:
            # Reuse existing stream — multi-symbol packing.
            cap = _MAX_SYMBOLS_PER_STREAM.get((broker, asset_class), 30)
            if len(conn.symbols) >= cap:
                raise RuntimeError(
                    f"broker stream cap reached for {broker}/{asset_class}: {cap} symbols"
                )
            if symbol not in conn.symbols:
                add = getattr(conn.handle, "add_symbols", None)
                if callable(add):
                    add([symbol])
                    conn.symbols.add(symbol)
                elif conn.handle is not None:
                    old_handle = conn.handle
                    if account_id is None:
                        adapter = await self._adapter_for_provider(broker)
                    else:
                        adapter = await self._adapter_for_account(account_id)
                    new_handle = None
                    try:
                        new_handle = adapter.start_market_data_stream(
                            list(conn.symbols | {symbol}),
                            self._make_on_trade(broker),
                            self._make_on_quote(broker),
                            asset_class=asset_class,
                        )
                    except Exception:
                        logger.exception("Failed to restart stream for %s/%s", broker, symbol)
                        return
                    conn.handle = new_handle
                    conn.symbols.add(symbol)
                    try:
                        old_handle.close()
                    except Exception:
                        pass

        # Per-symbol flush task as before.
        self._tasks[state_key] = asyncio.create_task(self._run(broker, symbol, asset_class))

    async def stop_subscription(self, account_id: Optional[str], symbol: str) -> None:
        """Remove this symbol from its stream subscribe set.

        When account_id is None, matches against provider-keyed streams
        (stream_key starts with "provider:").
        """
        # Find broker via state_key scan (state is keyed by (broker, symbol)).
        state_key = None
        for k in list(self._states.keys()):
            if k[1] == symbol:
                state_key = k
                break
        if state_key is None:
            return
        broker = state_key[0]

        t = self._tasks.pop(state_key, None)
        if t:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._states.pop(state_key, None)

        if account_id is None:
            # Provider-based: match streams whose key starts with "provider:".
            for key in list(self._streams.keys()):
                conn = self._streams[key]
                if not key[0].startswith("provider:") or symbol not in conn.symbols:
                    continue
                conn.symbols.discard(symbol)
                remove = getattr(conn.handle, "remove_symbols", None)
                if callable(remove):
                    remove([symbol])
                if not conn.symbols:
                    try:
                        if conn.handle is not None:
                            conn.handle.close()
                    except Exception:
                        pass
                    del self._streams[key]
                return
        else:
            for key in list(self._streams.keys()):
                conn = self._streams[key]
                if key[0] != account_id or symbol not in conn.symbols:
                    continue
                conn.symbols.discard(symbol)
                remove = getattr(conn.handle, "remove_symbols", None)
                if callable(remove):
                    remove([symbol])
                if not conn.symbols:
                    try:
                        if conn.handle is not None:
                            conn.handle.close()
                    except Exception:
                        pass
                    del self._streams[key]
                return

    async def _adapter_for_account(self, account_id: str) -> Optional[object]:
        """Construct a broker adapter using credentials from a specific account."""
        if self._sf is None:
            return None
        async with self._sf() as session:
            account = (await session.execute(
                select(Account).where(Account.id == account_id)
            )).scalar_one_or_none()
            if account is None:
                return None
            creds = self._decrypt_creds(account)
        try:
            return self._adapter_factory(account.broker_type, account.environment, creds)
        except Exception:
            logger.exception("Failed to construct adapter for account %s", account_id)
            return None

    async def _adapter_for_provider(self, provider_type: str) -> Optional[object]:
        """Construct a data-only adapter using credentials from Settings."""
        if self._sf is None:
            return None
        from coordinator.database.models import Setting
        async with self._sf() as session:
            async def _get(key: str) -> Optional[str]:
                row = (await session.execute(
                    select(Setting).where(Setting.key == key)
                )).scalar_one_or_none()
                if row is None or not row.value:
                    return None
                return self._encryption.decrypt(row.value) if row.encrypted else row.value

            if provider_type == "polygon":
                api_key = await _get("polygon_api_key")
                if api_key is None:
                    logger.warning("polygon_api_key not configured in Settings")
                    return None
                try:
                    from worker.polygon_stream_adapter import PolygonStreamAdapter
                    return PolygonStreamAdapter(api_key=api_key)
                except Exception:
                    logger.exception("Failed to construct PolygonStreamAdapter")
                    return None
            elif provider_type == "thetadata":
                username = await _get("theta_data_username")
                password = await _get("theta_data_password")
                if not username or not password:
                    logger.warning("ThetaData credentials not configured in Settings")
                    return None
                try:
                    from worker.thetadata_stream_adapter import ThetaDataStreamAdapter
                    return ThetaDataStreamAdapter(username=username, password=password)
                except Exception:
                    logger.exception("Failed to construct ThetaDataStreamAdapter")
                    return None
            else:
                logger.warning("Unknown provider_type: %s", provider_type)
                return None

    def _make_on_trade(self, broker: str) -> Callable:
        """Return a trade callback for the given broker.

        The callback dispatches each tick to the correct per-symbol _SubState
        by reading ``tick["symbol"]``.  Callbacks fire on the stream's own
        thread; we take the per-symbol lock for state mutation and bridge back
        into the asyncio loop for event dispatch.
        """
        def _on_trade(tick: dict) -> None:
            symbol = tick.get("symbol")
            state = self._states.get((broker, symbol))
            if state is None:
                return
            with state.lock:
                state.trades.append(tick)
                ts = tick.get("timestamp") or self._now()
                state.tick_count_window.append(ts)
                state.last_tick_at = ts
                price = float(tick.get("price") or 0.0)
                size = float(tick.get("size") or 0.0)
                # If this tick belongs to a new minute, close the previous bar
                # (defer the parquet write to the asyncio task).
                closed = state.bar.take_closed(ts.replace(second=0, microsecond=0))
                if closed is not None:
                    state.pending_bars.append(closed)
                state.bar.add(ts, price, size)
            if self._loop is not None:
                asyncio.run_coroutine_threadsafe(
                    self._dispatch_event(broker, symbol, tick), self._loop
                )
        return _on_trade

    def _make_on_quote(self, broker: str) -> Callable:
        """Return a quote callback for the given broker, dispatching by symbol."""
        def _on_quote(tick: dict) -> None:
            symbol = tick.get("symbol")
            state = self._states.get((broker, symbol))
            if state is None:
                return
            with state.lock:
                state.quotes.append(tick)
        return _on_quote

    # ------- per-symbol flush task -------
    async def _run(self, broker: str, symbol: str, asset_class: str = "equities") -> None:
        """Per-symbol flush / bar-finalizer / rate-update loop.

        Stream opening has moved to ``start_subscription``; this task only
        handles periodic flush and DB writes.
        """
        key = (broker, symbol)
        state = self._states[key]
        logger.info("LiveFeedAggregator flush task starting for %s/%s", broker, symbol)

        last_minute_flushed: Optional[datetime] = None
        last_rate_update: datetime = self._now()

        try:
            while True:
                await asyncio.sleep(self._flush_interval)
                now = self._now()
                # Flush any closed bar that the stream thread couldn't write itself.
                with state.lock:
                    current_minute = now.replace(second=0, microsecond=0)
                    closed = state.bar.take_closed(current_minute)
                if closed is not None:
                    last_minute_flushed = closed["timestamp"]
                    await self._flush_bar(broker, symbol, closed)

                # Flush ticks accumulated since the last flush.
                await self._flush_ticks(broker, symbol, state)

                # Bar rows accumulated via the stream-thread callback.
                with state.lock:
                    pending_bars = list(state.pending_bars)
                    state.pending_bars.clear()
                for bar_row in pending_bars:
                    await self._flush_bar(broker, symbol, bar_row)

                # Once a minute, update tick rate + last_tick_at.
                if (now - last_rate_update) >= timedelta(seconds=60):
                    await self._update_rate(broker, symbol, state, now)
                    last_rate_update = now
        except asyncio.CancelledError:
            pass

    # ------- helpers -------
    def _decrypt_creds(self, account: Account) -> dict:
        try:
            return self._encryption.decrypt_json(account.credentials)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to decrypt account credentials")
            return {}

    async def _mark_subscription_error(
        self, account_id: Optional[str], symbol: str, message: str,
    ) -> None:
        if self._sf is None:
            return
        async with self._sf() as session:
            stmt = select(LiveSubscription).where(
                LiveSubscription.symbol == symbol,
            )
            if account_id is not None:
                stmt = stmt.where(LiveSubscription.account_id == account_id)
            else:
                stmt = stmt.where(LiveSubscription.account_id.is_(None))
            sub = (await session.execute(stmt)).scalar_one_or_none()
            if sub is not None:
                sub.status = "error"
                sub.last_error = message
                await session.commit()

    async def _flush_ticks(
        self, broker: str, symbol: str, state: _SubState
    ) -> None:
        with state.lock:
            trades = list(state.trades)
            quotes = list(state.quotes)
            state.trades.clear()
            state.quotes.clear()
        if trades:
            self._append_parquet(broker, symbol, "trades", trades)
        if quotes:
            self._append_parquet(broker, symbol, "quotes", quotes)

    def _ticks_dir(self, broker: str, symbol: str) -> Path:
        return Path(self._market_dir) / f"{broker}_live" / symbol / "ticks"

    def _append_parquet(
        self, broker: str, symbol: str, kind: str, rows: list[dict]
    ) -> None:
        """Append ``rows`` to data/market/{broker}_live/{symbol}/ticks/{kind}-{day}.parquet.

        Day partitioning is by the first tick's date (UTC). For high-volume
        symbols, ticks arriving across a midnight boundary will land in two
        per-day files — the loop will write each one in its own pass.
        """
        if not rows:
            return
        # Bucket by date.
        by_day: dict[date, list[dict]] = {}
        for r in rows:
            ts = r.get("timestamp")
            if isinstance(ts, datetime):
                d = ts.astimezone(timezone.utc).date()
            else:
                d = self._now().astimezone(timezone.utc).date()
            by_day.setdefault(d, []).append(r)

        ticks_dir = self._ticks_dir(broker, symbol)
        os.makedirs(ticks_dir, exist_ok=True)
        for d, day_rows in by_day.items():
            path = ticks_dir / f"{kind}-{d.isoformat()}.parquet"
            df = pd.DataFrame(day_rows)
            # Normalize timestamps to UTC tz-aware.
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            if path.exists():
                try:
                    existing = pd.read_parquet(path)
                    df = pd.concat([existing, df], ignore_index=True)
                except Exception:  # noqa: BLE001
                    logger.exception("Failed to read existing parquet %s", path)
            try:
                df.to_parquet(path, index=False)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to write parquet %s", path)

    async def _flush_bar(
        self, broker: str, symbol: str, bar_row: dict
    ) -> None:
        df = pd.DataFrame([bar_row])
        provider = f"{broker}_live"
        try:
            self._data_service.save_market_data(provider, symbol, "1min", df)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to save 1min bar for %s/%s", broker, symbol)
        asyncio.create_task(self._dispatch_bar(broker, symbol, "1min", bar_row))

    async def _update_rate(
        self, broker: str, symbol: str, state: _SubState, now: datetime
    ) -> None:
        cutoff = now - timedelta(seconds=60)
        with state.lock:
            # tick_count_window only stores timestamps for rate-tracking.
            while state.tick_count_window and state.tick_count_window[0] < cutoff:
                state.tick_count_window.popleft()
            rate = float(len(state.tick_count_window))
            last_tick = state.last_tick_at
        async with self._sf() as session:
            subs = (
                await session.execute(
                    select(LiveSubscription).where(
                        LiveSubscription.broker == broker,
                        LiveSubscription.symbol == symbol,
                    )
                )
            ).scalars().all()
            for sub in subs:
                sub.tick_rate_per_min = rate
                if last_tick is not None:
                    sub.last_tick_at = last_tick
            await session.commit()

    # ------- stream disconnect / reconnect events -------
    async def _emit_stream_event(
        self,
        broker: str,
        asset_class: str,
        symbols: list[str],
        event_type: str,
        reason: str = "",
    ) -> None:
        """Insert a worker_activity row describing a stream disconnect/reconnect.

        event_type='stream_disconnect' → severity='warn'.
        event_type='stream_reconnect'  → severity='info'.
        """
        if self._sf is None or self._coord_worker_id is None:
            return
        severity = "warn" if event_type == "stream_disconnect" else "info"
        from coordinator.database.models import WorkerActivity
        async with self._sf() as session:
            session.add(WorkerActivity(
                worker_id=self._coord_worker_id,
                kind="event",
                event_type=event_type,
                severity=severity,
                payload={
                    "broker": broker,
                    "asset_class": asset_class,
                    "symbols": list(symbols),
                    "reason": reason,
                },
            ))
            await session.commit()

    async def _stale_stream_sweep(self) -> None:
        """Background task: every 30s, check if any stream has had no tick for
        > 60s during expected hours. If so, emit a stream_disconnect event."""
        while True:
            try:
                await asyncio.sleep(30.0)
            except asyncio.CancelledError:
                return
            now = self._now()
            for key, conn in list(self._streams.items()):
                account_id, asset_class = key
                # Derive broker from state symbols on this connection for event emission.
                # Pick the broker from any state whose symbol is in conn.symbols.
                broker = None
                for (b, sym) in self._states:
                    if sym in conn.symbols:
                        broker = b
                        break
                if broker is None:
                    broker = account_id  # fallback: use account_id as label
                # Pick any state under this connection to read last_tick_at.
                relevant_states = [
                    self._states.get((broker, sym)) for sym in conn.symbols
                ]
                last = max(
                    (s.last_tick_at for s in relevant_states if s and s.last_tick_at),
                    default=None,
                )
                if last is None or now - last > timedelta(seconds=60):
                    # Already-emitted suppression: simple flag on the conn.
                    already = getattr(conn, "_disconnect_emitted", False)
                    if not already:
                        await self._emit_stream_event(
                            broker=broker,
                            asset_class=asset_class,
                            symbols=sorted(conn.symbols),
                            event_type="stream_disconnect",
                            reason=f"no tick for > 60s (last={last})",
                        )
                        conn._disconnect_emitted = True
                else:
                    if getattr(conn, "_disconnect_emitted", False):
                        await self._emit_stream_event(
                            broker=broker,
                            asset_class=asset_class,
                            symbols=sorted(conn.symbols),
                            event_type="stream_reconnect",
                            reason="ticks resumed",
                        )
                        conn._disconnect_emitted = False

    # ------- retention sweep -------
    async def _retention_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(3600)
                await self._sweep_old_ticks()
        except asyncio.CancelledError:
            return

    async def _sweep_old_ticks(self) -> None:
        async with self._sf() as session:
            rows = (
                await session.execute(select(LiveSubscription))
            ).scalars().all()
        for sub in rows:
            ticks_dir = (
                Path(self._market_dir) / f"{sub.broker}_live" / sub.symbol / "ticks"
            )
            if not ticks_dir.exists():
                continue
            cutoff = (
                self._now() - timedelta(hours=sub.tick_retention_hours)
            ).date()
            for f in ticks_dir.glob("*.parquet"):
                try:
                    name = f.stem  # e.g. "trades-2026-05-14"
                    _, datestr = name.split("-", 1)
                    d = date.fromisoformat(datestr)
                    if d < cutoff:
                        f.unlink()
                except (ValueError, OSError):
                    continue
