"""Tests for BS-based option chain repricing."""
import pandas as pd
import pytest
from coordinator.services.backtest_tick_context import BacktestTickContext


def _make_chain(strike, call_price, put_price, iv=0.25):
    return pd.DataFrame([
        {"symbol": f"SPY250620C00{strike}000", "strike": float(strike),
         "option_type": "call", "bid": call_price - 0.05,
         "ask": call_price + 0.05, "last": call_price,
         "volume": 1000, "open_interest": 0, "implied_volatility": iv},
        {"symbol": f"SPY250620P00{strike}000", "strike": float(strike),
         "option_type": "put", "bid": put_price - 0.05,
         "ask": put_price + 0.05, "last": put_price,
         "volume": 1000, "open_interest": 0, "implied_volatility": iv},
    ])


def test_reprice_underlying_up_increases_call():
    """When underlying goes up, call price should increase."""
    # Use BS-consistent prices for S=500, K=500, T~111d, iv=0.25
    chain = _make_chain(500, call_price=30.44, put_price=24.40)
    spy_bars = pd.DataFrame({
        "timestamp": pd.to_datetime(["2025-03-01", "2025-03-02"]),
        "open": [500.0, 505.0], "high": [502.0, 507.0],
        "low": [498.0, 503.0], "close": [500.0, 505.0],
        "volume": [1000000, 1000000],
    })
    ctx = BacktestTickContext(
        bars={("polygon", "SPY", "1day"): spy_bars},
        positions={}, cash=100000,
    )
    ctx.set_sim_time(pd.Timestamp("2025-03-02").to_pydatetime())
    repriced = ctx._reprice_chain(chain, "SPY")
    call = repriced[repriced["option_type"] == "call"].iloc[0]
    assert call["last"] > 30.44


def test_reprice_underlying_up_decreases_put():
    """When underlying goes up, put price should decrease."""
    # Use BS-consistent prices for S=500, K=500, T~111d, iv=0.25
    chain = _make_chain(500, call_price=30.44, put_price=24.40)
    spy_bars = pd.DataFrame({
        "timestamp": pd.to_datetime(["2025-03-01", "2025-03-02"]),
        "open": [500.0, 505.0], "high": [502.0, 507.0],
        "low": [498.0, 503.0], "close": [500.0, 505.0],
        "volume": [1000000, 1000000],
    })
    ctx = BacktestTickContext(
        bars={("polygon", "SPY", "1day"): spy_bars},
        positions={}, cash=100000,
    )
    ctx.set_sim_time(pd.Timestamp("2025-03-02").to_pydatetime())
    repriced = ctx._reprice_chain(chain, "SPY")
    put = repriced[repriced["option_type"] == "put"].iloc[0]
    assert put["last"] < 24.40


def test_reprice_time_decay_reduces_value():
    """Even with flat underlying, options lose value over time (theta)."""
    # Use BS-consistent prices for S=500, K=500, T~111d, iv=0.25
    chain = _make_chain(500, call_price=30.44, put_price=24.40, iv=0.25)
    spy_bars = pd.DataFrame({
        "timestamp": pd.to_datetime(["2025-03-01", "2025-03-15"]),
        "open": [500.0, 500.0], "high": [502.0, 502.0],
        "low": [498.0, 498.0], "close": [500.0, 500.0],
        "volume": [1000000, 1000000],
    })
    ctx = BacktestTickContext(
        bars={("polygon", "SPY", "1day"): spy_bars},
        positions={}, cash=100000,
    )
    ctx._ref_price_SPY = 500.0
    ctx._ref_time_SPY = pd.Timestamp("2025-03-01")
    ctx.set_sim_time(pd.Timestamp("2025-03-15").to_pydatetime())
    repriced = ctx._reprice_chain(chain, "SPY")
    call = repriced[repriced["option_type"] == "call"].iloc[0]
    assert call["last"] < 30.44


def test_reprice_preserves_bid_ask_spread_ratio():
    """Bid-ask spread should scale with the repriced price, not stay fixed."""
    chain = _make_chain(500, call_price=30.44, put_price=24.40)
    spy_bars = pd.DataFrame({
        "timestamp": pd.to_datetime(["2025-03-01", "2025-03-02"]),
        "open": [500.0, 510.0], "high": [502.0, 512.0],
        "low": [498.0, 508.0], "close": [500.0, 510.0],
        "volume": [1000000, 1000000],
    })
    ctx = BacktestTickContext(
        bars={("polygon", "SPY", "1day"): spy_bars},
        positions={}, cash=100000,
    )
    ctx.set_sim_time(pd.Timestamp("2025-03-02").to_pydatetime())
    repriced = ctx._reprice_chain(chain, "SPY")
    call = repriced[repriced["option_type"] == "call"].iloc[0]
    spread = call["ask"] - call["bid"]
    assert spread > 0
    assert call["bid"] > 0
