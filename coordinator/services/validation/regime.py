from __future__ import annotations

import numpy as np
import pandas as pd


def tag_regimes(
    price_series: pd.Series,
    *,
    lookback_days: int = 90,
    bull_threshold: float = 0.15,
    bear_threshold: float = -0.15,
) -> pd.Series:
    """Tag each date as 'bull' / 'bear' / 'chop' by trailing return of `price_series`.

    For warmup (first `lookback_days`), tag is 'chop'.
    """
    ret = price_series.pct_change(periods=lookback_days)
    tags = pd.Series("chop", index=price_series.index, dtype=object)
    tags = tags.where(~(ret > bull_threshold), "bull")
    tags = tags.where(~(ret < bear_threshold), "bear")
    return tags


def regime_conditional_metrics(
    equity: pd.Series,
    regimes: pd.Series,
) -> dict[str, dict[str, float]]:
    """For each regime, compute Sharpe + total return + win rate on `equity`
    restricted to dates tagged with that regime."""
    out: dict[str, dict[str, float]] = {}
    aligned = pd.concat([equity, regimes], axis=1, join="inner")
    aligned.columns = ["equity", "regime"]

    for regime_name in sorted(aligned["regime"].dropna().unique()):
        sub = aligned[aligned["regime"] == regime_name]["equity"]
        if len(sub) < 2:
            out[regime_name] = {"sharpe": 0.0, "total_return": 0.0, "win_rate": 0.0, "n_days": int(len(sub))}
            continue
        rets = sub.pct_change().dropna().to_numpy()
        mu, sigma = float(np.mean(rets)), float(np.std(rets, ddof=1))
        sharpe = (mu / sigma * np.sqrt(252)) if sigma > 0 else 0.0
        out[regime_name] = {
            "sharpe": sharpe,
            "total_return": float(sub.iloc[-1] / sub.iloc[0] - 1.0),
            "win_rate": float(np.mean(rets > 0)),
            "n_days": int(len(sub)),
        }
    return out
