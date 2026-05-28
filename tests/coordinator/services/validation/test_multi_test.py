import pytest

from coordinator.services.validation.multi_test import correct, CorrectedResult


def test_bonferroni_correction():
    p_values = [0.01, 0.04, 0.20]
    results = correct(raw_p_values=p_values, n_tested=10, method="bonferroni", alpha=0.05)
    assert all(isinstance(r, CorrectedResult) for r in results)
    # Bonferroni: corrected = raw * n_tested; significant if corrected < alpha
    assert results[0].corrected_p == 0.10
    assert results[0].significant is False
    assert results[2].corrected_p == 1.0  # capped at 1


def test_benjamini_hochberg_correction():
    p_values = [0.001, 0.008, 0.04]
    results = correct(raw_p_values=p_values, n_tested=3, method="bh", alpha=0.05)
    assert len(results) == 3
    # BH at α=0.05 with 3 hypotheses: all p ≤ alpha*(k/n) for k=1,2,3 → all significant
    assert all(r.significant for r in results)


def test_correct_rejects_unknown_method():
    with pytest.raises(ValueError):
        correct(raw_p_values=[0.01], n_tested=1, method="unknown")


# ---- SPA / White's Reality Check ----

import numpy as np

from coordinator.services.validation.multi_test import spa_test, SPAResult


def test_spa_correctly_identifies_real_edge():
    """A strategy with genuine positive mean should produce a low p-value."""
    rng = np.random.default_rng(42)
    # 1 strategy with strong real edge (t-stat ~5) + 9 zero-mean noise strategies
    T = 2500
    real = rng.normal(0.002, 0.02, T)
    noise = rng.normal(0.0, 0.02, (9, T))
    matrix = np.vstack([real, noise])

    result = spa_test(returns_matrix=matrix, n_resamples=500, seed=1)
    assert isinstance(result, SPAResult)
    assert result.n_strategies == 10
    # With a t-stat ~5 real-edge strategy in the pool, SPA should detect
    # the edge — even though noise occasionally beats the real strategy in
    # SAMPLE mean (a finite-T artifact), the test correctly rejects the
    # null hypothesis that ALL strategies have zero edge.
    assert result.best_mean > 0
    assert result.p_value < 0.05


def test_spa_rejects_random_strategy_pool():
    """With ONLY zero-mean strategies, the p-value should be near uniform —
    NOT consistently low. We assert it's >0.05 to confirm the data-mining
    correction is doing its job (a naive t-test of the best strategy would
    falsely reject the null with probability ~50% across 10 strategies)."""
    rng = np.random.default_rng(7)
    T = 1000
    matrix = rng.normal(0.0, 0.02, (10, T))

    result = spa_test(returns_matrix=matrix, n_resamples=500, seed=2)
    # Conservative: under the null, the p-value distribution is roughly
    # uniform on [0, 1] (slightly biased due to discrete bootstrap). We
    # expect "no false positive at the 5% level" on average; a single
    # seed COULD fall below by chance, but the assertion is loose enough
    # to be deterministic.
    assert 0.0 <= result.p_value <= 1.0
    # Naive test (no correction) would frequently fire at p<0.05; SPA
    # should NOT. With seed 7 + 500 resamples, the realized p is
    # historically ~0.3-0.7 — well above 0.05.
    assert result.p_value > 0.05


def test_spa_rejects_empty_input():
    import pytest
    # Empty list is 1-D in numpy's view → "must be 2-D" trips first.
    with pytest.raises(ValueError):
        spa_test(returns_matrix=[])
    # 2-D empty matrix → caught by the explicit empty check
    with pytest.raises(ValueError, match="empty"):
        spa_test(returns_matrix=np.zeros((0, 0)))


def test_spa_rejects_wrong_dimensionality():
    import pytest
    with pytest.raises(ValueError, match="2-D"):
        spa_test(returns_matrix=[1.0, 2.0, 3.0])
