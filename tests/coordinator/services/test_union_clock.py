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


# ---- Integration test: synthetic clock rebuild ----

from coordinator.services.backtest_engine_v2 import (
    BacktestEngine, CancelToken, EngineObserver, EngineSummary, FillRecord,
)
from coordinator.services.backtest_tick_context import BacktestTickContext
from coordinator.services.backtest_config import SlippageModel
from sdk.signals import Signal, SignalLeg, SignalType, OrderType

class _RecordingObserver:
    def __init__(self):
        self.ticks = []
        self.fills = []
        self.equity = []
        self.complete = False
        self.error = None
    def on_tick(self, sim_time, ctx_snapshot): self.ticks.append(sim_time)
    def on_signals_emitted(self, sim_time, signals): pass
    def on_fill(self, fill): self.fills.append(fill)
    def on_signal_rejected(self, sim_time, signal, reason): pass
    def on_equity_point(self, sim_time, pv, cash, positions): self.equity.append({"pv": pv, "cash": cash})
    def on_complete(self, summary): self.complete = True; self.summary = summary
    def on_error(self, exc): self.error = exc

class _DynamicLoadAlgo:
    """Algo that dynamically loads SPY data on first tick, then buys."""
    def __init__(self): self._loaded = False; self._bought = False
    def on_start(self, config, restored_state): pass
    def on_tick(self, ctx):
        if not self._loaded:
            ctx.market_data("SPY", "1day", 5)
            self._loaded = True
            return []
        if not self._bought:
            self._bought = True
            return [Signal(legs=[SignalLeg(
                symbol="SPY", signal_type=SignalType.BUY, quantity=10,
                asset_type="equities", order_type=OrderType.MARKET,
            )])]
        return []
    def on_stop(self): return {}
    def save_state(self): return {}

def test_synthetic_clock_rebuilt_after_first_tick():
    """When clock is synthetic (all zeros), engine rebuilds from loaded symbols after first tick."""
    import numpy as np
    # Synthetic clock (all zeros) — like a scraper-only algo
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    synthetic_clock = pd.DataFrame({
        "timestamp": dates,
        "open": np.zeros(5), "high": np.zeros(5),
        "low": np.zeros(5), "close": np.zeros(5),
        "volume": np.zeros(5),
    })

    # Real SPY data that the algo will load dynamically
    spy_bars = _make_bars("SPY", ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])

    ctx = BacktestTickContext(
        bars={("polygon", "SPY", "1day"): spy_bars},
        positions={}, cash=100_000.0,
    )
    obs = _RecordingObserver()
    BacktestEngine().run(
        algorithm=_DynamicLoadAlgo(), ctx=ctx, clock_series=synthetic_clock,
        clock_timeframe="1day", clock_source="synthetic", clock_symbol="_clock",
        slippage=SlippageModel(), buy_fees=[], sell_fees=[],
        initial_cash=100_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert obs.complete
    assert obs.error is None
    # Should have fills at real prices (not $0)
    assert len(obs.fills) > 0
    assert obs.fills[0].fill_price > 0


class _BuyOnFirstTickAlgo:
    """Loads SPY dynamically, then buys on second tick."""
    def __init__(self): self._tick = 0
    def on_start(self, config, restored_state): pass
    def on_tick(self, ctx):
        self._tick += 1
        if self._tick == 1:
            ctx.market_data("SPY", "1day", bars=5)
            return []
        if self._tick == 2:
            return [Signal(legs=[SignalLeg(
                symbol="SPY", signal_type=SignalType.BUY, quantity=10,
                asset_type="equities", order_type=OrderType.MARKET,
            )])]
        return []
    def on_stop(self): return {}
    def save_state(self): return {}


def test_no_zero_price_fills_with_dynamic_load():
    """Fills must use real prices even when the clock started synthetic."""
    import numpy as np
    dates = pd.date_range("2024-01-01", periods=10, freq="B")
    synthetic_clock = pd.DataFrame({
        "timestamp": dates,
        "open": np.zeros(10), "high": np.zeros(10),
        "low": np.zeros(10), "close": np.zeros(10),
        "volume": np.zeros(10),
    })
    spy_bars = _make_bars("SPY", dates, price_base=450.0)
    ctx = BacktestTickContext(
        bars={("polygon", "SPY", "1day"): spy_bars},
        positions={}, cash=100_000.0,
    )
    obs = _RecordingObserver()
    BacktestEngine().run(
        algorithm=_BuyOnFirstTickAlgo(), ctx=ctx,
        clock_series=synthetic_clock,
        clock_timeframe="1day", clock_source="synthetic", clock_symbol="_clock",
        slippage=SlippageModel(market_bps=0),
        buy_fees=[], sell_fees=[],
        initial_cash=100_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert obs.complete
    assert len(obs.fills) == 1
    assert obs.fills[0].fill_price > 1.0
