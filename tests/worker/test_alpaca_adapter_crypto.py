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
