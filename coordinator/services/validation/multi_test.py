from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class CorrectedResult:
    raw_p: float
    corrected_p: float
    significant: bool
    method: str


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
