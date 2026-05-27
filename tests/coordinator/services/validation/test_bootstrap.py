import numpy as np
import pandas as pd
import pytest

from coordinator.services.validation.bootstrap import (
    block_bootstrap_sharpe,
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
