# tests/coordinator/services/test_options_fill_fixes.py
"""Tests for sell-to-open vs sell-to-close distinction in _apply_fill.

When selling an option we don't own (sell-to-open / writing), the engine
should create a SHORT position (negative quantity) with no realized PnL.
When buying back that short (buy-to-close), it should compute realized PnL
correctly.
"""
import pytest
import pandas as pd
from datetime import date
from coordinator.services.backtest_engine_v2 import BacktestEngine, CancelToken
from coordinator.services.backtest_tick_context import BacktestTickContext
from coordinator.services.backtest_config import SlippageModel
from sdk.signals import Signal, SignalLeg, SignalType, OrderType


class RecordingObserver:
    def __init__(self):
        self.fills = []; self.equity = []; self.rejected = []
        self.complete = False; self.error = None
    def on_tick(self, st, cs): pass
    def on_signals_emitted(self, st, s): pass
    def on_fill(self, f): self.fills.append(f)
    def on_signal_rejected(self, st, s, r): self.rejected.append((st, s, r))
    def on_equity_point(self, st, pv, c, p): self.equity.append({"pv": pv, "cash": c})
    def on_complete(self, s): self.complete = True
    def on_error(self, e): self.error = e


def _make_clock(n=5, start="2025-04-01"):
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=n, freq="D", tz="UTC"),
        "open": [500.0]*n, "high": [505.0]*n, "low": [495.0]*n,
        "close": [502.0]*n, "volume": [1e6]*n,
    })


def _make_chain():
    return pd.DataFrame([
        {"symbol": "O:QQQ250516C00500000", "strike": 500.0, "option_type": "call",
         "bid": 8.00, "ask": 8.50, "last": 8.25, "volume": 100,
         "open_interest": 5000, "implied_volatility": 0.20},
        {"symbol": "O:QQQ250516P00500000", "strike": 500.0, "option_type": "put",
         "bid": 7.00, "ask": 7.50, "last": 7.25, "volume": 80,
         "open_interest": 4000, "implied_volatility": 0.22},
    ])


def test_sell_to_open_creates_short_position():
    """Selling an option we don't own should NOT produce realized PnL."""
    class SellToOpenAlgo:
        def __init__(self): self._fired = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            if self._fired: return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="O:QQQ250516C00500000",
                signal_type=SignalType.SELL, quantity=2,
                asset_type="options", order_type=OrderType.MARKET,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _make_clock()
    chain_df = _make_chain()
    class MockDS:
        def load_market_data(self, s, sym, tf): return None
        def load_option_chain(self, p, s, e): return chain_df
        def list_option_chain_expirations(self, p, s): return [date(2025, 5, 16)]

    ctx = BacktestTickContext(
        bars={("polygon", "QQQ", "1day"): clock}, positions={}, cash=100_000.0,
        data_service=MockDS(), default_source="polygon",
    )
    ctx._option_chain_cache[("polygon", "QQQ", date(2025, 5, 16))] = chain_df

    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=SellToOpenAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="QQQ",
        slippage=SlippageModel(market_bps=0), buy_fees=[], sell_fees=[],
        initial_cash=100_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert obs.error is None
    assert len(obs.fills) == 1
    fill = obs.fills[0]
    assert fill.side == "sell"
    assert fill.fill_price == pytest.approx(8.00, abs=0.01)
    # Sell-to-open should NOT produce realized PnL
    assert fill.realized_pnl is None or fill.realized_pnl == 0.0


def test_straddle_round_trip():
    """Sell at bid $8, buy back at ask $8.50 -> loss of $50 per contract."""
    class RoundTripAlgo:
        def __init__(self): self._step = 0
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            self._step += 1
            if self._step == 1:
                return [Signal(legs=[SignalLeg(
                    symbol="O:QQQ250516C00500000",
                    signal_type=SignalType.SELL, quantity=1,
                    asset_type="options", order_type=OrderType.MARKET,
                )])]
            if self._step == 3:
                return [Signal(legs=[SignalLeg(
                    symbol="O:QQQ250516C00500000",
                    signal_type=SignalType.BUY, quantity=1,
                    asset_type="options", order_type=OrderType.MARKET,
                )])]
            return []
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _make_clock()
    chain_df = _make_chain()
    class MockDS:
        def load_market_data(self, s, sym, tf): return None
        def load_option_chain(self, p, s, e): return chain_df
        def list_option_chain_expirations(self, p, s): return [date(2025, 5, 16)]

    ctx = BacktestTickContext(
        bars={("polygon", "QQQ", "1day"): clock}, positions={}, cash=100_000.0,
        data_service=MockDS(), default_source="polygon",
    )
    ctx._option_chain_cache[("polygon", "QQQ", date(2025, 5, 16))] = chain_df

    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=RoundTripAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="QQQ",
        slippage=SlippageModel(market_bps=0), buy_fees=[], sell_fees=[],
        initial_cash=100_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert obs.error is None
    assert len(obs.fills) == 2
    sell_fill = obs.fills[0]
    buy_fill = obs.fills[1]
    assert sell_fill.realized_pnl is None  # sell-to-open
    assert buy_fill.realized_pnl == pytest.approx(-50.0, abs=1.0)  # (8.00 - 8.50) * 1 * 100


def test_options_fill_never_uses_equity_price():
    """When option chain lookup fails, reject the fill — never use the underlying's bar price."""
    class BuyUnknownOptionAlgo:
        def __init__(self): self._fired = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            if self._fired: return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="O:QQQ250516C00999000",  # strike 999 — not in chain
                signal_type=SignalType.BUY, quantity=1,
                asset_type="options", order_type=OrderType.MARKET,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _make_clock()
    chain_df = _make_chain()  # only has strike 500
    class MockDS:
        def load_market_data(self, s, sym, tf): return None
        def load_option_chain(self, p, s, e): return chain_df
        def list_option_chain_expirations(self, p, s): return [date(2025, 5, 16)]

    ctx = BacktestTickContext(
        bars={("polygon", "QQQ", "1day"): clock}, positions={}, cash=100_000.0,
        data_service=MockDS(), default_source="polygon",
    )
    ctx._option_chain_cache[("polygon", "QQQ", date(2025, 5, 16))] = chain_df

    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=BuyUnknownOptionAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="QQQ",
        slippage=SlippageModel(market_bps=0), buy_fees=[], sell_fees=[],
        initial_cash=100_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert obs.error is None
    assert len(obs.fills) == 0, f"Should not fill at equity price, got {len(obs.fills)} fills"
    assert len(obs.rejected) == 1
    assert "no_option_price" in obs.rejected[0][2]


def test_option_chain_reprices_with_underlying():
    """Option prices should change as the underlying moves."""
    from coordinator.services.backtest_tick_context import BacktestTickContext

    chain_df = _make_chain()  # call bid=8.00 at underlying ~500, strike 500

    class MockDS:
        def load_market_data(self, s, sym, tf): return None
        def load_option_chain(self, p, s, e): return chain_df.copy()
        def list_option_chain_expirations(self, p, s): return [date(2025, 5, 16)]

    # Scenario 1: underlying at $500 (ATM)
    ctx1 = BacktestTickContext(
        bars={("polygon", "QQQ", "1day"): pd.DataFrame({
            "timestamp": [pd.Timestamp("2025-04-15", tz="UTC")],
            "open": [500.0], "high": [505.0], "low": [495.0], "close": [500.0], "volume": [1e6],
        })},
        positions={}, cash=100_000.0,
        data_service=MockDS(), default_source="polygon",
    )
    ctx1.set_sim_time(pd.Timestamp("2025-04-15", tz="UTC").to_pydatetime())
    ctx1._option_chain_cache[("polygon", "QQQ", date(2025, 5, 16))] = chain_df.copy()
    chain1 = ctx1.option_chain("QQQ", date(2025, 5, 16))
    call_price_1 = chain1.calls[0].bid

    # Scenario 2: underlying at $520 (+4%, call goes deeper ITM)
    ctx2 = BacktestTickContext(
        bars={("polygon", "QQQ", "1day"): pd.DataFrame({
            "timestamp": [pd.Timestamp("2025-04-16", tz="UTC")],
            "open": [520.0], "high": [525.0], "low": [515.0], "close": [520.0], "volume": [1e6],
        })},
        positions={}, cash=100_000.0,
        data_service=MockDS(), default_source="polygon",
    )
    ctx2.set_sim_time(pd.Timestamp("2025-04-16", tz="UTC").to_pydatetime())
    ctx2._option_chain_cache[("polygon", "QQQ", date(2025, 5, 16))] = chain_df.copy()
    chain2 = ctx2.option_chain("QQQ", date(2025, 5, 16))
    call_price_2 = chain2.calls[0].bid

    # Call at strike 500 should be more expensive when underlying is at 520 vs 500
    assert call_price_2 > call_price_1, (
        f"Call price should increase when underlying rises: was {call_price_1}, now {call_price_2}"
    )


def test_short_option_mtm_reflects_liability():
    """A short option position should show as a LIABILITY in portfolio value."""
    class SellAndHoldAlgo:
        def __init__(self): self._fired = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            if self._fired: return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="O:QQQ250516C00500000",
                signal_type=SignalType.SELL, quantity=1,
                asset_type="options", order_type=OrderType.MARKET,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _make_clock()
    chain_df = _make_chain()  # call bid=8.00, ask=8.50
    class MockDS:
        def load_market_data(self, s, sym, tf): return None
        def load_option_chain(self, p, s, e): return chain_df
        def list_option_chain_expirations(self, p, s): return [date(2025, 5, 16)]

    ctx = BacktestTickContext(
        bars={("polygon", "QQQ", "1day"): clock}, positions={}, cash=100_000.0,
        data_service=MockDS(), default_source="polygon",
    )
    ctx._option_chain_cache[("polygon", "QQQ", date(2025, 5, 16))] = chain_df

    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=SellAndHoldAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="QQQ",
        slippage=SlippageModel(market_bps=0), buy_fees=[], sell_fees=[],
        initial_cash=100_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert obs.error is None
    # After selling 1 call at $8 bid, received $800 premium.
    # Cash = 100,000 + 800 = 100,800
    # But we OWE the option — liability at mid price ~$8.25 * 100 = $825
    # Portfolio = cash + positions_value = 100,800 + (-825) ≈ $99,975
    # Should be LESS than starting capital (small loss from bid/mid spread)
    final_pv = obs.equity[-1]["pv"]
    final_cash = obs.equity[-1]["cash"]
    assert final_cash == pytest.approx(100_800.0, abs=10.0), f"Cash should be ~100,800, got {final_cash}"
    assert final_pv < final_cash, f"Portfolio {final_pv} should be less than cash {final_cash} (short liability)"
    # Portfolio should be close to starting value, slightly less due to bid/mid spread
    assert 99_000 < final_pv < 100_100, f"Portfolio should be ~$99,975, got {final_pv}"
