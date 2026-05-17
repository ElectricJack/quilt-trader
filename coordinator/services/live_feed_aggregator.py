"""Live broker WebSocket -> tick parquet + 1-minute bar parquet.

Per Spec B §3, each ``LiveSubscription(broker, symbol)`` row runs an
``asyncio`` task that:

1. Resolves the account whose credentials power that broker's live feed
   (from the ``live_feed_account.{broker}`` setting), constructs the
   matching ``BrokerAdapter``, and opens a market-data stream via the
   adapter's ``start_market_data_stream`` method.
2. Buffers trades + quotes in memory and flushes them to per-day parquet
   files under ``data/market/{broker}_live/{symbol}/ticks/`` every 5
   seconds.
3. Aggregates trades into a 1-minute OHLCV bar. When the wall clock
   crosses a minute boundary, the closed bar is flushed via
   ``DataService.save_market_data`` to
   ``data/market/{broker}_live/{symbol}/1min.parquet``.
4. Updates the row's ``tick_rate_per_min`` and ``last_tick_at`` once a
   minute. A retention sweep deletes day-partitioned tick files older
   than the row's ``tick_retention_hours``.

If the broker has no credentials configured, the task logs the situation
and idles — it does not crash the lifespan. Tests inject a fake adapter
via ``adapter_factory`` (callable arg) to exercise the pipeline without
real WebSockets.
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

from coordinator.database.models import Account, LiveSubscription, Setting
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
        """If a bar exists for a strictly-earlier minute, return its row."""
        if self.minute_start is None or self.minute_start >= now_minute:
            return None
        row = {
            "timestamp": self.minute_start,
            "open": self.open_,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }
        # Reset for the next minute.
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
    handle: Optional[object] = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_tick_at: Optional[datetime] = None


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
        self._retention_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._bar_subscribers: dict[tuple[str, str, str], set[Callable]] = {}
        self._event_subscribers: dict[tuple[str, str], set[Callable]] = {}

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
                await self.start_subscription(r.broker, r.symbol)
        self._retention_task = asyncio.create_task(self._retention_loop())

    async def stop(self) -> None:
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
            state = self._states.get(key)
            if state is not None and state.handle is not None:
                close = getattr(state.handle, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:  # noqa: BLE001
                        pass
        self._tasks.clear()
        self._states.clear()

    async def start_subscription(self, broker: str, symbol: str) -> None:
        key = (broker, symbol)
        if key in self._tasks:
            return
        self._states[key] = _SubState()
        self._tasks[key] = asyncio.create_task(self._run(broker, symbol))

    async def stop_subscription(self, broker: str, symbol: str) -> None:
        key = (broker, symbol)
        t = self._tasks.pop(key, None)
        if t:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        state = self._states.pop(key, None)
        if state and state.handle is not None:
            close = getattr(state.handle, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass

    # ------- main task -------
    async def _run(self, broker: str, symbol: str) -> None:
        key = (broker, symbol)
        state = self._states[key]
        logger.info("LiveFeedAggregator starting for %s/%s", broker, symbol)
        loop = asyncio.get_running_loop()

        # Resolve which account's creds power the broker's live feed.
        account = await self._resolve_live_feed_account(broker)
        if account is None:
            logger.warning(
                "No live_feed_account.%s setting (or account); aggregator idles for %s/%s",
                broker, broker, symbol,
            )
            await self._mark_subscription_error(
                broker, symbol,
                f"No live_feed_account.{broker} configured",
            )
            try:
                while True:
                    await asyncio.sleep(60)
            except asyncio.CancelledError:
                return

        creds = self._decrypt_creds(account)
        try:
            adapter = self._adapter_factory(
                account.broker_type, account.environment, creds
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("Failed to construct adapter for %s", broker)
            await self._mark_subscription_error(broker, symbol, f"adapter init failed: {e}")
            try:
                while True:
                    await asyncio.sleep(60)
            except asyncio.CancelledError:
                return

        # Callbacks fire on the stream's own thread; bridge into our state
        # under a small lock — we don't need an event-loop call here since
        # all draining happens from the asyncio task below.
        def _on_trade(tick: dict) -> None:
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

        def _on_quote(tick: dict) -> None:
            with state.lock:
                state.quotes.append(tick)

        try:
            state.handle = adapter.start_market_data_stream(
                [symbol], _on_trade, _on_quote
            )
        except NotImplementedError:
            logger.warning(
                "Adapter for %s does not implement start_market_data_stream; aggregator idles",
                broker,
            )
            await self._mark_subscription_error(
                broker, symbol, "adapter lacks start_market_data_stream"
            )
            try:
                while True:
                    await asyncio.sleep(60)
            except asyncio.CancelledError:
                return
        except Exception as e:  # noqa: BLE001
            logger.exception("Failed to open stream for %s/%s", broker, symbol)
            await self._mark_subscription_error(broker, symbol, f"stream open failed: {e}")
            return

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
        finally:
            handle = state.handle
            if handle is not None:
                close = getattr(handle, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:  # noqa: BLE001
                        pass

    # ------- helpers -------
    async def _resolve_live_feed_account(self, broker: str) -> Optional[Account]:
        async with self._sf() as session:
            setting = (
                await session.execute(
                    select(Setting).where(Setting.key == f"live_feed_account.{broker}")
                )
            ).scalar_one_or_none()
            if setting is None or not setting.value:
                # Fallback: first account on that broker.
                acct = (
                    await session.execute(
                        select(Account).where(Account.broker_type == broker)
                    )
                ).scalars().first()
                return acct
            account_id = setting.value
            return (
                await session.execute(
                    select(Account).where(Account.id == account_id)
                )
            ).scalar_one_or_none()

    def _decrypt_creds(self, account: Account) -> dict:
        try:
            return self._encryption.decrypt_json(account.credentials)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to decrypt account credentials")
            return {}

    async def _mark_subscription_error(
        self, broker: str, symbol: str, message: str
    ) -> None:
        async with self._sf() as session:
            sub = (
                await session.execute(
                    select(LiveSubscription).where(
                        LiveSubscription.broker == broker,
                        LiveSubscription.symbol == symbol,
                    )
                )
            ).scalar_one_or_none()
            if sub is not None:
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
            sub = (
                await session.execute(
                    select(LiveSubscription).where(
                        LiveSubscription.broker == broker,
                        LiveSubscription.symbol == symbol,
                    )
                )
            ).scalar_one_or_none()
            if sub is not None:
                sub.tick_rate_per_min = rate
                if last_tick is not None:
                    sub.last_tick_at = last_tick
                await session.commit()

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
