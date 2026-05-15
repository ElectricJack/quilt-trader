"""Tests for the QuantStats-backed metrics wrapper."""
import math
import pandas as pd
import pytest

from coordinator.services.backtest_metrics_qs import compute_all


def _daily_df(values: list[float]) -> pd.DataFrame:
    """Build a daily portfolio_value frame with a 'return' column."""
    idx = pd.date_range("2024-01-01", periods=len(values), freq="D")
    df = pd.DataFrame({"portfolio_value": values}, index=idx)
    df["return"] = df["portfolio_value"].pct_change().fillna(0)
    return df


def test_compute_all_returns_canonical_keys():
    df = _daily_df([100.0, 101.0, 99.0, 102.0, 100.0, 103.0, 105.0])
    result = compute_all(df, trades=[], initial_cash=100.0, risk_free_rate=0.04)
    expected_keys = {
        "total_return", "cagr", "volatility", "sharpe_ratio", "sortino_ratio",
        "calmar_ratio", "max_drawdown", "max_drawdown_date", "romad",
        "trade_count", "win_rate", "profit_factor", "avg_win", "avg_loss",
        "expectancy", "longest_drawdown_days",
        "longest_winning_streak", "longest_losing_streak",
        "drawdown_periods",
    }
    assert expected_keys.issubset(result.keys())
    assert isinstance(result["sharpe_ratio"], float)
    assert isinstance(result["drawdown_periods"], list)


def test_total_return_matches_simple_calc():
    df = _daily_df([100.0, 110.0, 120.0])
    result = compute_all(df, trades=[], initial_cash=100.0)
    assert result["total_return"] == pytest.approx(0.20, rel=1e-3)


def test_max_drawdown_positive_value():
    df = _daily_df([100.0, 90.0, 80.0, 95.0])
    result = compute_all(df, trades=[], initial_cash=100.0)
    # qs returns drawdown as negative; we normalize to positive magnitude
    assert result["max_drawdown"] == pytest.approx(0.20, rel=1e-2)
    assert result["max_drawdown_date"] is not None


def test_empty_df_returns_safe_zeros():
    df = pd.DataFrame(columns=["portfolio_value", "return"])
    result = compute_all(df, trades=[], initial_cash=100.0)
    assert result["total_return"] == 0.0
    assert result["sharpe_ratio"] == 0.0
    assert result["drawdown_periods"] == []
