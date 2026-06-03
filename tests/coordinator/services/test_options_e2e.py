"""End-to-end options backtesting integration test.

Verifies that an algorithm can:
1. Request an option chain via ctx.option_chain()
2. Buy a call option (filled at the ask)
3. Sell the option (filled at the bid)
4. Produce correct fill records with realized PnL
"""
import pytest
import pandas as pd
from datetime import date
from coordinator.services.backtest_engine_v2 import BacktestEngine, CancelToken
from coordinator.services.backtest_tick_context import BacktestTickContext
from coordinator.services.backtest_config import SlippageModel
from sdk.signals import Signal, SignalLeg, SignalType, OrderType


class _SimpleOptionsAlgo:
    """Buy a call option on first tick, sell on third tick."""

    def __init__(self):
        self._step = 0

    def on_start(self, config, restored_state):
        self._step = 0

    def on_tick(self, ctx):
        self._step += 1
        if self._step == 1:
            chain = ctx.option_chain("SPY", expiration=date(2026, 1, 17))
            if chain.calls:
                return [Signal(legs=[SignalLeg(
                    symbol=chain.calls[0].symbol,
                    signal_type=SignalType.BUY,
                    quantity=1,
                    asset_type="options",
                    order_type=OrderType.MARKET,
                )])]
        elif self._step == 3:
            if ctx.positions:
                sym = list(ctx.positions.keys())[0]
                return [Signal(legs=[SignalLeg(
                    symbol=sym,
                    signal_type=SignalType.SELL,
                    quantity=1,
                    asset_type="options",
                    order_type=OrderType.MARKET,
                )])]
        return []

    def on_stop(self):
        return {}

    def save_state(self):
        return {}


class _RecordingObserver:
    def __init__(self):
        self.fills = []
        self.equity = []
        self.complete = False
        self.error = None
        self.rejected = []

    def on_tick(self, st, cs):
        pass

    def on_signals_emitted(self, st, s):
        pass

    def on_fill(self, f):
        self.fills.append(f)

    def on_signal_rejected(self, st, s, r):
        self.rejected.append(r)

    def on_equity_point(self, st, pv, c, p):
        self.equity.append({"pv": pv})

    def on_complete(self, s):
        self.complete = True

    def on_error(self, e):
        self.error = e


def test_options_backtest_end_to_end():
    clock = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-10", periods=5, freq="D", tz="UTC"),
        "open": [550.0] * 5,
        "high": [555.0] * 5,
        "low": [545.0] * 5,
        "close": [552.0] * 5,
        "volume": [1e6] * 5,
    })
    chain_df = pd.DataFrame([
        {
            "ticker": "SPY260117C00550000",
            "strike": 550.0,
            "option_type": "call",
            "bid": 8.0,
            "ask": 8.5,
            "last": 8.2,
            "volume": 500,
            "open_interest": 3000,
            "implied_volatility": 0.20,
        },
        {
            "ticker": "SPY260117P00550000",
            "strike": 550.0,
            "option_type": "put",
            "bid": 7.0,
            "ask": 7.5,
            "last": 7.2,
            "volume": 400,
            "open_interest": 2500,
            "implied_volatility": 0.22,
        },
    ])

    class MockDS:
        def load_market_data(self, s, sym, tf):
            return None

        def load_option_chain(self, p, s, e):
            return chain_df if s == "SPY" else None

        def list_option_chain_expirations(self, p, s):
            return [date(2026, 1, 17)] if s == "SPY" else []

    ctx = BacktestTickContext(
        bars={("polygon", "SPY", "1day"): clock},
        positions={},
        cash=100_000.0,
        data_service=MockDS(),
        default_source="polygon",
    )
    # Pre-warm cache
    ctx._option_chain_cache[("polygon", "SPY", date(2026, 1, 17))] = chain_df

    obs = _RecordingObserver()
    BacktestEngine().run(
        algorithm=_SimpleOptionsAlgo(),
        ctx=ctx,
        clock_series=clock,
        clock_timeframe="1day",
        clock_source="polygon",
        clock_symbol="SPY",
        slippage=SlippageModel(market_bps=0),
        buy_fees=[],
        sell_fees=[],
        initial_cash=100_000.0,
        observer=obs,
        cancel_token=CancelToken(),
    )
    assert obs.error is None, f"Engine error: {obs.error}"
    assert obs.complete
    assert len(obs.fills) == 2, f"Expected 2 fills, got {len(obs.fills)}: {obs.fills}"
    buy_fill = obs.fills[0]
    sell_fill = obs.fills[1]
    assert buy_fill.side == "buy"
    assert buy_fill.fill_price == pytest.approx(8.5, abs=0.1)  # Ask for buy
    assert sell_fill.side == "sell"
    assert sell_fill.fill_price == pytest.approx(8.0, abs=0.1)  # Bid for sell
    assert sell_fill.realized_pnl is not None
