import os
import tempfile

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


# ─── load_market_data derivation tests ───────────────────────────────────────


def _write_1min_parquet(tmpdir: str) -> DataService:
    """Write a 1-min parquet file and return a DataService pointing at tmpdir."""
    svc = DataService(market_data_dir=tmpdir, custom_data_dir=os.path.join(tmpdir, "custom"))
    df = _fixture_1min_bars()
    path = svc.market_data_path("polygon", "SPY", "1min")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_parquet(path, index=False)
    return svc


def test_load_market_data_exact_file_returned():
    """Requesting 1min when only 1min.parquet exists returns the file directly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = _write_1min_parquet(tmpdir)
        df = svc.load_market_data("polygon", "SPY", "1min")
        assert df is not None
        assert len(df) == 6


def test_load_market_data_5min_derived_from_1min():
    """Requesting 5min when only 1min.parquet exists returns aggregated bars."""
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = _write_1min_parquet(tmpdir)
        df = svc.load_market_data("polygon", "SPY", "5min")
        assert df is not None
        # 6 1-min bars → 2 5-min buckets (14:30-bucket + 14:35-bucket)
        assert len(df) == 2


def test_load_market_data_native_5min_file_preferred():
    """If a native 5min.parquet exists, it's returned instead of deriving from 1min."""
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = _write_1min_parquet(tmpdir)
        # Write a fake native 5min file with a distinctive shape
        native_df = pd.DataFrame([{"timestamp": pd.Timestamp("2026-05-18 14:30:00", tz="UTC"),
                                    "open": 999.0, "high": 999.0, "low": 999.0,
                                    "close": 999.0, "volume": 42}])
        path_5min = svc.market_data_path("polygon", "SPY", "5min")
        native_df.to_parquet(path_5min, index=False)

        result = svc.load_market_data("polygon", "SPY", "5min")
        assert result is not None
        assert result.iloc[0]["open"] == 999.0  # native file, not derived


def test_load_market_data_missing_returns_none():
    """Requesting a canonical symbol with no data on disk returns None."""
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = DataService(market_data_dir=tmpdir, custom_data_dir=tmpdir)
        assert svc.load_market_data("polygon", "AAPL", "1min") is None


def test_load_market_data_1hour_alias_derived():
    """Timeframe alias '1hour' derives from 1min just like '1h'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = _write_1min_parquet(tmpdir)
        df = svc.load_market_data("polygon", "SPY", "1hour")
        # All 6 bars fall within a single hour bucket
        assert df is not None
        assert len(df) == 1


def test_load_market_data_1day_alias_derived():
    """Timeframe alias '1day' derives from 1min."""
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = _write_1min_parquet(tmpdir)
        df = svc.load_market_data("polygon", "SPY", "1day")
        assert df is not None
        assert len(df) == 1
