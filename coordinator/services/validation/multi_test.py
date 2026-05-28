from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np


@dataclass
class CorrectedResult:
    raw_p: float
    corrected_p: float
    significant: bool
    method: str


@dataclass
class SPAResult:
    """White's Reality Check / Hansen's SPA test result.

    `best_idx`: index of the best-performing strategy in the input
    `best_mean`: that strategy's sample mean (e.g. Sharpe, daily return)
    `p_value`: probability that the best strategy's outperformance is due to
      data mining across the full set of strategies (under the null that all
      strategies have zero edge). Smaller is stronger evidence the best
      strategy has a real edge.
    """
    best_idx: int
    best_mean: float
    p_value: float
    method: str
    n_strategies: int
    n_resamples: int


def correct(
    *,
    raw_p_values: list[float],
    n_tested: int,
    method: Literal["bonferroni", "bh"] = "bonferroni",
    alpha: float = 0.05,
) -> list[CorrectedResult]:
    """Apply a multiple-testing correction.

    Bonferroni: corrected = min(raw * n_tested, 1).
    Benjamini-Hochberg (FDR control): order p-values; threshold k = max k where p[k] ≤ alpha * (k+1) / n.
    """
    if method == "bonferroni":
        return [
            CorrectedResult(
                raw_p=p,
                corrected_p=min(p * n_tested, 1.0),
                significant=(min(p * n_tested, 1.0) < alpha),
                method=method,
            )
            for p in raw_p_values
        ]
    elif method == "bh":
        n = len(raw_p_values)
        if n == 0:
            return []
        order = sorted(range(n), key=lambda i: raw_p_values[i])
        sorted_p = [raw_p_values[i] for i in order]
        thresholds = [alpha * (k + 1) / n_tested for k in range(n)]
        # Find largest k where sorted_p[k] <= thresholds[k]
        k_max = -1
        for k in range(n):
            if sorted_p[k] <= thresholds[k]:
                k_max = k
        significant_set = set(order[: k_max + 1]) if k_max >= 0 else set()
        return [
            CorrectedResult(
                raw_p=raw_p_values[i],
                corrected_p=raw_p_values[i] * n_tested / (rank_in_sorted + 1)
                if (rank_in_sorted := next(idx for idx, j in enumerate(order) if j == i)) is not None
                else raw_p_values[i],
                significant=(i in significant_set),
                method=method,
            )
            for i in range(n)
        ]
    else:
        raise ValueError(f"Unknown correction method: {method}")


def spa_test(
    *,
    returns_matrix: Sequence[Sequence[float]],
    n_resamples: int = 2000,
    block_size: int | None = None,
    seed: int = 0,
) -> SPAResult:
    """Hansen's Superior Predictive Ability (SPA) test / White's Reality Check.

    Given a matrix of N strategies × T observations (e.g. daily returns), test
    the null hypothesis that NONE of the strategies has a positive expected
    value, against the alternative that AT LEAST one does. Corrects for
    data-mining: even a portfolio of zero-edge strategies will produce a
    "best" with positive sample mean by chance, so naive significance testing
    overstates real edge.

    Implementation: stationary bootstrap of the centered returns. For each
    resample, compute the per-strategy mean and take the max. The p-value is
    the fraction of bootstrap-max-means that exceed the observed
    best-strategy mean. Hansen (2005) refines this with a studentized
    statistic and a "consistent estimator" of which strategies belong in the
    comparison set; this implementation ships the simpler White (2000)
    version, which is conservative compared to Hansen but easier to reason
    about and validate.

    Args:
        returns_matrix: list of N strategy return series, each of length T.
            All series must be the same length. Each entry is the per-period
            return (e.g. daily). DO NOT pre-center; the function does it.
        n_resamples: how many bootstrap iterations to run. 2000 is the
            common default; 5000+ for tight p-value precision.
        block_size: stationary-bootstrap block length. Defaults to
            max(1, T // 20) which preserves typical autocorrelation in
            daily returns. Increase for slower-mean-reverting series.
        seed: RNG seed for reproducibility.

    Returns:
        SPAResult with the best strategy's index, sample mean, and the
        data-mining-corrected p-value.
    """
    arr = np.asarray(returns_matrix, dtype=float)
    if arr.ndim != 2:
        raise ValueError("returns_matrix must be 2-D (N strategies × T observations)")
    n, t = arr.shape
    if n == 0 or t == 0:
        raise ValueError("returns_matrix is empty")
    if block_size is None:
        block_size = max(1, t // 20)

    sample_means = arr.mean(axis=1)
    best_idx = int(np.argmax(sample_means))
    best_mean = float(sample_means[best_idx])

    # Center each strategy's returns so the bootstrap distribution has zero
    # mean — this is the null hypothesis.
    centered = arr - sample_means[:, None]

    rng = np.random.default_rng(seed)
    # Stationary-bootstrap block-start indices. For each resample we draw
    # roughly t/block_size blocks and concatenate.
    n_blocks_per = int(np.ceil(t / block_size))
    bootstrap_max_means = np.empty(n_resamples, dtype=float)
    for i in range(n_resamples):
        starts = rng.integers(0, t - block_size + 1, size=n_blocks_per)
        # Build the resample index list by concatenating blocks
        idx_chunks = [np.arange(s, s + block_size) for s in starts]
        idx = np.concatenate(idx_chunks)[:t]
        # Per-strategy mean over the bootstrap sample, then take the max
        per_strategy = centered[:, idx].mean(axis=1)
        bootstrap_max_means[i] = per_strategy.max()

    # p-value: P(max of zero-mean bootstrap > observed best_mean)
    p_value = float(np.mean(bootstrap_max_means >= best_mean))

    return SPAResult(
        best_idx=best_idx,
        best_mean=best_mean,
        p_value=p_value,
        method="white_reality_check",
        n_strategies=n,
        n_resamples=n_resamples,
    )
