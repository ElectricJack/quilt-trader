from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd


@dataclass
class MetricCI:
    point: float
    lower: float
    upper: float
    confidence: float


def _equity_to_returns(equity: pd.Series) -> np.ndarray:
    arr = equity.dropna().to_numpy(dtype=float)
    return np.diff(arr) / arr[:-1]


def _annualized_sharpe(returns: np.ndarray, periods_per_year: int = 252) -> float:
    if returns.size == 0:
        return 0.0
    mu = float(np.mean(returns))
    sigma = float(np.std(returns, ddof=1))
    if sigma == 0:
        return 0.0
    return mu / sigma * np.sqrt(periods_per_year)


def _max_drawdown(equity_arr: np.ndarray) -> float:
    if equity_arr.size == 0:
        return 0.0
    peak = np.maximum.accumulate(equity_arr)
    dd = (equity_arr - peak) / peak
    return float(np.min(dd))  # negative number


def _block_resample(returns: np.ndarray, block_size: int, rng: np.random.Generator) -> np.ndarray:
    n = returns.size
    n_blocks = int(np.ceil(n / block_size))
    starts = rng.integers(0, n - block_size + 1, size=n_blocks)
    out = np.concatenate([returns[s : s + block_size] for s in starts])
    return out[:n]


def block_bootstrap_sharpe(
    equity: pd.Series,
    *,
    block_size: int | None = None,
    n_resamples: int = 1000,
    confidence: float = 0.95,
    periods_per_year: int = 252,
    seed: int = 0,
) -> MetricCI:
    returns = _equity_to_returns(equity)
    if block_size is None:
        block_size = max(20, len(returns) // 20)
    rng = np.random.default_rng(seed)

    point = _annualized_sharpe(returns, periods_per_year)
    samples = np.array(
        [
            _annualized_sharpe(_block_resample(returns, block_size, rng), periods_per_year)
            for _ in range(n_resamples)
        ]
    )
    alpha = 1.0 - confidence
    lower = float(np.quantile(samples, alpha / 2))
    upper = float(np.quantile(samples, 1 - alpha / 2))
    return MetricCI(point=point, lower=lower, upper=upper, confidence=confidence)
