"""Tests for multi-symbol stream packing: one stream per (broker, asset_class)."""
import asyncio
import pytest
from unittest.mock import MagicMock

from coordinator.services.live_feed_aggregator import LiveFeedAggregator


@pytest.mark.asyncio
async def test_two_equities_subscriptions_share_one_stream(tmp_path, monkeypatch):
    """Subscribing to two equities on alpaca opens exactly one stream
    handle whose symbol set contains both."""
    handles_opened = []
    symbol_sets: list[set[str]] = []

    class FakeHandle:
        def __init__(self):
            self.symbols: set[str] = set()
            handles_opened.append(self)
            symbol_sets.append(self.symbols)
        def add_symbols(self, syms):
            self.symbols.update(syms)
        def remove_symbols(self, syms):
            self.symbols.difference_update(syms)
        def close(self): pass

    class FakeAdapter:
        def start_market_data_stream(self, symbols, on_trade, on_quote, asset_class="equities"):
            h = FakeHandle()
            h.add_symbols(symbols)
            return h
        def close(self): pass

    agg = LiveFeedAggregator(session_factory=None, encryption=None)
    async def fake_adapter_for_broker(broker):
        return FakeAdapter()
    monkeypatch.setattr(agg, "_adapter_for_broker", fake_adapter_for_broker)
    monkeypatch.setattr(agg, "_ticks_dir",
                        lambda b, s: tmp_path / b / s / "ticks")

    await agg.start_subscription("alpaca", "SPY", "equities")
    await agg.start_subscription("alpaca", "QQQ", "equities")
    assert len(handles_opened) == 1, "second equity subscription should reuse the stream"
    assert handles_opened[0].symbols == {"SPY", "QQQ"}


@pytest.mark.asyncio
async def test_equities_and_crypto_open_separate_streams(tmp_path, monkeypatch):
    handles_opened = []

    class FakeHandle:
        def __init__(self):
            self.symbols: set[str] = set()
            handles_opened.append(self)
        def add_symbols(self, syms):
            self.symbols.update(syms)
        def remove_symbols(self, syms):
            self.symbols.difference_update(syms)
        def close(self): pass

    class FakeAdapter:
        def start_market_data_stream(self, symbols, on_trade, on_quote, asset_class="equities"):
            h = FakeHandle()
            h.add_symbols(symbols)
            return h
        def close(self): pass

    agg = LiveFeedAggregator(session_factory=None, encryption=None)
    async def fake_adapter_for_broker(broker):
        return FakeAdapter()
    monkeypatch.setattr(agg, "_adapter_for_broker", fake_adapter_for_broker)
    monkeypatch.setattr(agg, "_ticks_dir",
                        lambda b, s: tmp_path / b / s / "ticks")

    await agg.start_subscription("alpaca", "SPY", "equities")
    await agg.start_subscription("alpaca", "BTCUSD", "crypto")
    assert len(handles_opened) == 2
