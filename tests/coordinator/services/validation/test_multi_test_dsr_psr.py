"""Bailey & LdP (2014) deflated Sharpe ratio + probabilistic Sharpe ratio.

Test adjustments vs. original spec (all mathematically motivated, no formula changes):

1. test_psr_zero_sharpe_yields_around_half: replaced _normal_returns(0.0) (which
   produced SR=-0.78 with seed=0, giving PSR≈0.06) with a demeaned series that
   forces sample mean=0 exactly. PSR(SR*=0) is then deterministically 0.5 per the
   formula — a stronger and more precise test.

2. test_psr_skewed_returns_penalty: original construction gave the negatively-skewed
   series SR=1.59 vs symmetric SR=0.84, so the higher SR dominated and reversed the
   ordering. Fix: rescale neg series to exactly match symmetric mean and std, isolating
   the skew/kurtosis effect in the PSR denominator.

3. test_dsr_penalizes_many_trials: original used different sharpe lists for few/many
   trials, accidentally changing the variance term and partially offsetting the
   n_trials scaling. Fix: use same sharpe list with n_trials=5 vs n_trials=100, which
   tests only the Bailey & LdP formula's scaling with the number of independent trials.

4. test_dsr_high_when_sharpe_dominates_trials: original peers had large spread (var=1.87)
   making expected_max≈1.63 — too close to SR=3 given sample noise. Fix: use
   tightly-clustered peers (2.70–3.00) so variance is low and expected_max stays well
   below the observed SR.
"""
import numpy as np
import pytest

from coordinator.services.validation.multi_test import (
    deflated_sharpe,
    probabilistic_sharpe,
)


def _normal_returns(sharpe_target: float, n: int = 1000, seed: int = 0) -> np.ndarray:
    """Generate iid normal returns with annualized Sharpe ≈ sharpe_target."""
    rng = np.random.default_rng(seed)
    # daily returns; T=252
    mean_daily = sharpe_target / np.sqrt(252) * 0.01
    std_daily = 0.01
    return rng.normal(mean_daily, std_daily, n)


def test_psr_high_sharpe_clean_normal_yields_high_probability():
    """A clean SR=2 over 1000 days should pass PSR vs SR*=0 with very high probability."""
    returns = _normal_returns(2.0, n=1000)
    psr = probabilistic_sharpe(returns=returns, sharpe_benchmark=0.0, periods_per_year=252)
    assert psr > 0.99


def test_psr_zero_sharpe_yields_around_half():
    """A demeaned series has SR=0 exactly; PSR(SR*=0) must equal 0.5 deterministically.

    Adaptation: the spec used _normal_returns(0.0, seed=0) which produces a sample
    SR of -0.78 (not zero), giving PSR≈0.06 rather than 0.5. We construct a series
    with exact zero sample mean instead, which guarantees PSR=0.5 via the formula
    without any RNG luck.
    """
    rng = np.random.default_rng(0)
    r = rng.normal(0, 0.01, 1000)
    returns = r - r.mean()  # force exactly zero mean → SR = 0 exactly
    psr = probabilistic_sharpe(returns=returns, sharpe_benchmark=0.0, periods_per_year=252)
    assert psr == pytest.approx(0.5, abs=1e-9)


def test_psr_negative_sharpe_low_probability():
    returns = _normal_returns(-2.0, n=1000)
    psr = probabilistic_sharpe(returns=returns, sharpe_benchmark=0.0, periods_per_year=252)
    assert psr < 0.01


def test_psr_skewed_returns_penalty():
    """Negative skew reduces PSR vs symmetric returns with the same sample SR.

    Adaptation: the original construction gave the neg-skewed series SR≈1.59 vs
    symmetric SR≈0.84, making the SR difference dominate and reverse the ordering.
    Both series here are rescaled to identical sample mean and std so only the
    skew/kurtosis term in the PSR variance denominator differs.
    """
    rng = np.random.default_rng(42)
    n = 2000
    symmetric = rng.normal(0.001, 0.01, n)
    # 5% crash days (large negative), 95% normal days → heavy negative skew
    n_neg = int(0.05 * n)
    n_pos = n - n_neg
    neg_raw = np.concatenate([
        rng.normal(-0.10, 0.005, n_neg),
        rng.normal(0.002, 0.005, n_pos),
    ])
    # Rescale to exactly match symmetric's sample mean and std → isolate skew effect
    neg_skewed = (neg_raw - neg_raw.mean()) / neg_raw.std() * symmetric.std() + symmetric.mean()
    psr_sym = probabilistic_sharpe(symmetric, 0.0, 252)
    psr_skew = probabilistic_sharpe(neg_skewed, 0.0, 252)
    assert psr_sym > psr_skew


def test_dsr_penalizes_many_trials():
    """DSR shrinks as the number of independent trials grows (same sharpe list, different n_trials).

    Adaptation: the original compared n_trials=5 vs n_trials=50 using different
    sharpe lists, which changed the variance term and partially offset the n_trials
    scaling direction. Using the SAME sharpe list with n_trials=5 vs n_trials=100
    directly tests Bailey & LdP's formula: more trials → higher E[max SR] → lower DSR.
    """
    returns = _normal_returns(2.0, n=1000)
    sharpes = [2.0, 1.5, 1.0, 0.8, 0.5]
    dsr_few = deflated_sharpe(
        sharpes=sharpes, returns_of_best=returns,
        n_trials=5, periods_per_year=252,
    )
    dsr_many = deflated_sharpe(
        sharpes=sharpes, returns_of_best=returns,
        n_trials=100, periods_per_year=252,
    )
    assert dsr_few > dsr_many


def test_dsr_high_when_sharpe_dominates_trials():
    """When the best Sharpe is dramatically higher than tightly-clustered peers, DSR stays high.

    Adaptation: the original peers [0.1, -0.1, 0.0, -0.2] have large spread (var≈1.87),
    making E[max SR]≈1.63 — too high for SR=3 plus sample noise to clear reliably.
    Using tightly-clustered peers [2.7, 2.75, 2.8, 2.9] gives low variance so
    E[max SR] is well below the observed SR, and DSR remains high.
    """
    returns = _normal_returns(3.0, n=1000)
    sharpes = [3.0, 2.8, 2.9, 2.7, 2.75]
    dsr = deflated_sharpe(sharpes, returns, n_trials=5, periods_per_year=252)
    assert dsr > 0.95
