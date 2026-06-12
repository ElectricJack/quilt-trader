"""Verify BacktestRunner.run consumes a provided BacktestBarsCache."""
from __future__ import annotations

from datetime import date
import pandas as pd
import pytest

from coordinator.services.validation.bars_cache import BacktestBarsCache


def _df(n: int = 50) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
        index=idx,
    )


@pytest.mark.asyncio
async def test_runner_skips_disk_load_when_cache_has_required_bars(monkeypatch):
    """When bars_cache contains the required (source, symbol, timeframe), the
    runner does NOT call _load_bar_series for that key."""
    from coordinator.services import backtest_runner as br_mod

    cache = BacktestBarsCache()
    cache._bars[("polygon", "SPY", "1day")] = _df()

    disk_calls: list = []

    def fake_load(_ds, source, symbol, timeframe):
        disk_calls.append((source, symbol, timeframe))
        return None

    monkeypatch.setattr(br_mod, "_load_bar_series", fake_load)

    # Construct a mock runner: most internals are stubbed; we only check the
    # bar-load branch is short-circuited.
    runner = br_mod.BacktestRunner.__new__(br_mod.BacktestRunner)
    runner._ds = object()
    bars_in: dict = {}

    runner._load_bars_with_cache_then_disk(
        requirements=[("polygon", "SPY", "1day"), ("polygon", "QQQ", "1day")],
        start=date(2024, 1, 1),
        end=date(2024, 1, 30),
        bars=bars_in,
        bars_cache=cache,
    )
    assert ("polygon", "SPY", "1day") in bars_in
    assert ("polygon", "QQQ", "1day") not in bars_in
    assert ("polygon", "SPY", "1day") not in disk_calls  # served from cache
    assert ("polygon", "QQQ", "1day") in disk_calls       # fell through to disk
