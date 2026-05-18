from unittest.mock import patch, MagicMock

from worker.alpaca_adapter import AlpacaAdapter


def test_start_market_data_stream_uses_stock_data_stream_for_equities():
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    captured = {}

    class FakeStockStream:
        def __init__(self, api_key, secret_key):
            captured["class"] = "stock"
        def subscribe_trades(self, h, *symbols):
            captured["symbols"] = list(symbols)
        def subscribe_quotes(self, h, *symbols): pass
        def run(self): pass
        def stop(self): pass

    with patch("alpaca.data.live.StockDataStream", FakeStockStream):
        adapter.start_market_data_stream(
            symbols=["SPY"], on_trade=lambda t: None, on_quote=lambda q: None,
            asset_class="equities",
        )
    assert captured["class"] == "stock"
    assert captured["symbols"] == ["SPY"]


def test_start_market_data_stream_uses_crypto_data_stream_for_crypto():
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    captured = {}

    class FakeCryptoStream:
        def __init__(self, api_key, secret_key):
            captured["class"] = "crypto"
        def subscribe_trades(self, h, *symbols):
            captured["symbols"] = list(symbols)
        def subscribe_quotes(self, h, *symbols): pass
        def run(self): pass
        def stop(self): pass

    with patch("alpaca.data.live.CryptoDataStream", FakeCryptoStream):
        adapter.start_market_data_stream(
            symbols=["BTC/USD"], on_trade=lambda t: None, on_quote=lambda q: None,
            asset_class="crypto",
        )
    assert captured["class"] == "crypto"
    assert captured["symbols"] == ["BTC/USD"]


def test_start_market_data_stream_normalizes_crypto_no_slash_to_slash():
    """`BTCUSD` (order-side format) must be auto-converted to `BTC/USD` for
    the crypto WS subscribe call — otherwise Alpaca silently delivers no ticks.
    Inbound ticks coming back with the slashed form are denormalized to the
    caller-facing BTCUSD."""
    import asyncio
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    captured = {}

    class FakeTrade:
        def __init__(self, symbol):
            self.symbol = symbol
            self.timestamp = None
            self.price = 100.0
            self.size = 1.0

    class FakeCryptoStream:
        def __init__(self, api_key, secret_key): pass
        def subscribe_trades(self, h, *symbols):
            captured["symbols"] = list(symbols)
            captured["trade_handler"] = h
        def subscribe_quotes(self, h, *symbols): pass
        def run(self): pass
        def stop(self): pass

    received = []
    with patch("alpaca.data.live.CryptoDataStream", FakeCryptoStream):
        adapter.start_market_data_stream(
            symbols=["BTCUSD"],
            on_trade=lambda t: received.append(t),
            on_quote=lambda q: None,
            asset_class="crypto",
        )

    # WS receives the slashed form.
    assert captured["symbols"] == ["BTC/USD"]

    # Ticks coming back with the slashed form are denormalized to BTCUSD
    # before being dispatched to the caller.
    asyncio.run(captured["trade_handler"](FakeTrade("BTC/USD")))
    assert len(received) == 1
    assert received[0]["symbol"] == "BTCUSD"
