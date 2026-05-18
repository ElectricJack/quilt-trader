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
    async def fake_adapter_for_account(account_id):
        return FakeAdapter()
    monkeypatch.setattr(agg, "_adapter_for_account", fake_adapter_for_account)
    monkeypatch.setattr(agg, "_ticks_dir",
                        lambda b, s: tmp_path / b / s / "ticks")

    await agg.start_subscription("acct-1", "alpaca", "SPY", "equities")
    await agg.start_subscription("acct-1", "alpaca", "QQQ", "equities")
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
    async def fake_adapter_for_account(account_id):
        return FakeAdapter()
    monkeypatch.setattr(agg, "_adapter_for_account", fake_adapter_for_account)
    monkeypatch.setattr(agg, "_ticks_dir",
                        lambda b, s: tmp_path / b / s / "ticks")

    await agg.start_subscription("acct-1", "alpaca", "SPY", "equities")
    await agg.start_subscription("acct-1", "alpaca", "BTCUSD", "crypto")
    assert len(handles_opened) == 2


@pytest.mark.asyncio
async def test_no_streamconn_created_when_adapter_unavailable(tmp_path, monkeypatch):
    """If no account is configured for the broker, no _StreamConn is created
    and a later subscription on the same (broker, asset_class) when an adapter
    DOES become available works correctly."""
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
    monkeypatch.setattr(agg, "_ticks_dir",
                        lambda b, s: tmp_path / b / s / "ticks")
    # Suppress DB write (session_factory is None in these unit tests).
    async def noop_mark_error(*a, **kw):
        pass
    monkeypatch.setattr(agg, "_mark_subscription_error", noop_mark_error)

    # First call: adapter unavailable.
    async def no_adapter(account_id):
        return None
    monkeypatch.setattr(agg, "_adapter_for_account", no_adapter)
    await agg.start_subscription("acct-1", "alpaca", "SPY", "equities")
    assert ("acct-1", "equities") not in agg._streams, \
        "no _StreamConn should be created when adapter is None"

    # Second call (different symbol, adapter now available).
    async def with_adapter(account_id):
        return FakeAdapter()
    monkeypatch.setattr(agg, "_adapter_for_account", with_adapter)
    await agg.start_subscription("acct-1", "alpaca", "QQQ", "equities")
    assert ("acct-1", "equities") in agg._streams
    assert agg._streams[("acct-1", "equities")].symbols == {"QQQ"}


@pytest.mark.asyncio
async def test_two_accounts_open_separate_streams_same_broker_asset_class(
    tmp_path, monkeypatch,
):
    """Two Alpaca accounts subscribing to the same asset_class open two
    independent WS connections (so each account's free-tier slot is its own)."""
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
        def __init__(self, api_key):
            self.api_key = api_key
        def start_market_data_stream(self, symbols, on_trade, on_quote, asset_class="equities"):
            h = FakeHandle()
            h.add_symbols(symbols)
            return h
        def close(self): pass

    seen_api_keys = []

    async def fake_adapter_for_account(account_id):
        seen_api_keys.append(account_id)
        return FakeAdapter(api_key=account_id)

    agg = LiveFeedAggregator(session_factory=None, encryption=None)
    monkeypatch.setattr(agg, "_adapter_for_account", fake_adapter_for_account)
    monkeypatch.setattr(agg, "_ticks_dir",
                        lambda b, s: tmp_path / b / s / "ticks")
    async def noop_mark_error(*a, **kw): pass
    monkeypatch.setattr(agg, "_mark_subscription_error", noop_mark_error)

    await agg.start_subscription("acct-1", "alpaca", "SPY", "equities")
    await agg.start_subscription("acct-2", "alpaca", "SPY", "equities")
    assert len(handles_opened) == 2
    assert seen_api_keys == ["acct-1", "acct-2"]
