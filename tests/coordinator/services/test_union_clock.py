# tests/coordinator/services/test_union_clock.py
import pytest
import pandas as pd
import numpy as np
from coordinator.services.backtest_engine_v2 import BacktestEngine

def _make_bars(symbol, dates, price_base=100.0):
    n = len(dates)
    return pd.DataFrame({
        "timestamp": pd.to_datetime(dates),
        "open": [price_base + i for i in range(n)],
        "high": [price_base + i + 1 for i in range(n)],
        "low": [price_base + i - 1 for i in range(n)],
        "close": [price_base + i + 0.5 for i in range(n)],
        "volume": [1_000_000] * n,
    })

class TestBuildUnionClock:
    def test_two_symbols_merged_and_deduplicated(self):
        aapl = _make_bars("AAPL", ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])
        goog = _make_bars("GOOG", ["2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"], price_base=150.0)
        bars = {
            ("polygon", "AAPL", "1day"): aapl,
            ("polygon", "GOOG", "1day"): goog,
        }
        clock = BacktestEngine._build_union_clock(bars)
        timestamps = clock["timestamp"].tolist()
        assert len(timestamps) == 6
        assert timestamps == sorted(timestamps)
        assert (clock["close"] != 0).all()

    def test_single_symbol_returns_that_series(self):
        spy = _make_bars("SPY", ["2024-01-02", "2024-01-03", "2024-01-04"])
        bars = {("polygon", "SPY", "1day"): spy}
        clock = BacktestEngine._build_union_clock(bars)
        assert len(clock) == 3
        assert list(clock["close"]) == list(spy["close"])

    def test_empty_bars_returns_empty_dataframe(self):
        clock = BacktestEngine._build_union_clock({})
        assert len(clock) == 0
        assert list(clock.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
