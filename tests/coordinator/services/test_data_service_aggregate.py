import pandas as pd
import pytest

from coordinator.services.data_service import DataService


def _fixture_1min_bars():
    """6 minutes of 1m bars: easy to validate 5m aggregation."""
    return pd.DataFrame([
        {"timestamp": pd.Timestamp("2026-05-18 14:30:00", tz="UTC"),
         "open": 500.0, "high": 501.0, "low": 499.5, "close": 500.5, "volume": 100},
        {"timestamp": pd.Timestamp("2026-05-18 14:31:00", tz="UTC"),
         "open": 500.5, "high": 502.0, "low": 500.0, "close": 501.5, "volume": 200},
        {"timestamp": pd.Timestamp("2026-05-18 14:32:00", tz="UTC"),
         "open": 501.5, "high": 502.5, "low": 501.0, "close": 502.0, "volume": 150},
        {"timestamp": pd.Timestamp("2026-05-18 14:33:00", tz="UTC"),
         "open": 502.0, "high": 503.0, "low": 501.5, "close": 502.5, "volume": 175},
        {"timestamp": pd.Timestamp("2026-05-18 14:34:00", tz="UTC"),
         "open": 502.5, "high": 503.5, "low": 502.0, "close": 503.0, "volume": 125},
        {"timestamp": pd.Timestamp("2026-05-18 14:35:00", tz="UTC"),
         "open": 503.0, "high": 504.0, "low": 502.5, "close": 503.5, "volume": 100},
    ])


def test_aggregate_1min_to_5min_produces_correct_ohlcv():
    df = _fixture_1min_bars()
    out = DataService.aggregate_bars(df, "5min")
    # 6 minutes of input @ 14:30..14:35 → two 5-min buckets: 14:30 (5 bars) + 14:35 (1 bar).
    assert len(out) == 2
    first = out.iloc[0]
    assert first["open"] == 500.0          # first bar's open
    assert first["high"] == 503.5          # max of the 5 bars (14:34: 503.5)
    assert first["low"] == 499.5           # min of the 5 bars (14:30: 499.5)
    assert first["close"] == 503.0         # last bar's close in bucket (14:34: 503.0)
    assert first["volume"] == 100 + 200 + 150 + 175 + 125

    second = out.iloc[1]
    assert second["open"] == 503.0
    assert second["close"] == 503.5
    assert second["volume"] == 100


def test_aggregate_passthroughs_1min():
    df = _fixture_1min_bars()
    out = DataService.aggregate_bars(df, "1min")
    pd.testing.assert_frame_equal(out.reset_index(drop=True), df.reset_index(drop=True))
