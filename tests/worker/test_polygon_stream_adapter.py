"""Unit tests for PolygonStreamAdapter."""
import threading
from unittest.mock import MagicMock, patch

import pytest

from worker.polygon_stream_adapter import PolygonStreamAdapter, _PolygonStreamHandle
from worker.broker_adapter import MarketDataStreamHandle


class TestPolygonStreamAdapter:
    def test_constructor(self):
        adapter = PolygonStreamAdapter(api_key="test-key")
        assert adapter._api_key == "test-key"

    def test_trading_stubs_raise(self):
        adapter = PolygonStreamAdapter(api_key="test-key")
        with pytest.raises(NotImplementedError, match="data-only"):
            adapter.get_positions()
        with pytest.raises(NotImplementedError, match="data-only"):
            adapter.get_account_info()
        with pytest.raises(NotImplementedError, match="data-only"):
            adapter.submit_order()

    def test_close_is_noop(self):
        adapter = PolygonStreamAdapter(api_key="test-key")
        adapter.close()  # Should not raise

    def test_start_market_data_stream_returns_handle(self):
        """start_market_data_stream returns a MarketDataStreamHandle (mocked WS)."""
        adapter = PolygonStreamAdapter(api_key="test-key")

        trades = []
        quotes = []

        # Patch the WS connect so the background thread exits immediately
        fake_ws = MagicMock()
        # First recv returns connected message, second returns auth_failed to exit
        fake_ws.recv.side_effect = [
            '[{"status": "connected"}]',
            '[{"status": "auth_failed", "message": "not authorized"}]',
        ]
        fake_ws.__enter__ = MagicMock(return_value=fake_ws)
        fake_ws.__exit__ = MagicMock(return_value=False)

        with patch("websockets.sync.client.connect", return_value=fake_ws):
            handle = adapter.start_market_data_stream(
                symbols=["SPY"],
                on_trade=trades.append,
                on_quote=quotes.append,
                asset_class="equities",
            )

        assert isinstance(handle, MarketDataStreamHandle)
        handle.close()

    def test_format_symbol_crypto(self):
        adapter = PolygonStreamAdapter(api_key="k")
        # Access via a handle instance (no actual thread needed)
        stop = threading.Event()
        stop.set()  # prevent thread from doing anything
        handle = object.__new__(_PolygonStreamHandle)
        handle._asset_class = "crypto"
        handle._symbols = []
        handle._stop = stop
        handle._thread = threading.Thread(target=lambda: None, daemon=True)
        handle._thread.start()
        assert handle._format_symbol("BTCUSD") == "X:BTCUSD"
        assert handle._normalize_symbol("X:BTCUSD") == "BTCUSD"

    def test_format_symbol_equities(self):
        stop = threading.Event()
        stop.set()
        handle = object.__new__(_PolygonStreamHandle)
        handle._asset_class = "equities"
        handle._symbols = []
        handle._stop = stop
        handle._thread = threading.Thread(target=lambda: None, daemon=True)
        handle._thread.start()
        assert handle._format_symbol("SPY") == "SPY"
        assert handle._normalize_symbol("SPY") == "SPY"

    def test_dispatch_trade(self):
        stop = threading.Event()
        stop.set()
        handle = object.__new__(_PolygonStreamHandle)
        handle._asset_class = "equities"
        handle._symbols = []
        handle._stop = stop
        handle._thread = threading.Thread(target=lambda: None, daemon=True)
        handle._thread.start()

        received = []
        handle._on_trade = received.append
        handle._on_quote = lambda x: None

        handle._dispatch({"ev": "T", "sym": "SPY", "p": 450.0, "s": 100.0, "t": 1700000000000})
        assert len(received) == 1
        assert received[0]["symbol"] == "SPY"
        assert received[0]["price"] == 450.0
        assert received[0]["size"] == 100.0

    def test_dispatch_quote(self):
        stop = threading.Event()
        stop.set()
        handle = object.__new__(_PolygonStreamHandle)
        handle._asset_class = "equities"
        handle._symbols = []
        handle._stop = stop
        handle._thread = threading.Thread(target=lambda: None, daemon=True)
        handle._thread.start()

        received = []
        handle._on_trade = lambda x: None
        handle._on_quote = received.append

        handle._dispatch({
            "ev": "Q", "sym": "SPY",
            "bp": 449.5, "ap": 450.5,
            "bs": 200.0, "as": 150.0,
            "t": 1700000000000,
        })
        assert len(received) == 1
        assert received[0]["symbol"] == "SPY"
        assert received[0]["bid"] == 449.5
        assert received[0]["ask"] == 450.5

    def test_cluster_mapping(self):
        adapter = PolygonStreamAdapter(api_key="k")
        stop_evt = threading.Event()
        stop_evt.set()

        with patch("worker.polygon_stream_adapter._PolygonStreamHandle.__init__",
                   return_value=None) as mock_init:
            # Mock out the handle init so no thread is spawned
            mock_init.return_value = None
            handle = object.__new__(_PolygonStreamHandle)
            handle._stop = stop_evt

        # Verify cluster URLs for different asset classes
        from worker.polygon_stream_adapter import _CLUSTER_MAP, _WS_BASE
        assert _CLUSTER_MAP["equities"] == "stocks"
        assert _CLUSTER_MAP["crypto"] == "crypto"
        assert _CLUSTER_MAP["options"] == "options"
