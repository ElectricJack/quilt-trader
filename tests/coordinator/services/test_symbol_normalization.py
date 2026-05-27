"""Symbol normalization seam: algorithm-canonical symbol → provider-specific
disk path, transparently routed through the asset registry."""
import pandas as pd
import pytest
from datetime import datetime, timezone

from coordinator.services.backtest_tick_context import BacktestTickContext
from coordinator.services.asset_services.crypto import _to_canonical


def test_to_canonical_handles_all_input_forms():
    assert _to_canonical("BTC/USD") == "BTCUSD"
    assert _to_canonical("BTC-USD") == "BTCUSD"
    assert _to_canonical("BTCUSD") == "BTCUSD"
    assert _to_canonical("ETH-USDT") == "ETHUSDT"


def test_resolve_symbol_canonicalizes_input():
    from coordinator.services.asset_services.crypto import CryptoAssetService

    svc = CryptoAssetService()
    # Any input form → yfinance dash form
    assert svc.resolve_symbol("BTC/USD", "yfinance") == "BTC-USD"
    assert svc.resolve_symbol("BTC-USD", "yfinance") == "BTC-USD"
    assert svc.resolve_symbol("BTCUSD", "yfinance") == "BTC-USD"

    # Any input form → alpaca slash form
    assert svc.resolve_symbol("BTC/USD", "alpaca") == "BTC/USD"
    assert svc.resolve_symbol("BTC-USD", "alpaca") == "BTC/USD"
    assert svc.resolve_symbol("BTCUSD", "alpaca") == "BTC/USD"


def test_market_data_resolves_symbol_for_yfinance_disk_lookup(tmp_path):
    """When the algorithm calls market_data with BTC/USD and source=yfinance,
    the bars cache + disk lookup should use BTC-USD (the yfinance form).

    Uses a mock data_service to verify that the resolved symbol (BTC-USD) is
    what reaches the disk lookup — testing the wiring, not the timestamp filter.
    """
    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=100, freq="D"),
        "open": [100.0] * 100, "high": [101.0] * 100, "low": [99.0] * 100,
        "close": [100.0 + i for i in range(100)], "volume": [1000.0] * 100,
    })

    # Capture what symbol is passed to load_market_data
    loaded_symbols = []

    def _load(src, sym, tf):
        loaded_symbols.append(sym)
        return df

    mock_ds = type("DS", (), {
        "load_market_data": lambda self, s, sym, tf: _load(s, sym, tf)
    })()

    ctx = BacktestTickContext(bars={}, positions={}, cash=1000.0, data_service=mock_ds)
    ctx.set_sim_time(datetime(2024, 4, 1, tzinfo=timezone.utc))

    # Algorithm-canonical form: BTC/USD (Alpaca-spot convention)
    ctx.market_data("BTC/USD", timeframe="1day", bars=30, source="yfinance")

    # The disk lookup should use the yfinance-specific form (BTC-USD), not BTC/USD
    assert loaded_symbols == ["BTC-USD"], (
        f"Expected disk lookup with 'BTC-USD', got {loaded_symbols}"
    )
    # And the bars cache should be keyed by the resolved symbol
    assert ("yfinance", "BTC-USD", "1day") in ctx._bars


def test_market_data_equity_unchanged(tmp_path):
    """Equity symbols pass through unchanged — no normalization for AAPL etc."""
    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=50, freq="D"),
        "open": [100.0] * 50, "high": [101.0] * 50, "low": [99.0] * 50,
        "close": [100.0 + i for i in range(50)], "volume": [1000.0] * 50,
    })

    loaded_symbols = []

    def _load(src, sym, tf):
        loaded_symbols.append(sym)
        return df

    mock_ds = type("DS", (), {
        "load_market_data": lambda self, s, sym, tf: _load(s, sym, tf)
    })()

    ctx = BacktestTickContext(bars={}, positions={}, cash=1000.0, data_service=mock_ds)
    ctx.set_sim_time(datetime(2024, 2, 15, tzinfo=timezone.utc))

    ctx.market_data("AAPL", timeframe="1day", bars=10, source="polygon")

    # Equity symbol must not be transformed
    assert loaded_symbols == ["AAPL"], (
        f"Expected disk lookup with 'AAPL' unchanged, got {loaded_symbols}"
    )
    assert ("polygon", "AAPL", "1day") in ctx._bars
