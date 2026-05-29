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


"""Pass-1 must populate ctx._bars without firing observers; pass-2 fires
observers exactly once per real-clock bar."""


class _RecordingObserver:
    """Captures every event for assertion."""
    def __init__(self):
        self.events = []
    def on_tick(self, *a, **k): self.events.append(("tick", a, k))
    def on_signals_emitted(self, *a, **k): self.events.append(("sig", a, k))
    def on_signal_rejected(self, *a, **k): self.events.append(("rej", a, k))
    def on_fill(self, *a, **k): self.events.append(("fill", a, k))
    def on_error(self, *a, **k): self.events.append(("err", a, k))
    def on_summary(self, *a, **k): self.events.append(("sum", a, k))
    def on_equity_point(self, *a, **k): pass
    def on_complete(self, *a, **k): self.events.append(("complete", a, k))


class _DiscoveryAlgo:
    """Touches ETH on its first tick so pass-1 discovery sees the symbol."""
    def on_start(self, config, restored_state):
        self._calls = 0

    def on_tick(self, ctx):
        self._calls += 1
        if self._calls == 1:
            try:
                ctx.market_data("BTC/USD", n=1)
            except Exception:
                pass
        return []

    def on_stop(self): pass


def test_two_pass_execution_does_not_double_call_observer():
    """Pass 1 must NOT fire observers; pass-2 fires observer.on_tick exactly
    len(real union clock) times — NOT 1 (synthetic) + N (real)."""
    from coordinator.services.backtest_engine_v2 import BacktestEngine
    from coordinator.services.backtest_tick_context import BacktestTickContext
    from coordinator.services.backtest_config import BacktestConfig, SlippageModel

    bars = {
        ("yfinance", "BTC-USD", "1day"): _make_bar_df(
            ["2024-01-01", "2024-01-02", "2024-01-03"], base=100.0,
        ),
    }
    ctx = BacktestTickContext(
        bars=dict(bars), positions={}, cash=10_000.0,
        default_source="yfinance",
    )
    obs = _RecordingObserver()
    eng = BacktestEngine(config=BacktestConfig(
        start="2024-01-01", end="2024-01-03",
        initial_cash=10_000.0, cost_profile=None,
    ))

    # Pass a synthetic clock — engine should rebuild on pass-2 using the union.
    synthetic_clock = _make_bar_df(["2024-01-01"], base=0.0)
    eng.run(
        algorithm=_DiscoveryAlgo(),
        ctx=ctx,
        clock_series=synthetic_clock,
        clock_timeframe="1day",
        clock_source="synthetic",
        clock_symbol="_clock",
        slippage=SlippageModel(),
        buy_fees=[],
        sell_fees=[],
        initial_cash=10_000.0,
        observer=obs,
        cancel_token=type("X", (), {"is_set": lambda self: False})(),
    )

    tick_events = [e for e in obs.events if e[0] == "tick"]
    # Pass-2 over the real 3-bar union clock — exactly 3 tick events
    # (NOT 1 synthetic + 3 real = 4, and NOT 6 if both passes fired).
    assert len(tick_events) == 3, (
        f"expected 3 ticks (pass-2 over real union clock), got {len(tick_events)}"
    )


def test_two_pass_falls_back_to_original_clock_when_no_symbols():
    """A scraper-only algo with no market_data() calls produces an empty
    ctx._bars after pass 1. The engine must fall back to running pass 2 on
    the originally-supplied clock_series (not crash)."""
    from coordinator.services.backtest_engine_v2 import BacktestEngine
    from coordinator.services.backtest_tick_context import BacktestTickContext
    from coordinator.services.backtest_config import BacktestConfig, SlippageModel

    class _ScraperOnly:
        def on_start(self, *a, **k): pass
        def on_tick(self, ctx): return []
        def on_stop(self): pass

    ctx = BacktestTickContext(
        bars={}, positions={}, cash=10_000.0, default_source="polygon",
    )
    obs = _RecordingObserver()
    eng = BacktestEngine(config=BacktestConfig(
        start="2024-01-01", end="2024-01-03",
        initial_cash=10_000.0, cost_profile=None,
    ))
    synthetic_clock = _make_bar_df(
        ["2024-01-01", "2024-01-02", "2024-01-03"], base=0.0,
    )
    eng.run(
        algorithm=_ScraperOnly(),
        ctx=ctx,
        clock_series=synthetic_clock,
        clock_timeframe="1day",
        clock_source="synthetic",
        clock_symbol="_clock",
        slippage=SlippageModel(),
        buy_fees=[],
        sell_fees=[],
        initial_cash=10_000.0,
        observer=obs,
        cancel_token=type("X", (), {"is_set": lambda self: False})(),
    )

    # Pass-2 fell back to synthetic_clock — 3 ticks.
    tick_events = [e for e in obs.events if e[0] == "tick"]
    assert len(tick_events) == 3


def test_lookup_symbol_close_returns_symbols_own_close_not_clock_bar():
    """Regression test for the 2026-05-27 ETH-at-BTC-price bug. When asked for
    ETH's MtM price at a timestamp where ETH has a bar, the engine must return
    ETH's close — not the clock bar's close (which may be BTC)."""
    from coordinator.services.backtest_engine_v2 import BacktestEngine
    from coordinator.services.backtest_tick_context import BacktestTickContext
    from coordinator.services.backtest_config import BacktestConfig
    from coordinator.services.asset_services.registry import AssetServiceRegistry

    bars = {
        ("yfinance", "BTC-USD", "1day"): _make_bar_df(
            ["2024-01-01", "2024-01-02"], base=42_000.0,
        ),
        ("yfinance", "ETH-USD", "1day"): _make_bar_df(
            ["2024-01-01", "2024-01-02"], base=2_500.0,
        ),
    }
    ctx = BacktestTickContext(
        bars=dict(bars), positions={}, cash=10_000.0,
        default_source="yfinance",
    )
    eng = BacktestEngine(config=BacktestConfig(
        start="2024-01-01", end="2024-01-02",
        initial_cash=10_000.0, cost_profile=None,
    ))
    # The engine normally initialises these on entry to _run_internal; in this
    # micro-test we drive _lookup_symbol_close directly, so set them up:
    eng._asset_registry = AssetServiceRegistry()
    eng._ts_cache = {}

    btc_bar = bars[("yfinance", "BTC-USD", "1day")].iloc[1]
    price = eng._lookup_symbol_close(
        sym="ETH/USD",
        sim_time=btc_bar["timestamp"].to_pydatetime(),
        ctx=ctx,
        fallback_bar=btc_bar,  # the CLOCK bar — used to be the bug source
    )
    # Pre-fix: returned ≈42000 (BTC close). Post-fix: ≈2500 (ETH close).
    assert 2_400 < price < 2_600, f"expected ETH close ~2500, got {price}"


def test_lookup_symbol_close_returns_zero_for_unknown_symbol():
    """If a symbol has no cache entry, the lookup must return 0.0 so callers
    can detect 'no mark available' and fall back to cost basis. It must NOT
    return the clock bar's close (which would be a different symbol's price)."""
    from coordinator.services.backtest_engine_v2 import BacktestEngine
    from coordinator.services.backtest_tick_context import BacktestTickContext
    from coordinator.services.backtest_config import BacktestConfig
    from coordinator.services.asset_services.registry import AssetServiceRegistry

    bars = {
        ("yfinance", "BTC-USD", "1day"): _make_bar_df(
            ["2024-01-01"], base=42_000.0,
        ),
    }
    ctx = BacktestTickContext(
        bars=dict(bars), positions={}, cash=10_000.0,
        default_source="yfinance",
    )
    eng = BacktestEngine(config=BacktestConfig(
        start="2024-01-01", end="2024-01-02",
        initial_cash=10_000.0, cost_profile=None,
    ))
    eng._asset_registry = AssetServiceRegistry()
    eng._ts_cache = {}

    btc_bar = bars[("yfinance", "BTC-USD", "1day")].iloc[0]
    price = eng._lookup_symbol_close(
        sym="ETH/USD",  # not in cache
        sim_time=btc_bar["timestamp"].to_pydatetime(),
        ctx=ctx,
        fallback_bar=btc_bar,
    )
    assert price == 0.0, f"expected 0.0 sentinel, got {price}"
