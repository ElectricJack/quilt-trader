"""Regime tagging — partition a date index into discrete regimes (bull/bear/
chop or whatever taxonomy the tagger defines) so that backtest equity can
be sliced by regime to surface per-regime Sharpe / drawdown / win-rate.

A tagger is any callable accepting a (timestamp-indexed) reference series
and returning a same-indexed string-valued tag series. Three taggers ship:

- :func:`tag_regimes_by_trailing_return` (default): bull/bear/chop from the
  trailing N-day return of a single reference series (typically BTC for
  crypto strategies).
- :func:`tag_regimes_by_vix`: low_vol/mid_vol/high_vol from VIX percentiles.
- :class:`FunctionTagger`: wraps an arbitrary user function so a strategy
  can declare its own tagger in research notebooks without re-importing.

For backward compatibility, the original public function name
``tag_regimes`` is preserved as an alias for trailing-return tagging.
"""
from __future__ import annotations

from typing import Callable, Protocol

import numpy as np
import pandas as pd


class RegimeTagger(Protocol):
    """Callable accepting a reference price/level series and returning a
    same-indexed Series of string regime tags."""

    def __call__(self, reference: pd.Series) -> pd.Series: ...


# ---------------------------------------------------------------------------
# Built-in taggers
# ---------------------------------------------------------------------------

def tag_regimes_by_trailing_return(
    price_series: pd.Series,
    *,
    lookback_days: int = 90,
    bull_threshold: float = 0.15,
    bear_threshold: float = -0.15,
) -> pd.Series:
    """Tag each date as 'bull' / 'bear' / 'chop' by trailing N-day return.

    For warmup (first `lookback_days`), tag is 'chop'. Caller typically
    passes BTC or SPY closes — whichever index best characterizes the
    market regime relevant to the strategy.
    """
    ret = price_series.pct_change(periods=lookback_days)
    tags = pd.Series("chop", index=price_series.index, dtype=object)
    tags = tags.where(~(ret > bull_threshold), "bull")
    tags = tags.where(~(ret < bear_threshold), "bear")
    return tags


def tag_regimes_by_vix(
    vix_series: pd.Series,
    *,
    low_pctile: float = 0.33,
    high_pctile: float = 0.67,
) -> pd.Series:
    """Tag each date as 'low_vol' / 'mid_vol' / 'high_vol' from VIX percentiles.

    Percentiles are computed over the entire series (in-sample). For a
    strategy whose research period is the same as the backtest period, this
    is fine. For an OOS-pure design, the caller should pre-compute the
    percentile thresholds on the train window and pass a wrapped tagger
    with absolute VIX thresholds instead.
    """
    if vix_series.empty:
        return pd.Series(dtype=object)
    lo = vix_series.quantile(low_pctile)
    hi = vix_series.quantile(high_pctile)
    tags = pd.Series("mid_vol", index=vix_series.index, dtype=object)
    tags = tags.where(~(vix_series < lo), "low_vol")
    tags = tags.where(~(vix_series > hi), "high_vol")
    return tags


class FunctionTagger:
    """Wraps an arbitrary callable as a RegimeTagger.

    Use when an algorithm wants a one-off custom rule and doesn't justify a
    dedicated module — e.g., 'tag by sign of yield-curve spread' in a
    research notebook.
    """

    def __init__(self, fn: Callable[[pd.Series], pd.Series], *, name: str = "custom"):
        self._fn = fn
        self.name = name

    def __call__(self, reference: pd.Series) -> pd.Series:
        out = self._fn(reference)
        if not isinstance(out, pd.Series):
            raise TypeError(f"FunctionTagger {self.name!r}: expected pd.Series, got {type(out).__name__}")
        return out


# ---------------------------------------------------------------------------
# Backward-compat alias
# ---------------------------------------------------------------------------

def tag_regimes(
    price_series: pd.Series,
    *,
    lookback_days: int = 90,
    bull_threshold: float = 0.15,
    bear_threshold: float = -0.15,
) -> pd.Series:
    """Default tagger — preserved name for backward compatibility.

    Delegates to :func:`tag_regimes_by_trailing_return`.
    """
    return tag_regimes_by_trailing_return(
        price_series,
        lookback_days=lookback_days,
        bull_threshold=bull_threshold,
        bear_threshold=bear_threshold,
    )


# ---------------------------------------------------------------------------
# Conditional metrics — tagger-agnostic
# ---------------------------------------------------------------------------

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
