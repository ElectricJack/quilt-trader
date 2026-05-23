"""Tests for BacktestEngine. These are correctness-critical — if any
regress, the engine is broken at its core. See Spec D §9."""
import pytest
import pandas as pd
from dataclasses import dataclass
from datetime import datetime, timezone
from coordinator.services.backtest_engine_v2 import (
    BacktestEngine, EngineObserver, FillRecord, EngineSummary, CancelToken,
)
from coordinator.services.backtest_tick_context import BacktestTickContext
from coordinator.services.backtest_config import TradingFee, SlippageModel


class RecordingObserver:
    def __init__(self):
        self.signals = []
        self.fills = []
        self.equity = []
        self.complete = False
        self.error = None
        self.rejected = []

    def on_tick(self, sim_time, ctx_snapshot): pass
    def on_signals_emitted(self, sim_time, signals): self.signals.append((sim_time, signals))
    def on_fill(self, fill): self.fills.append(fill)
    def on_signal_rejected(self, sim_time, signal, reason): self.rejected.append((sim_time, signal, reason))
    def on_equity_point(self, sim_time, portfolio_value, cash, positions):
        self.equity.append({"sim_time": sim_time, "pv": portfolio_value, "cash": cash, "positions": positions})
    def on_complete(self, summary): self.complete = True
    def on_error(self, exc): self.error = exc


def _bars(start, n, opens=None, highs=None, lows=None, closes=None, vols=None):
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=n, freq="D", tz="UTC"),
        "open":  opens or [100.0]*n,
        "high":  highs or [101.0]*n,
        "low":   lows or [99.0]*n,
        "close": closes or [100.5]*n,
        "volume": vols or [1_000_000]*n,
    })


class _BuyOnceAlgo:
    """Algorithm that emits a single market BUY on its first tick, then nothing."""
    def __init__(self):
        self._fired = False
    def on_start(self, config, restored_state): pass
    def on_tick(self, ctx):
        from sdk.signals import Signal, SignalLeg, SignalType, OrderType
        if self._fired:
            return []
        self._fired = True
        return [Signal(legs=[SignalLeg(
            symbol="SPY", signal_type=SignalType.BUY, quantity=1,
            asset_type="equities", order_type=OrderType.MARKET,
        )])]
    def on_stop(self): return {}
    def save_state(self): return {}


def test_market_order_fills_at_next_bar_open_with_slippage_never_signal_bar():
    """Spec D §9.3. A signal on bar T fills at bar T+1's open + slippage. Never bar T."""
    clock = _bars("2024-01-01", 5, opens=[100, 102, 105, 110, 115])
    ctx = BacktestTickContext(
        bars={("polygon", "SPY", "1day"): clock},
        positions={}, cash=10_000.0,
    )
    obs = RecordingObserver()
    engine = BacktestEngine()
    engine.run(
        algorithm=_BuyOnceAlgo(),
        ctx=ctx,
        clock_series=clock,
        clock_timeframe="1day",
        clock_source="polygon",
        clock_symbol="SPY",
        slippage=SlippageModel(market_bps=5.0),
        buy_fees=[], sell_fees=[],
        initial_cash=10_000.0,
        observer=obs,
        cancel_token=CancelToken(),
    )
    # Signal emitted at end of bar 0 (sim_time = bar 0 close = 2024-01-02 00:00 UTC).
    # Fill MUST be at bar 1's open = 102.0 + 5bps = 102.051.
    assert len(obs.fills) == 1
    fill = obs.fills[0]
    assert fill.symbol == "SPY"
    assert fill.timestamp == pd.Timestamp("2024-01-02", tz="UTC")  # bar 1's timestamp
    expected_price = 102.0 * (1 + 5/10000)
    assert fill.fill_price == pytest.approx(expected_price, abs=1e-6)
    assert fill.requested_price == pytest.approx(102.0, abs=1e-6)


def test_limit_order_requires_strict_cross():
    """Spec D §9.5. Buy limit at $100: low=100 → no fill; low=99.99 → fill; low=100.01 → no fill."""
    from sdk.signals import Signal, SignalLeg, SignalType, OrderType

    class LimitAlgo:
        def __init__(self, limit_price): self.limit_price = limit_price; self._fired = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            if self._fired: return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="SPY", signal_type=SignalType.BUY, quantity=1,
                asset_type="equities", order_type=OrderType.LIMIT, limit_price=self.limit_price,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    def _run(next_bar_low, limit=100.0):
        clock = _bars("2024-01-01", 3, lows=[99.0, next_bar_low, 99.0],
                      highs=[101.0]*3, opens=[100.0]*3, closes=[100.5]*3)
        ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): clock}, positions={}, cash=10_000.0)
        obs = RecordingObserver()
        BacktestEngine().run(
            algorithm=LimitAlgo(limit_price=limit), ctx=ctx, clock_series=clock,
            clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
            slippage=SlippageModel(), buy_fees=[], sell_fees=[],
            initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
        )
        return obs

    obs_exact = _run(next_bar_low=100.0)
    assert len(obs_exact.fills) == 0  # Exact touch — no fill

    obs_cross = _run(next_bar_low=99.99)
    assert len(obs_cross.fills) == 1  # Strict cross — fills
    assert obs_cross.fills[0].fill_price == pytest.approx(100.0, abs=1e-6)

    obs_no_cross = _run(next_bar_low=100.01)
    assert len(obs_no_cross.fills) == 0  # Didn't cross


def test_fee_recorded_with_breakdown():
    clock = _bars("2024-01-01", 3, opens=[100.0]*3)
    ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): clock}, positions={}, cash=10_000.0)
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=_BuyOnceAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(market_bps=0),  # No slippage so fees easy to verify
        buy_fees=[TradingFee(flat_fee=1.0, percent_fee=0.001, taker=True)],
        sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
    )
    fill = obs.fills[0]
    # Fee = flat_fee(1.0) + percent_fee(0.001) * 100.0 * 1.0 = 1.1
    assert fill.fees == pytest.approx(1.1, abs=1e-6)
    assert len(fill.fee_breakdown) == 1


def test_no_path_to_same_bar_fill():
    """Spec D §9.4. Regression guard — pending orders are processed AFTER on_tick returns and AGAINST the next iteration."""
    clock = _bars("2024-01-01", 5, opens=[100, 102, 105, 110, 115])
    ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): clock}, positions={}, cash=10_000.0)
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=_BuyOnceAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(market_bps=5.0), buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
    )
    # Signal emitted while sim_time = end of bar 0. Fill MUST NOT have a timestamp <= bar 0.
    assert obs.fills[0].timestamp > pd.Timestamp("2024-01-01 23:59:59", tz="UTC")
    assert obs.fills[0].timestamp >= pd.Timestamp("2024-01-02", tz="UTC")


def test_options_leg_fails_run_cleanly():
    """Spec D §9.11. A signal with asset_type=options halts the run cleanly."""
    from sdk.signals import Signal, SignalLeg, SignalType, OrderType

    class OptionsAlgo:
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            return [Signal(legs=[SignalLeg(
                symbol="SPY240620C00500000", signal_type=SignalType.BUY, quantity=1,
                asset_type="options", order_type=OrderType.MARKET,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _bars("2024-01-01", 3, opens=[100.0]*3)
    ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): clock}, positions={}, cash=10_000.0)
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=OptionsAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(), buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert obs.error is not None
    assert "options" in str(obs.error).lower()


def test_cancel_token_stops_engine_cleanly():
    cancel = CancelToken()
    cancel.set()  # Pre-cancelled
    clock = _bars("2024-01-01", 100)
    ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): clock}, positions={}, cash=10_000.0)
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=_BuyOnceAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(), buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=cancel,
    )
    # Engine should exit early with no fills (cancel was set before run started)
    assert not obs.complete
    assert len(obs.fills) == 0


def test_ioc_order_expires_after_one_bar():
    """IOC limit order that doesn't cross on the fill bar is rejected immediately."""
    from sdk.signals import Signal, SignalLeg, SignalType, OrderType, TimeInForce

    class IOCAlgo:
        def __init__(self): self._fired = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            if self._fired: return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="SPY", signal_type=SignalType.BUY, quantity=1,
                order_type=OrderType.LIMIT, limit_price=90.0,
                time_in_force=TimeInForce.IOC,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _bars("2024-01-01", 5, lows=[99.0]*5, highs=[101.0]*5)
    ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): clock}, positions={}, cash=10_000.0)
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=IOCAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(), buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert len(obs.fills) == 0
    assert len(obs.rejected) == 1
    assert "no_fill_within_timeout" in obs.rejected[0][2]


def test_day_order_expires_at_day_boundary():
    """DAY limit order placed on Jan 2 that doesn't fill same-day expires
    before Jan 3 bars are evaluated — even though Jan 3 would cross the limit."""
    from sdk.signals import Signal, SignalLeg, SignalType, OrderType, TimeInForce
    clock = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2024-01-02 10:00", "2024-01-02 11:00",
            "2024-01-02 12:00",
            "2024-01-03 10:00", "2024-01-03 11:00",
        ]).tz_localize("UTC"),
        "open": [100.0]*5, "high": [101.0]*5,
        # Same-day lows stay above limit; next-day lows would cross it
        "low": [99.5, 99.5, 99.5, 98.0, 98.0],
        "close": [100.0]*5, "volume": [1e6]*5,
    })
    class DayLimitAlgo:
        def __init__(self): self._fired = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            if self._fired: return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="SPY", signal_type=SignalType.BUY, quantity=1,
                order_type=OrderType.LIMIT, limit_price=99.0,
                time_in_force=TimeInForce.DAY,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): clock}, positions={}, cash=10_000.0)
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=DayLimitAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1hour", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(), buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
    )
    # Signal on bar 0 (Jan 2 10:00). Bars 1-2 (Jan 2) low=99.5 > limit 99.0, no cross.
    # DAY order expires when clock crosses to Jan 3 — should NOT fill on Jan 3 bars.
    assert len(obs.fills) == 0
    assert any("day_expired" in r[2] for r in obs.rejected)


def test_gtc_order_fills_across_days():
    """GTC limit order persists across multiple bars until the limit is crossed."""
    from sdk.signals import Signal, SignalLeg, SignalType, OrderType, TimeInForce

    class GTCAlgo:
        def __init__(self): self._fired = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            if self._fired: return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="SPY", signal_type=SignalType.BUY, quantity=1,
                order_type=OrderType.LIMIT, limit_price=95.0,
                time_in_force=TimeInForce.GTC,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _bars("2024-01-01", 6, opens=[100]*6, highs=[101]*6,
                  lows=[99, 99, 99, 99, 94, 99], closes=[100]*6)
    ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): clock}, positions={}, cash=10_000.0)
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=GTCAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(), buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert len(obs.fills) == 1
    assert obs.fills[0].fill_price == pytest.approx(95.0, abs=1e-6)


def test_gtc_order_persists_until_end_if_never_crossed():
    """GTC order that never fills is rejected at end of backtest with gtc_expired reason."""
    from sdk.signals import Signal, SignalLeg, SignalType, OrderType, TimeInForce

    class GTCNeverFillAlgo:
        def __init__(self): self._fired = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            if self._fired: return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="SPY", signal_type=SignalType.BUY, quantity=1,
                order_type=OrderType.LIMIT, limit_price=50.0,
                time_in_force=TimeInForce.GTC,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _bars("2024-01-01", 5)
    ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): clock}, positions={}, cash=10_000.0)
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=GTCNeverFillAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(), buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert len(obs.fills) == 0
    assert any("gtc_expired" in r[2] for r in obs.rejected)


def test_algorithm_can_cancel_gtc_order():
    from sdk.signals import Signal, SignalLeg, SignalType, OrderType, TimeInForce

    class CancelAlgo:
        def __init__(self): self._step = 0
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            self._step += 1
            if self._step == 1:
                return [Signal(legs=[SignalLeg(
                    symbol="SPY", signal_type=SignalType.BUY, quantity=1,
                    order_type=OrderType.LIMIT, limit_price=95.0,
                    time_in_force=TimeInForce.GTC,
                )])]
            if self._step == 3:
                ctx.cancel_all_orders()
            return []
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _bars("2024-01-01", 6, lows=[99]*6)  # Never crosses 95
    ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): clock}, positions={}, cash=10_000.0)
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=CancelAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(), buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert len(obs.fills) == 0
    # Should be cancelled, not gtc_expired
    assert any("cancelled" in r[2] for r in obs.rejected)
