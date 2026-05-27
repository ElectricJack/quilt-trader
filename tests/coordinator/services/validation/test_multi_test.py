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
