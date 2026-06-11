from datetime import date
import pandas as pd
import pytest

from coordinator.services.validation.bars_cache import BacktestBarsCache


def _df(start: str, end: str, freq: str = "D") -> pd.DataFrame:
    idx = pd.date_range(start, end, freq=freq)
    return pd.DataFrame(
        {"open": range(len(idx)), "high": range(len(idx)), "low": range(len(idx)),
         "close": range(len(idx)), "volume": range(len(idx))},
        index=idx,
    )


def test_preload_stores_each_requirement_once():
    cache = BacktestBarsCache()
    loads: list[tuple[str, str, str]] = []

    def fake_loader(source, symbol, timeframe, start, end):
        loads.append((source, symbol, timeframe))
        return _df(start, end)

    cache.preload(
        requirements=[("polygon", "SPY", "1day"), ("polygon", "QQQ", "1day")],
        start=date(2024, 1, 1),
        end=date(2024, 6, 30),
        loader=fake_loader,
    )
    assert len(loads) == 2
    assert ("polygon", "SPY", "1day") in cache._bars
    assert ("polygon", "QQQ", "1day") in cache._bars


def test_get_returns_slice_for_window():
    cache = BacktestBarsCache()
    cache._bars[("polygon", "SPY", "1day")] = _df("2024-01-01", "2024-12-31")
    slice_ = cache.get("polygon", "SPY", "1day", date(2024, 3, 1), date(2024, 3, 31))
    assert len(slice_) == 31
    assert slice_.index[0] == pd.Timestamp("2024-03-01")
    assert slice_.index[-1] == pd.Timestamp("2024-03-31")


def test_get_returns_none_when_not_preloaded():
    cache = BacktestBarsCache()
    assert cache.get("polygon", "SPY", "1day", date(2024, 1, 1), date(2024, 1, 31)) is None


def test_preload_skip_when_already_cached():
    cache = BacktestBarsCache()
    calls: list[tuple] = []

    def fake_loader(source, symbol, timeframe, start, end):
        calls.append((source, symbol, timeframe))
        return _df(start, end)

    cache.preload([("polygon", "SPY", "1day")], date(2024, 1, 1), date(2024, 12, 31), fake_loader)
    cache.preload([("polygon", "SPY", "1day")], date(2024, 1, 1), date(2024, 12, 31), fake_loader)
    assert len(calls) == 1


def test_memory_estimate_in_bytes():
    cache = BacktestBarsCache()
    cache._bars[("polygon", "SPY", "1day")] = _df("2024-01-01", "2024-12-31")
    assert cache.memory_estimate_bytes() > 0


def test_has_returns_membership_correctly():
    cache = BacktestBarsCache()
    cache._bars[("polygon", "SPY", "1day")] = _df("2024-01-01", "2024-12-31")
    assert cache.has("polygon", "SPY", "1day")
    assert not cache.has("polygon", "QQQ", "1day")
    assert not cache.has("alpaca", "SPY", "1day")


def test_get_returns_none_when_window_outside_preloaded_range():
    """Preloaded key but the requested window has no rows -> None (not empty)."""
    cache = BacktestBarsCache()
    cache._bars[("polygon", "SPY", "1day")] = _df("2024-06-01", "2024-06-30")
    # Window entirely before the preloaded range
    result = cache.get("polygon", "SPY", "1day", date(2024, 1, 1), date(2024, 1, 31))
    assert result is None
