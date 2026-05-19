"""Unit tests for CoinbaseStreamAdapter."""
import threading

import pytest

from worker.coinbase_stream_adapter import (
    CoinbaseStreamAdapter,
    _CoinbaseStreamHandle,
    _to_coinbase_symbol,
    _from_coinbase_symbol,
)
from worker.broker_adapter import MarketDataStreamHandle


def test_to_coinbase_symbol():
    assert _to_coinbase_symbol("BTCUSD") == "BTC-USD"
    assert _to_coinbase_symbol("ETHUSD") == "ETH-USD"
    assert _to_coinbase_symbol("BTC-USD") == "BTC-USD"  # already formatted


def test_from_coinbase_symbol():
    assert _from_coinbase_symbol("BTC-USD") == "BTCUSD"
    assert _from_coinbase_symbol("ETH-USD") == "ETHUSD"


def test_adapter_is_data_only():
    adapter = CoinbaseStreamAdapter()
    with pytest.raises(NotImplementedError):
        adapter.get_positions()
    with pytest.raises(NotImplementedError):
        adapter.get_account_info()
    with pytest.raises(NotImplementedError):
        adapter.submit_order()


def test_close_is_noop():
    adapter = CoinbaseStreamAdapter()
    adapter.close()  # Should not raise


def test_start_market_data_stream_returns_handle():
    """Verify the adapter constructs a handle (don't actually connect)."""
    adapter = CoinbaseStreamAdapter()

    original_start = threading.Thread.start
    started = []
    threading.Thread.start = lambda self: started.append(self.name)
    try:
        handle = adapter.start_market_data_stream(
            symbols=["BTCUSD"],
            on_trade=lambda t: None,
            on_quote=lambda q: None,
            asset_class="crypto",
        )
        assert handle is not None
        assert isinstance(handle, MarketDataStreamHandle)
        assert "coinbase-stream" in started
    finally:
        threading.Thread.start = original_start


def test_start_market_data_stream_converts_symbols():
    """Symbol map is built correctly before passing to handle."""
    adapter = CoinbaseStreamAdapter()

    original_start = threading.Thread.start
    threading.Thread.start = lambda self: None  # no-op
    try:
        handle = adapter.start_market_data_stream(
            symbols=["BTCUSD", "ETHUSD"],
            on_trade=lambda t: None,
            on_quote=lambda q: None,
            asset_class="crypto",
        )
        assert handle._symbols == ["BTC-USD", "ETH-USD"]
        assert handle._symbol_map == {"BTC-USD": "BTCUSD", "ETH-USD": "ETHUSD"}
    finally:
        threading.Thread.start = original_start


def test_dispatch_market_trade():
    """_dispatch correctly calls on_trade for market_trades channel."""
    stop = threading.Event()
    stop.set()
    handle = object.__new__(_CoinbaseStreamHandle)
    handle._stop = stop
    handle._symbols = ["BTC-USD"]
    handle._symbol_map = {"BTC-USD": "BTCUSD"}
    handle._thread = threading.Thread(target=lambda: None, daemon=True)
    handle._thread.start()

    received = []
    handle._on_trade = received.append
    handle._on_quote = lambda x: None

    msg = {
        "channel": "market_trades",
        "events": [
            {
                "trades": [
                    {
                        "product_id": "BTC-USD",
                        "price": "43250.50",
                        "size": "0.01",
                        "time": "2026-05-18T12:00:00Z",
                    }
                ]
            }
        ],
    }
    handle._dispatch(msg)

    assert len(received) == 1
    assert received[0]["symbol"] == "BTCUSD"
    assert received[0]["price"] == 43250.50
    assert received[0]["size"] == 0.01


def test_dispatch_ticker():
    """_dispatch correctly calls on_quote for ticker channel."""
    stop = threading.Event()
    stop.set()
    handle = object.__new__(_CoinbaseStreamHandle)
    handle._stop = stop
    handle._symbols = ["BTC-USD"]
    handle._symbol_map = {"BTC-USD": "BTCUSD"}
    handle._thread = threading.Thread(target=lambda: None, daemon=True)
    handle._thread.start()

    received = []
    handle._on_trade = lambda x: None
    handle._on_quote = received.append

    msg = {
        "channel": "ticker",
        "events": [
            {
                "tickers": [
                    {
                        "product_id": "BTC-USD",
                        "best_bid": "43200.00",
                        "best_ask": "43210.00",
                        "best_bid_quantity": "0.5",
                        "best_ask_quantity": "0.3",
                    }
                ]
            }
        ],
    }
    handle._dispatch(msg)

    assert len(received) == 1
    assert received[0]["symbol"] == "BTCUSD"
    assert received[0]["bid"] == 43200.00
    assert received[0]["ask"] == 43210.00
    assert received[0]["bid_size"] == 0.5
    assert received[0]["ask_size"] == 0.3


def test_dispatch_unknown_channel_is_noop():
    """Unknown channel messages are silently ignored."""
    stop = threading.Event()
    stop.set()
    handle = object.__new__(_CoinbaseStreamHandle)
    handle._stop = stop
    handle._symbols = []
    handle._symbol_map = {}
    handle._thread = threading.Thread(target=lambda: None, daemon=True)
    handle._thread.start()

    calls = []
    handle._on_trade = calls.append
    handle._on_quote = calls.append

    handle._dispatch({"channel": "heartbeat", "events": []})
    assert calls == []


def test_symbol_map_fallback():
    """Symbols not in the map fall back to _from_coinbase_symbol."""
    stop = threading.Event()
    stop.set()
    handle = object.__new__(_CoinbaseStreamHandle)
    handle._stop = stop
    handle._symbols = ["ETH-USD"]
    handle._symbol_map = {}  # empty map → fallback
    handle._thread = threading.Thread(target=lambda: None, daemon=True)
    handle._thread.start()

    received = []
    handle._on_trade = received.append
    handle._on_quote = lambda x: None

    msg = {
        "channel": "market_trades",
        "events": [{"trades": [{"product_id": "ETH-USD", "price": "3000", "size": "1"}]}],
    }
    handle._dispatch(msg)

    assert received[0]["symbol"] == "ETHUSD"
