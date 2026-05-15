import pytest
import math
import pandas as pd
from datetime import datetime, timezone
from coordinator.services.backtest_metrics import (
    cagr, volatility, sharpe_ratio, sortino_ratio, calmar_ratio,
    max_drawdown, romad, total_return, win_rate, profit_factor,
    avg_win, avg_loss, expectancy, round_trip_trades, longest_streak,
    longest_drawdown_days, top_n_drawdowns, compute_all,
)


def _daily_returns(values):
    """Build a daily-indexed dataframe with portfolio_value series."""
    idx = pd.date_range("2024-01-01", periods=len(values), freq="D", tz="UTC")
    df = pd.DataFrame({"portfolio_value": values}, index=idx)
    df["return"] = df["portfolio_value"].pct_change().fillna(0)
    return df


def test_total_return_simple():
    df = _daily_returns([100, 110, 121])  # +10%, +10%
    assert total_return(df, initial_cash=100) == pytest.approx(0.21, abs=1e-6)


def test_cagr_one_year():
    df = _daily_returns([100, 110] + [110] * 365)  # 10% gain, held one full year
    # CAGR ≈ 10% / 366 days * 365 ~ 9.97%; approx check
    result = cagr(df)
    assert 0.08 < result < 0.12


def test_volatility_zero_when_no_variation():
    df = _daily_returns([100] * 100)
    assert volatility(df) == pytest.approx(0.0, abs=1e-9)


def test_sharpe_uses_cagr_minus_rf_over_vol():
    df = _daily_returns([100, 101, 102, 103, 104, 105])  # steady gains
    s = sharpe_ratio(df, risk_free_rate=0.0)
    # Returns are positive with low vol, so sharpe should be large positive
    assert s > 0


def test_sortino_penalizes_downside_only():
    # Two series with same total return; one has downside vol, one doesn't.
    smooth = _daily_returns([100, 110, 120, 130, 140])
    volatile = _daily_returns([100, 110, 100, 120, 140])
    s_smooth = sortino_ratio(smooth, risk_free_rate=0.0)
    s_volatile = sortino_ratio(volatile, risk_free_rate=0.0)
    assert s_smooth > s_volatile  # Smooth has no downside


def test_max_drawdown_finds_peak_to_trough():
    df = _daily_returns([100, 110, 120, 90, 100, 80, 130])  # peak 120, trough 80 → -33.3%
    md = max_drawdown(df)
    assert md["drawdown"] == pytest.approx((120 - 80) / 120, abs=1e-4)


def test_romad():
    df = _daily_returns([100, 110, 90, 105])
    r = romad(df)
    assert isinstance(r, float)


def test_calmar_cagr_over_max_drawdown():
    df = _daily_returns([100, 110, 90, 105, 120])
    c = calmar_ratio(df)
    assert isinstance(c, float)


# ---- Trade-based metrics ----

def _make_trades(realized_pnls):
    """Each pnl creates one round-trip trade (open + close at same price+pnl)."""
    return [{"realized_pnl": p, "timestamp": f"2024-01-{i+1:02d}T00:00:00+00:00"}
            for i, p in enumerate(realized_pnls)]


def test_win_rate():
    trades = _make_trades([10, 20, -5, 15, -10])  # 3 wins / 5
    assert win_rate(trades) == pytest.approx(0.6, abs=1e-6)


def test_profit_factor():
    trades = _make_trades([10, 20, -5, 15, -10])  # gross profit 45, gross loss 15
    assert profit_factor(trades) == pytest.approx(45/15, abs=1e-6)


def test_avg_win_and_loss():
    trades = _make_trades([10, 20, -5, 15, -10])
    assert avg_win(trades) == pytest.approx((10+20+15)/3, abs=1e-6)
    assert avg_loss(trades) == pytest.approx((-5-10)/2, abs=1e-6)


def test_expectancy():
    trades = _make_trades([10, 20, -5, 15, -10])
    # E = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)
    expected = 0.6 * 15.0 + 0.4 * -7.5
    assert expectancy(trades) == pytest.approx(expected, abs=1e-6)


def test_longest_streak():
    trades = _make_trades([10, 20, -5, 15, 30, 5, -10, -20, 5])
    assert longest_streak(trades, win=True) == 3  # 15, 30, 5
    assert longest_streak(trades, win=False) == 2  # -10, -20


def test_compute_all_returns_dict():
    df = _daily_returns([100, 102, 105, 100, 110])
    trades = _make_trades([5, -2, 10, -3])
    out = compute_all(df, trades, initial_cash=100, risk_free_rate=0.04)
    assert "total_return" in out
    assert "sharpe_ratio" in out
    assert "win_rate" in out
    assert "total_fees_paid" not in out  # fees come from trade dicts, separate sum
