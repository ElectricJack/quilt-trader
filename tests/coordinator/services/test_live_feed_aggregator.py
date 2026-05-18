"""Integration tests for ``LiveFeedAggregator`` using a fake stream.

The fake adapter emits a synthetic burst of trades + quotes when the
aggregator opens its stream. We then drive the aggregator's flush loop
once and assert:

- ``trades-{today}.parquet`` and ``quotes-{today}.parquet`` exist with
  the expected rows.
- ``1min.parquet`` contains the closed bar with correct OHLCV.
- ``LiveSubscription.last_tick_at`` updates after the rate-update path runs.
"""
from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import select

from coordinator.database.connection import create_engine, create_session_factory
from coordinator.database.models import Account, Base, LiveSubscription, Setting
from coordinator.services.data_service import DataService
from coordinator.services.encryption import EncryptionService
from coordinator.services.live_feed_aggregator import LiveFeedAggregator
from worker.broker_adapter import MarketDataStreamHandle

_TEST_ENCRYPTION = EncryptionService("test-key-for-live-feed-aggregator")


@dataclass
class _FakeHandle(MarketDataStreamHandle):
    stopped: bool = False

    def close(self) -> None:
        self.stopped = True


class _FakeAdapter:
    """Emits one synthetic batch of trades + quotes when the stream opens."""

    def __init__(self, ticks: list[dict], quotes: list[dict]) -> None:
        self._ticks = ticks
        self._quotes = quotes

    def start_market_data_stream(self, symbols, on_trade, on_quote, asset_class="equities"):
        # Fire the callbacks synchronously on this thread — the aggregator's
        # callback path takes the state lock, so this is safe.
        for t in self._ticks:
            on_trade(t)
        for q in self._quotes:
            on_quote(q)
        return _FakeHandle()


@pytest_asyncio.fixture
async def engine_and_factory():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(engine)
    yield engine, sf
    await engine.dispose()


@pytest.mark.asyncio
async def test_aggregator_writes_tick_parquets_and_bars(tmp_path, engine_and_factory):
    engine, sf = engine_and_factory

    # Pre-seed: account + setting that maps live_feed_account.fakebroker → that account,
    # plus a running LiveSubscription row.
    async with sf() as session:
        acct = Account(
            id="acct-fake",
            name="fake",
            broker_type="fakebroker",
            environment="paper",
            credentials=_TEST_ENCRYPTION.encrypt_json({"api_key": "x", "secret_key": "y"}),
            supported_asset_types=["equities"],
        )
        session.add(acct)
        session.add(Setting(
            key="live_feed_account.fakebroker", value="acct-fake", encrypted=False
        ))
        session.add(LiveSubscription(
            broker="fakebroker", symbol="SPY", status="running", dependent_count=1,
        ))
        await session.commit()

    # Build 60 trades over a 60-second window — all in one UTC minute.
    minute = datetime(2026, 5, 14, 14, 30, 0, tzinfo=timezone.utc)
    trades = []
    for i in range(60):
        trades.append({
            "symbol": "SPY",
            "timestamp": minute + timedelta(seconds=i, microseconds=500_000),
            "price": 500.0 + i * 0.01,  # rising
            "size": 10.0,
        })
    quotes = [
        {
            "symbol": "SPY",
            "timestamp": minute + timedelta(seconds=i),
            "bid": 500.0,
            "ask": 500.1,
            "bid_size": 100.0,
            "ask_size": 100.0,
        }
        for i in range(5)
    ]

    fake = _FakeAdapter(trades, quotes)

    market_dir = str(tmp_path / "market")
    data_service = DataService(market_data_dir=market_dir, custom_data_dir=str(tmp_path / "custom"))

    # Drive _now() into the *next* minute so the in-flight bar is "closed".
    fake_now = minute + timedelta(minutes=1, seconds=3)

    agg = LiveFeedAggregator(
        session_factory=sf,
        encryption=_TEST_ENCRYPTION,
        data_service=data_service,
        adapter_factory=lambda b, e, c: fake,
        market_dir=market_dir,
        flush_interval_s=0.05,
        now_fn=lambda: fake_now,
    )

    await agg.start()
    # Give the run task a couple of flush cycles.
    await asyncio.sleep(0.5)
    await agg.stop()

    # ---- assert trades parquet ----
    ticks_dir = Path(market_dir) / "fakebroker_live" / "SPY" / "ticks"
    trades_path = ticks_dir / f"trades-{minute.date().isoformat()}.parquet"
    quotes_path = ticks_dir / f"quotes-{minute.date().isoformat()}.parquet"
    assert trades_path.exists(), f"missing {trades_path}; ls={list(ticks_dir.iterdir()) if ticks_dir.exists() else 'no-dir'}"
    assert quotes_path.exists(), f"missing {quotes_path}"

    tdf = pd.read_parquet(trades_path)
    qdf = pd.read_parquet(quotes_path)
    assert len(tdf) == 60
    assert len(qdf) == 5
    assert set(tdf.columns) >= {"symbol", "timestamp", "price", "size"}

    # ---- assert 1min bar ----
    bar_path = Path(market_dir) / "fakebroker_live" / "SPY" / "1min.parquet"
    assert bar_path.exists(), f"missing {bar_path}"
    bars = pd.read_parquet(bar_path)
    assert len(bars) == 1
    row = bars.iloc[0]
    assert float(row["open"]) == pytest.approx(500.0)
    # high should be the last (60th) trade's price: 500.0 + 59*0.01
    assert float(row["high"]) == pytest.approx(500.0 + 59 * 0.01)
    assert float(row["low"]) == pytest.approx(500.0)
    assert float(row["close"]) == pytest.approx(500.0 + 59 * 0.01)
    assert float(row["volume"]) == pytest.approx(60 * 10.0)


@pytest.mark.asyncio
async def test_aggregator_idles_when_no_account(tmp_path, engine_and_factory):
    """Should not crash when there's no live_feed_account setting nor any matching account."""
    engine, sf = engine_and_factory

    async with sf() as session:
        session.add(LiveSubscription(
            broker="nobroker", symbol="SPY", status="running", dependent_count=1,
        ))
        await session.commit()

    market_dir = str(tmp_path / "market")
    agg = LiveFeedAggregator(
        session_factory=sf,
        encryption=_TEST_ENCRYPTION,
        adapter_factory=lambda b, e, c: _FakeAdapter([], []),
        market_dir=market_dir,
        flush_interval_s=0.05,
    )
    await agg.start()
    await asyncio.sleep(0.2)
    await agg.stop()

    # Subscription should have been marked with an error message.
    async with sf() as session:
        sub = (
            await session.execute(
                select(LiveSubscription).where(LiveSubscription.broker == "nobroker")
            )
        ).scalar_one()
        assert sub.last_error is not None
        assert "live_feed_account" in (sub.last_error or "")


@pytest.mark.asyncio
async def test_aggregator_updates_rate_and_last_tick(tmp_path, engine_and_factory):
    """After ~60s wall time, the row's tick_rate_per_min + last_tick_at update."""
    engine, sf = engine_and_factory

    async with sf() as session:
        session.add(Account(
            id="acct-fake",
            name="fake",
            broker_type="fakebroker",
            environment="paper",
            credentials=_TEST_ENCRYPTION.encrypt_json({"api_key": "x", "secret_key": "y"}),
            supported_asset_types=["equities"],
        ))
        session.add(Setting(
            key="live_feed_account.fakebroker", value="acct-fake", encrypted=False
        ))
        session.add(LiveSubscription(
            broker="fakebroker", symbol="SPY", status="running", dependent_count=1,
        ))
        await session.commit()

    minute = datetime(2026, 5, 14, 14, 30, 0, tzinfo=timezone.utc)
    trades = [
        {
            "symbol": "SPY",
            "timestamp": minute + timedelta(seconds=i),
            "price": 100.0,
            "size": 1.0,
        }
        for i in range(10)
    ]
    fake = _FakeAdapter(trades, [])

    # We need _now() to advance: start at `minute + 30s`, then step forward
    # to `minute + 90s` after one flush so the rate-update branch fires.
    times = [
        minute + timedelta(seconds=30),
        minute + timedelta(seconds=30),  # used during the first flush
        minute + timedelta(seconds=90),  # second iteration: ≥60s later
        minute + timedelta(seconds=90),
        minute + timedelta(seconds=90),
    ]
    idx = {"i": 0}

    def now_fn():
        i = idx["i"]
        out = times[min(i, len(times) - 1)]
        idx["i"] = i + 1
        return out

    market_dir = str(tmp_path / "market")
    agg = LiveFeedAggregator(
        session_factory=sf,
        encryption=_TEST_ENCRYPTION,
        adapter_factory=lambda b, e, c: fake,
        market_dir=market_dir,
        flush_interval_s=0.05,
        now_fn=now_fn,
    )
    await agg.start()
    await asyncio.sleep(0.5)
    await agg.stop()

    async with sf() as session:
        sub = (
            await session.execute(
                select(LiveSubscription).where(LiveSubscription.broker == "fakebroker")
            )
        ).scalar_one()
        # last_tick_at should be set to one of the trade timestamps.
        assert sub.last_tick_at is not None
        # tick_rate_per_min should be non-None after the rate-update branch.
        assert sub.tick_rate_per_min is not None


@pytest.mark.asyncio
async def test_subscribe_bars_receives_callback_on_dispatch():
    from coordinator.services.live_feed_aggregator import LiveFeedAggregator
    agg = LiveFeedAggregator.__new__(LiveFeedAggregator)
    agg._bar_subscribers = {}
    agg._event_subscribers = {}
    received: list = []
    async def cb(bar):
        received.append(bar)
    agg.subscribe_bars("alpaca", "AAPL", "1min", cb)
    await agg._dispatch_bar("alpaca", "AAPL", "1min", {"close": 100.0})
    assert received == [{"close": 100.0}]


@pytest.mark.asyncio
async def test_unsubscribe_bars_stops_callbacks():
    from coordinator.services.live_feed_aggregator import LiveFeedAggregator
    agg = LiveFeedAggregator.__new__(LiveFeedAggregator)
    agg._bar_subscribers = {}
    agg._event_subscribers = {}
    received: list = []
    async def cb(bar):
        received.append(bar)
    agg.subscribe_bars("alpaca", "AAPL", "1min", cb)
    agg.unsubscribe_bars("alpaca", "AAPL", "1min", cb)
    await agg._dispatch_bar("alpaca", "AAPL", "1min", {"close": 100.0})
    assert received == []


@pytest.mark.asyncio
async def test_subscribe_events_receives_callback_on_dispatch():
    from coordinator.services.live_feed_aggregator import LiveFeedAggregator
    agg = LiveFeedAggregator.__new__(LiveFeedAggregator)
    agg._bar_subscribers = {}
    agg._event_subscribers = {}
    received: list = []
    async def cb(evt):
        received.append(evt)
    agg.subscribe_events("alpaca", "AAPL", cb)
    await agg._dispatch_event("alpaca", "AAPL", {"price": 100.0, "size": 10})
    assert len(received) == 1


@pytest.mark.asyncio
async def test_subscriber_exception_does_not_break_other_subscribers():
    from coordinator.services.live_feed_aggregator import LiveFeedAggregator
    agg = LiveFeedAggregator.__new__(LiveFeedAggregator)
    agg._bar_subscribers = {}
    agg._event_subscribers = {}
    received: list = []
    async def bad_cb(bar):
        raise RuntimeError("boom")
    async def good_cb(bar):
        received.append(bar)
    agg.subscribe_bars("alpaca", "AAPL", "1min", bad_cb)
    agg.subscribe_bars("alpaca", "AAPL", "1min", good_cb)
    await agg._dispatch_bar("alpaca", "AAPL", "1min", {"close": 100.0})
    assert received == [{"close": 100.0}]


@pytest.mark.asyncio
async def test_subscribe_with_empty_subscribers_for_target_is_noop():
    """Dispatch with no subscribers does not raise."""
    from coordinator.services.live_feed_aggregator import LiveFeedAggregator
    agg = LiveFeedAggregator.__new__(LiveFeedAggregator)
    agg._bar_subscribers = {}
    agg._event_subscribers = {}
    await agg._dispatch_bar("alpaca", "AAPL", "1min", {"close": 100.0})  # no raise
    await agg._dispatch_event("alpaca", "AAPL", {"x": 1})  # no raise
