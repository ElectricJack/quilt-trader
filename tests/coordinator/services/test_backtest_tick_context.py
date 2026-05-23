import pytest
import pandas as pd
from datetime import datetime, date, timezone
from coordinator.services.backtest_tick_context import BacktestTickContext, timeframe_to_seconds
from sdk.models import OptionChain, OptionContract


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


# ---- auto-download / on-miss tests ----

def _make_daily_naive(start, days):
    """Like _make_daily but with tz-naive timestamps (as stored on disk)."""
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=days, freq="D"),  # tz-naive
        "open": [100.0 + i for i in range(days)],
        "high": [101.0 + i for i in range(days)],
        "low":  [ 99.0 + i for i in range(days)],
        "close":[100.5 + i for i in range(days)],
        "volume": [1_000_000] * days,
    })


def test_market_data_loads_from_disk_on_miss():
    """When symbol is missing from pre-loaded bars, data_service.load_market_data is used."""
    disk_df = _make_daily_naive("2026-01-01", 30)

    mock_ds = type("DS", (), {
        "load_market_data": lambda self, src, sym, tf: disk_df
    })()

    ctx = BacktestTickContext(
        bars={},  # empty — nothing pre-loaded
        positions={}, cash=100_000.0,
        data_service=mock_ds,
    )
    ctx.set_sim_time(datetime(2026, 1, 15, 14, 30, tzinfo=timezone.utc))
    out = ctx.market_data("AAPL", timeframe="1day", bars=100, source="polygon")

    assert not out.empty, "Expected rows loaded from disk"
    # No-look-ahead still applies: 2026-01-15 must not appear
    assert pd.Timestamp("2026-01-15") not in out["timestamp"].values


def test_market_data_caches_disk_result_in_bars():
    """After the first disk load the data is stored in _bars so subsequent
    calls skip data_service entirely."""
    disk_df = _make_daily_naive("2026-01-01", 10)
    call_count = [0]

    def _load(src, sym, tf):
        call_count[0] += 1
        return disk_df

    mock_ds = type("DS", (), {"load_market_data": lambda self, s, sym, tf: _load(s, sym, tf)})()

    ctx = BacktestTickContext(bars={}, positions={}, cash=0, data_service=mock_ds)
    ctx.set_sim_time(datetime(2026, 1, 15, tzinfo=timezone.utc))

    ctx.market_data("AAPL", "1day", 10, source="polygon")
    ctx.market_data("AAPL", "1day", 10, source="polygon")  # second call

    assert call_count[0] == 1, "DataService should be called only once (result is cached in _bars)"


def test_market_data_calls_on_miss_when_not_on_disk():
    """When data_service returns None, on_miss is invoked and its result is used."""
    disk_df = _make_daily_naive("2026-01-01", 20)
    on_miss_calls = []

    def on_miss(symbol, timeframe, source):
        on_miss_calls.append((symbol, timeframe, source))
        return disk_df

    mock_ds = type("DS", (), {"load_market_data": lambda self, s, sym, tf: None})()

    ctx = BacktestTickContext(
        bars={}, positions={}, cash=0,
        data_service=mock_ds,
        on_miss=on_miss,
    )
    ctx.set_sim_time(datetime(2026, 1, 15, tzinfo=timezone.utc))
    out = ctx.market_data("TSLA", "1day", 100, source="polygon")

    assert len(on_miss_calls) == 1
    assert on_miss_calls[0] == ("TSLA", "1day", "polygon")
    assert not out.empty


def test_market_data_on_miss_not_called_when_disk_has_data():
    """on_miss must NOT be called if data_service already returns data from disk."""
    disk_df = _make_daily_naive("2026-01-01", 20)
    on_miss_calls = []

    mock_ds = type("DS", (), {"load_market_data": lambda self, s, sym, tf: disk_df})()

    ctx = BacktestTickContext(
        bars={}, positions={}, cash=0,
        data_service=mock_ds,
        on_miss=lambda sym, tf, src: on_miss_calls.append(1) or None,
    )
    ctx.set_sim_time(datetime(2026, 1, 15, tzinfo=timezone.utc))
    ctx.market_data("MSFT", "1day", 100, source="polygon")

    assert len(on_miss_calls) == 0, "on_miss should be skipped when disk has data"


def test_market_data_on_miss_exception_returns_empty():
    """If on_miss raises an exception, market_data returns an empty DataFrame gracefully."""
    def bad_on_miss(symbol, timeframe, source):
        raise RuntimeError("network error")

    mock_ds = type("DS", (), {"load_market_data": lambda self, s, sym, tf: None})()

    ctx = BacktestTickContext(
        bars={}, positions={}, cash=0,
        data_service=mock_ds,
        on_miss=bad_on_miss,
    )
    ctx.set_sim_time(datetime(2026, 1, 15, tzinfo=timezone.utc))
    out = ctx.market_data("BAD", "1day", 100, source="polygon")

    assert out.empty
    assert list(out.columns) == ["timestamp", "open", "high", "low", "close", "volume"]


def test_market_data_default_source_falls_back_to_polygon():
    """When no source and no pre-loaded key hint, the default provider is 'polygon'."""
    disk_df = _make_daily_naive("2026-01-01", 10)
    loaded_sources = []

    def _load(src, sym, tf):
        loaded_sources.append(src)
        return disk_df if src == "polygon" else None

    mock_ds = type("DS", (), {"load_market_data": lambda self, s, sym, tf: _load(s, sym, tf)})()

    ctx = BacktestTickContext(bars={}, positions={}, cash=0, data_service=mock_ds)
    ctx.set_sim_time(datetime(2026, 1, 15, tzinfo=timezone.utc))
    out = ctx.market_data("SPY", "1day", 10)  # no source= kwarg

    assert "polygon" in loaded_sources
    assert not out.empty


# ---- option_chain tests ----

def _make_mock_data_service_with_chains():
    chains = {
        ("polygon", "SPY", date(2026, 1, 17)): pd.DataFrame([
            {"ticker": "O:SPY260117C00450000", "strike": 450.0, "option_type": "call",
             "bid": 5.1, "ask": 5.3, "last": 5.2, "volume": 1200,
             "open_interest": 8000, "implied_volatility": 0.25},
            {"ticker": "O:SPY260117P00450000", "strike": 450.0, "option_type": "put",
             "bid": 4.1, "ask": 4.3, "last": 4.2, "volume": 900,
             "open_interest": 6000, "implied_volatility": 0.27},
        ]),
    }
    class MockDS:
        def load_market_data(self, src, sym, tf): return None
        def load_option_chain(self, provider, symbol, expiration):
            return chains.get((provider, symbol, expiration))
        def list_option_chain_expirations(self, provider, symbol):
            return [exp for (p, s, exp) in chains if p == provider and s == symbol]
    return MockDS()


def test_option_chain_returns_populated_chain():
    ds = _make_mock_data_service_with_chains()
    ctx = BacktestTickContext(
        bars={}, positions={}, cash=100_000.0,
        data_service=ds, default_source="polygon",
    )
    ctx.set_sim_time(datetime(2026, 1, 17, 12, 0, tzinfo=timezone.utc))
    chain = ctx.option_chain("SPY", expiration=date(2026, 1, 17))
    assert isinstance(chain, OptionChain)
    assert chain.underlying == "SPY"
    assert chain.expiration == date(2026, 1, 17)
    assert len(chain.calls) == 1
    assert len(chain.puts) == 1
    assert chain.calls[0].strike == 450.0
    assert chain.calls[0].bid == 5.1


def test_option_chain_returns_empty_when_no_data():
    mock_ds = type("DS", (), {
        "load_market_data": lambda self, s, sym, tf: None,
        "load_option_chain": lambda self, p, s, e: None,
        "list_option_chain_expirations": lambda self, p, s: [],
    })()
    ctx = BacktestTickContext(
        bars={}, positions={}, cash=100_000.0,
        data_service=mock_ds, default_source="polygon",
    )
    ctx.set_sim_time(datetime(2026, 1, 15, tzinfo=timezone.utc))
    chain = ctx.option_chain("SPY", expiration=date(2026, 1, 17))
    assert isinstance(chain, OptionChain)
    assert chain.calls == []
    assert chain.puts == []
