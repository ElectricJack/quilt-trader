"""Unit tests for BacktestTickContext.reset_for_replay (C2 / P3 prep)."""
from __future__ import annotations

import pandas as pd
import pytest


def test_reset_for_replay_preserves_bars_cache_clears_tick_state():
    """Pass-1 may mutate sim_time + account; pass-2 needs a clean slate but
    must keep the bars cache so it doesn't re-download."""
    from coordinator.services.backtest_tick_context import BacktestTickContext

    bars = {
        ("yfinance", "BTC-USD", "1day"): pd.DataFrame({
            "timestamp": pd.to_datetime(["2024-01-01"]),
            "open": [1.0], "high": [1.0], "low": [1.0],
            "close": [1.0], "volume": [1.0],
        }),
    }
    ctx = BacktestTickContext(
        bars=dict(bars), positions={}, cash=10_000.0,
        default_source="yfinance",
    )

    # Simulate pass-1 mutations the engine performs.
    ctx.set_sim_time(pd.Timestamp("2024-01-15").to_pydatetime())
    ctx.update_account(
        cash=5_000.0,
        account_value=20_000.0,
        buying_power=5_000.0,
        positions={"BTC/USD": object()},
    )

    ctx.reset_for_replay()

    # Bars cache preserved (same DataFrame objects).
    assert ctx._bars == bars

    # Tick-time state cleared.
    assert ctx._sim_time_now is None

    # Account state reset to initial cash.
    assert ctx.cash == 10_000.0
    assert ctx.account_value == 10_000.0
    assert ctx.buying_power == 10_000.0
    assert ctx.positions == {}


def test_reset_for_replay_after_no_mutations_is_idempotent():
    """Calling reset before any mutations leaves the context unchanged."""
    from coordinator.services.backtest_tick_context import BacktestTickContext

    ctx = BacktestTickContext(
        bars={}, positions={}, cash=10_000.0,
        default_source="yfinance",
    )
    ctx.reset_for_replay()
    assert ctx.cash == 10_000.0
    assert ctx.account_value == 10_000.0
    assert ctx.buying_power == 10_000.0
    assert ctx.positions == {}
    assert ctx._sim_time_now is None


def test_reset_for_replay_does_not_touch_data_service():
    """data_service + on_miss + default_source survive a reset."""
    from coordinator.services.backtest_tick_context import BacktestTickContext

    class _FakeDS: ...

    ds = _FakeDS()
    on_miss = lambda *a, **k: None
    ctx = BacktestTickContext(
        bars={}, positions={}, cash=5_000.0,
        default_source="polygon",
        data_service=ds,
        on_miss=on_miss,
    )
    ctx.reset_for_replay()
    assert ctx._data_service is ds
    assert ctx._on_miss is on_miss
    assert ctx._default_source == "polygon"
