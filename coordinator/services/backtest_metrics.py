"""Backtest performance metrics.

Functions accept a daily-resampled DataFrame with a 'return' column
and/or a list of trade dicts with 'realized_pnl'. Pure math; no I/O,
no DB. Matches Lumibot's metric set (lumibot/tools/indicators.py) plus
Sortino, Calmar, win-rate, profit-factor, expectancy, streak/drawdown
period analytics.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Optional

import pandas as pd


# ---- Equity-curve metrics ----

def total_return(df: pd.DataFrame, initial_cash: float) -> float:
    if df.empty:
        return 0.0
    final = df["portfolio_value"].iloc[-1]
    return (final / initial_cash) - 1.0


def cagr(df: pd.DataFrame) -> float:
    if df.empty or len(df) < 2:
        return 0.0
    df_sorted = df.sort_index()
    cum = (1 + df_sorted["return"]).cumprod()
    total = float(cum.iloc[-1])
    start = df_sorted.index[0]
    end = df_sorted.index[-1]
    days = (end - start).days
    if days == 0:
        return 0.0
    period_years = days / 365.25
    if total <= 0:
        return -1.0
    return total ** (1 / period_years) - 1


def volatility(df: pd.DataFrame) -> float:
    if df.empty or len(df) < 2:
        return 0.0
    df_sorted = df.sort_index()
    start = df_sorted.index[0]
    end = df_sorted.index[-1]
    days = (end - start).days
    if days == 0:
        return 0.0
    period_years = days / 365.25
    ratio = df_sorted["return"].count() / period_years
    return float(df_sorted["return"].std() * math.sqrt(ratio))


def sharpe_ratio(df: pd.DataFrame, risk_free_rate: float = 0.04) -> float:
    vol = volatility(df)
    if vol == 0:
        return 0.0
    return (cagr(df) - risk_free_rate) / vol


def sortino_ratio(df: pd.DataFrame, risk_free_rate: float = 0.04) -> float:
    if df.empty or len(df) < 2:
        return 0.0
    excess = cagr(df) - risk_free_rate
    df_sorted = df.sort_index()
    # Downside deviation: root-mean-square of negative-only returns (target=0).
    # Use the canonical Sortino formulation rather than std-of-negatives so that
    # a single downside observation still produces a finite, non-zero result.
    negative_returns = df_sorted["return"].clip(upper=0)
    if (negative_returns == 0).all():
        # No downside volatility — Sortino is conventionally infinite when
        # excess return is positive, zero when zero, -inf when negative.
        if excess > 0:
            return float("inf")
        if excess < 0:
            return float("-inf")
        return 0.0
    days = (df_sorted.index[-1] - df_sorted.index[0]).days
    period_years = max(days / 365.25, 1e-9)
    downside_dev = float(math.sqrt((negative_returns ** 2).mean()))
    downside_vol = downside_dev * math.sqrt(df_sorted["return"].count() / period_years)
    if downside_vol == 0:
        if excess > 0:
            return float("inf")
        if excess < 0:
            return float("-inf")
        return 0.0
    return excess / downside_vol


def max_drawdown(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"drawdown": 0.0, "date": None}
    df_sorted = df.sort_index().copy()
    cum = (1 + df_sorted["return"]).cumprod()
    cum_max = cum.cummax()
    drawdown = (cum_max - cum) / cum_max
    dd_max = float(drawdown.max())
    if math.isnan(dd_max):
        return {"drawdown": 0.0, "date": None}
    return {"drawdown": dd_max, "date": drawdown.idxmax()}


def romad(df: pd.DataFrame) -> float:
    md = max_drawdown(df)
    if md["drawdown"] == 0:
        return 0.0
    return cagr(df) / md["drawdown"]


def calmar_ratio(df: pd.DataFrame) -> float:
    """Same as RoMaD but with explicit naming."""
    return romad(df)


def longest_drawdown_days(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    df_sorted = df.sort_index()
    cum = (1 + df_sorted["return"]).cumprod()
    cum_max = cum.cummax()
    underwater = cum < cum_max
    if not underwater.any():
        return 0
    # Group consecutive runs of underwater periods
    longest = 0
    current = 0
    for u in underwater:
        if u:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def top_n_drawdowns(df: pd.DataFrame, n: int = 5) -> list[dict]:
    """Return top-n drawdown periods sorted by depth, with start/end/recovery."""
    if df.empty:
        return []
    df_sorted = df.sort_index()
    cum = (1 + df_sorted["return"]).cumprod()
    cum_max = cum.cummax()
    drawdown_pct = (cum_max - cum) / cum_max

    periods = []
    in_dd = False
    start_idx = None
    peak_idx = None
    trough_idx = None
    trough_dd = 0.0
    for ts, dd in drawdown_pct.items():
        if dd > 0 and not in_dd:
            in_dd = True
            start_idx = ts
            peak_idx = ts
            trough_idx = ts
            trough_dd = dd
        elif dd > 0 and in_dd:
            if dd > trough_dd:
                trough_idx = ts
                trough_dd = dd
        elif dd == 0 and in_dd:
            in_dd = False
            periods.append({
                "start": start_idx.isoformat(),
                "trough": trough_idx.isoformat(),
                "recovered": ts.isoformat(),
                "depth": float(trough_dd),
            })
            start_idx = peak_idx = trough_idx = None
            trough_dd = 0.0
    if in_dd:  # Ongoing drawdown at end of series
        periods.append({
            "start": start_idx.isoformat(),
            "trough": trough_idx.isoformat(),
            "recovered": None,
            "depth": float(trough_dd),
        })
    periods.sort(key=lambda p: p["depth"], reverse=True)
    return periods[:n]


# ---- Trade-based metrics ----

def round_trip_trades(trades: list[dict]) -> list[dict]:
    """Return trades that have a non-null realized_pnl (i.e., closed positions).

    The engine writes one trade dict per fill, but only the closing fills
    carry realized_pnl. v1 keeps it simple: any trade with realized_pnl
    counts as one round-trip.
    """
    return [t for t in trades if t.get("realized_pnl") is not None]


def win_rate(trades: list[dict]) -> float:
    rts = round_trip_trades(trades)
    if not rts:
        return 0.0
    wins = sum(1 for t in rts if t["realized_pnl"] > 0)
    return wins / len(rts)


def profit_factor(trades: list[dict]) -> float:
    rts = round_trip_trades(trades)
    gross_profit = sum(t["realized_pnl"] for t in rts if t["realized_pnl"] > 0)
    gross_loss = abs(sum(t["realized_pnl"] for t in rts if t["realized_pnl"] < 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def avg_win(trades: list[dict]) -> float:
    wins = [t["realized_pnl"] for t in round_trip_trades(trades) if t["realized_pnl"] > 0]
    return sum(wins) / len(wins) if wins else 0.0


def avg_loss(trades: list[dict]) -> float:
    losses = [t["realized_pnl"] for t in round_trip_trades(trades) if t["realized_pnl"] < 0]
    return sum(losses) / len(losses) if losses else 0.0


def expectancy(trades: list[dict]) -> float:
    wr = win_rate(trades)
    return wr * avg_win(trades) + (1 - wr) * avg_loss(trades)


def longest_streak(trades: list[dict], *, win: bool) -> int:
    rts = round_trip_trades(trades)
    longest = 0
    current = 0
    for t in rts:
        is_win = t["realized_pnl"] > 0
        if (win and is_win) or (not win and not is_win):
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def compute_all(
    df: pd.DataFrame, trades: list[dict], *,
    initial_cash: float, risk_free_rate: float = 0.04,
) -> dict[str, Any]:
    md = max_drawdown(df)
    return {
        "total_return": total_return(df, initial_cash),
        "cagr": cagr(df),
        "volatility": volatility(df),
        "sharpe_ratio": sharpe_ratio(df, risk_free_rate),
        "sortino_ratio": sortino_ratio(df, risk_free_rate),
        "calmar_ratio": calmar_ratio(df),
        "max_drawdown": md["drawdown"],
        "max_drawdown_date": md["date"],
        "romad": romad(df),
        "trade_count": len(round_trip_trades(trades)),
        "win_rate": win_rate(trades),
        "profit_factor": profit_factor(trades),
        "avg_win": avg_win(trades),
        "avg_loss": avg_loss(trades),
        "expectancy": expectancy(trades),
        "longest_drawdown_days": longest_drawdown_days(df),
        "longest_winning_streak": longest_streak(trades, win=True),
        "longest_losing_streak": longest_streak(trades, win=False),
        "drawdown_periods": top_n_drawdowns(df, n=5),
    }
