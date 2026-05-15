import pytest
import pandas as pd
from datetime import datetime, timezone
from coordinator.services.backtest_tick_context import BacktestTickContext, timeframe_to_seconds


def _make_daily(start, days):
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=days, freq="D", tz="UTC"),
        "open": [100.0 + i for i in range(days)],
        "high": [101.0 + i for i in range(days)],
        "low":  [ 99.0 + i for i in range(days)],
        "close":[100.5 + i for i in range(days)],
        "volume": [1_000_000] * days,
    })


def test_market_data_filters_future_bars():
    daily = _make_daily("2026-01-01", 30)
    ctx = BacktestTickContext(
        bars={("polygon", "SPY", "1day"): daily},
        positions={},
        cash=100_000.0,
    )
    ctx.set_sim_time(datetime(2026, 1, 15, 14, 30, tzinfo=timezone.utc))
    out = ctx.market_data("SPY", timeframe="1day", bars=100, source="polygon")
    # Most recent fully-closed daily bar is 2026-01-14 (close = 2026-01-15 00:00).
    assert out["timestamp"].max() == pd.Timestamp("2026-01-14", tz="UTC")
    # In-progress 2026-01-15 must NOT appear.
    assert pd.Timestamp("2026-01-15", tz="UTC") not in out["timestamp"].values


def test_market_data_returns_tail():
    daily = _make_daily("2026-01-01", 30)
    ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): daily}, positions={}, cash=0)
    ctx.set_sim_time(datetime(2026, 1, 31, tzinfo=timezone.utc))
    out = ctx.market_data("SPY", timeframe="1day", bars=5, source="polygon")
    assert len(out) == 5
    # Tail = last 5 bars before sim_time
    assert out["timestamp"].max() == pd.Timestamp("2026-01-30", tz="UTC")


def test_multi_timeframe_no_lookahead():
    daily = _make_daily("2026-01-01", 30)
    minute = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-15 09:30", periods=200, freq="min", tz="UTC"),
        "open": [100.0] * 200,
        "high": [101.0] * 200,
        "low":  [ 99.0] * 200,
        "close":[100.5] * 200,
        "volume": [10_000] * 200,
    })
    ctx = BacktestTickContext(
        bars={
            ("polygon", "SPY", "1day"): daily,
            ("polygon", "SPY", "1min"): minute,
        },
        positions={}, cash=0,
    )
    # Sim time mid-day on Jan 15
    ctx.set_sim_time(datetime(2026, 1, 15, 12, 30, tzinfo=timezone.utc))
    # Daily for SPY must NOT include Jan 15 (in progress)
    daily_out = ctx.market_data("SPY", "1day", 100, source="polygon")
    assert daily_out["timestamp"].max() == pd.Timestamp("2026-01-14", tz="UTC")
    # Minute bars before sim_time are accessible
    minute_out = ctx.market_data("SPY", "1min", 100, source="polygon")
    assert minute_out["timestamp"].max() < pd.Timestamp("2026-01-15 12:30", tz="UTC") + pd.Timedelta(seconds=1)


def test_tick_timeframe_zero_duration_strict():
    """A '1tick' bar is available the instant its timestamp <= sim_time_now."""
    ticks = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-15 09:30:00", periods=5, freq="100ms", tz="UTC"),
        "open":   [100.00, 100.01, 100.00, 100.02, 100.03],
        "high":   [100.00, 100.01, 100.00, 100.02, 100.03],
        "low":    [100.00, 100.01, 100.00, 100.02, 100.03],
        "close":  [100.00, 100.01, 100.00, 100.02, 100.03],
        "volume": [100, 200, 50, 300, 150],
    })
    ctx = BacktestTickContext(bars={("polygon", "SPY", "1tick"): ticks}, positions={}, cash=0)
    sim = pd.Timestamp("2026-01-15 09:30:00.2", tz="UTC").to_pydatetime()
    ctx.set_sim_time(sim)
    out = ctx.market_data("SPY", "1tick", 10, source="polygon")
    # Ticks at 09:30:00.0, .1, .2 are all available (zero-duration; timestamp <= sim_time)
    assert len(out) == 3


def test_timeframe_to_seconds():
    assert timeframe_to_seconds("1min") == 60
    assert timeframe_to_seconds("5min") == 300
    assert timeframe_to_seconds("15min") == 900
    assert timeframe_to_seconds("1hour") == 3600
    assert timeframe_to_seconds("1day") == 86400
    assert timeframe_to_seconds("1tick") == 0
    with pytest.raises(ValueError):
        timeframe_to_seconds("invalid")
