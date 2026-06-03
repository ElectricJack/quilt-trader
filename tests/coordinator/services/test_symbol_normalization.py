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
