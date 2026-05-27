import numpy as np
import pandas as pd
import pytest

from coordinator.services.validation.bootstrap import (
    block_bootstrap_sharpe,
    block_bootstrap_max_drawdown,
    bootstrap_metrics,
    MetricCI,
)


def test_block_bootstrap_sharpe_recovers_known_signal():
    rng = np.random.default_rng(1)
    daily_returns = rng.normal(loc=0.001, scale=0.02, size=2000)
    equity = pd.Series(1000.0 * np.cumprod(1.0 + daily_returns))

    ci = block_bootstrap_sharpe(equity, block_size=20, n_resamples=500, confidence=0.95, seed=1)
    assert isinstance(ci, MetricCI)
    # Annualized Sharpe = mean/std * sqrt(252) ≈ (0.001/0.02)*sqrt(252) ≈ 0.79
    assert 0.4 <= ci.point <= 1.2
    assert ci.lower < ci.point < ci.upper
    assert ci.upper - ci.lower > 0


def test_block_bootstrap_ci_widens_with_smaller_sample():
    rng = np.random.default_rng(42)
    returns_long = rng.normal(0.001, 0.02, 2000)
    returns_short = rng.normal(0.001, 0.02, 200)
    eq_long = pd.Series(np.cumprod(1.0 + returns_long))
    eq_short = pd.Series(np.cumprod(1.0 + returns_short))

    ci_long = block_bootstrap_sharpe(eq_long, block_size=20, n_resamples=300, seed=2)
    ci_short = block_bootstrap_sharpe(eq_short, block_size=20, n_resamples=300, seed=2)

    assert (ci_short.upper - ci_short.lower) > (ci_long.upper - ci_long.lower)


def test_block_bootstrap_max_drawdown_negative_and_bounded():
    rng = np.random.default_rng(0)
    daily = rng.normal(0.0005, 0.015, 1000)
    equity = pd.Series(np.cumprod(1.0 + daily))
    ci = block_bootstrap_max_drawdown(equity, block_size=15, n_resamples=300, seed=3)
    assert ci.point < 0
    assert ci.lower <= ci.point <= ci.upper
    assert ci.lower >= -1.0


def test_bootstrap_metrics_returns_named_dict():
    rng = np.random.default_rng(0)
    daily = rng.normal(0.0005, 0.015, 1000)
    equity = pd.Series(np.cumprod(1.0 + daily))
    out = bootstrap_metrics(equity, n_resamples=200, seed=4)
    assert "sharpe" in out
    assert "max_drawdown" in out
    assert isinstance(out["sharpe"], MetricCI)


def test_bootstrap_metrics_includes_sortino_cagr_calmar():
    rng = np.random.default_rng(0)
    daily = rng.normal(0.0005, 0.015, 1000)
    equity = pd.Series(np.cumprod(1.0 + daily))
    out = bootstrap_metrics(equity, n_resamples=200, seed=4)
    assert "sortino" in out
    assert "cagr" in out
    assert "calmar" in out
    for k in ("sortino", "cagr", "calmar"):
        assert isinstance(out[k], MetricCI)
        assert out[k].lower <= out[k].point <= out[k].upper
