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


def _annualized_sortino(returns: np.ndarray, periods_per_year: int = 252) -> float:
    if returns.size == 0:
        return 0.0
    mu = float(np.mean(returns))
    downside = returns[returns < 0]
    if downside.size == 0:
        return 0.0
    sigma_d = float(np.std(downside, ddof=1))
    if sigma_d == 0:
        return 0.0
    return mu / sigma_d * np.sqrt(periods_per_year)


def _cagr(equity_arr: np.ndarray, periods_per_year: int = 252) -> float:
    if equity_arr.size < 2:
        return 0.0
    total = equity_arr[-1] / equity_arr[0]
    years = (equity_arr.size - 1) / periods_per_year
    if years <= 0 or total <= 0:
        return 0.0
    return float(total ** (1.0 / years) - 1.0)


def _calmar(equity_arr: np.ndarray, periods_per_year: int = 252) -> float:
    cagr = _cagr(equity_arr, periods_per_year)
    max_dd = _max_drawdown(equity_arr)
    if max_dd == 0:
        return 0.0
    return cagr / abs(max_dd)


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


def block_bootstrap_max_drawdown(
    equity: pd.Series,
    *,
    block_size: int | None = None,
    n_resamples: int = 1000,
    confidence: float = 0.95,
    seed: int = 0,
) -> MetricCI:
    returns = _equity_to_returns(equity)
    if block_size is None:
        block_size = max(20, len(returns) // 20)
    rng = np.random.default_rng(seed)
    point = _max_drawdown(equity.to_numpy(dtype=float))

    def _sample_dd() -> float:
        r = _block_resample(returns, block_size, rng)
        synthetic_equity = np.cumprod(1.0 + r)
        return _max_drawdown(synthetic_equity)

    samples = np.array([_sample_dd() for _ in range(n_resamples)])
    alpha = 1.0 - confidence
    return MetricCI(
        point=point,
        lower=float(np.quantile(samples, alpha / 2)),
        upper=float(np.quantile(samples, 1 - alpha / 2)),
        confidence=confidence,
    )


def block_bootstrap_sortino(
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
    point = _annualized_sortino(returns, periods_per_year)
    samples = np.array([
        _annualized_sortino(_block_resample(returns, block_size, rng), periods_per_year)
        for _ in range(n_resamples)
    ])
    alpha = 1.0 - confidence
    return MetricCI(
        point=point,
        lower=float(np.quantile(samples, alpha / 2)),
        upper=float(np.quantile(samples, 1 - alpha / 2)),
        confidence=confidence,
    )


def block_bootstrap_cagr(
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
    point = _cagr(equity.to_numpy(dtype=float), periods_per_year)

    def _sample() -> float:
        r = _block_resample(returns, block_size, rng)
        eq = np.cumprod(1.0 + r)
        return _cagr(eq, periods_per_year)

    samples = np.array([_sample() for _ in range(n_resamples)])
    alpha = 1.0 - confidence
    return MetricCI(
        point=point,
        lower=float(np.quantile(samples, alpha / 2)),
        upper=float(np.quantile(samples, 1 - alpha / 2)),
        confidence=confidence,
    )


def block_bootstrap_calmar(
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
    point = _calmar(equity.to_numpy(dtype=float), periods_per_year)

    def _sample() -> float:
        r = _block_resample(returns, block_size, rng)
        eq = np.cumprod(1.0 + r)
        return _calmar(eq, periods_per_year)

    samples = np.array([_sample() for _ in range(n_resamples)])
    alpha = 1.0 - confidence
    return MetricCI(
        point=point,
        lower=float(np.quantile(samples, alpha / 2)),
        upper=float(np.quantile(samples, 1 - alpha / 2)),
        confidence=confidence,
    )


def bootstrap_metrics(
    equity: pd.Series,
    *,
    block_size: int | None = None,
    n_resamples: int = 1000,
    confidence: float = 0.95,
    seed: int = 0,
) -> dict[str, MetricCI]:
    """Bundle: bootstrap Sharpe + MaxDD with shared block_size."""
    if block_size is None:
        block_size = max(20, (len(equity) - 1) // 20)
    return {
        "sharpe": block_bootstrap_sharpe(
            equity, block_size=block_size, n_resamples=n_resamples,
            confidence=confidence, seed=seed,
        ),
        "max_drawdown": block_bootstrap_max_drawdown(
            equity, block_size=block_size, n_resamples=n_resamples,
            confidence=confidence, seed=seed + 1,
        ),
        "sortino": block_bootstrap_sortino(
            equity, block_size=block_size, n_resamples=n_resamples,
            confidence=confidence, seed=seed + 2,
        ),
        "cagr": block_bootstrap_cagr(
            equity, block_size=block_size, n_resamples=n_resamples,
            confidence=confidence, seed=seed + 3,
        ),
        "calmar": block_bootstrap_calmar(
            equity, block_size=block_size, n_resamples=n_resamples,
            confidence=confidence, seed=seed + 4,
        ),
    }
