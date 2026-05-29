"""Two-pass execution + union-of-symbol-timelines clock tests.

C1 prep: pin the existing `_build_union_clock` static helper so subsequent
P3 refactors (C2-C5) don't accidentally regress its contract.  Additional
tests in C3-C7 build on these primitives.
"""
from __future__ import annotations

import pandas as pd

from coordinator.services.backtest_engine_v2 import BacktestEngine


def _make_bar_df(timestamps, base=100.0):
    n = len(timestamps)
    return pd.DataFrame({
        "timestamp": pd.to_datetime(timestamps),
        "open": [base] * n,
        "high": [base * 1.01] * n,
        "low": [base * 0.99] * n,
        "close": [base] * n,
        "volume": [1.0] * n,
    })


def test_build_union_clock_merges_all_symbol_timelines():
    """The union clock includes every distinct timestamp from every symbol,
    deduplicated and sorted ascending."""
    bars = {
        ("yfinance", "BTC-USD", "1day"): _make_bar_df(
            ["2024-01-01", "2024-01-02", "2024-01-03"], base=100.0,
        ),
        ("yfinance", "ETH-USD", "1day"): _make_bar_df(
            ["2024-01-02", "2024-01-03", "2024-01-04"], base=200.0,
        ),
    }
    clock = BacktestEngine._build_union_clock(bars)
    assert list(clock["timestamp"].dt.strftime("%Y-%m-%d")) == [
        "2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04",
    ]
    # OHLCV present and non-zero — never the all-zero synthetic-clock fallback.
    assert (clock["close"] > 0).all()


def test_build_union_clock_empty_bars_returns_columned_empty_df():
    """No cache entries → empty DataFrame with the expected columns."""
    clock = BacktestEngine._build_union_clock({})
    assert list(clock.columns) == [
        "timestamp", "open", "high", "low", "close", "volume",
    ]
    assert len(clock) == 0
