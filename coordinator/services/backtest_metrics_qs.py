"""QuantStats-backed metrics wrapper.

Same surface as `backtest_metrics.compute_all` so the runner can swap
the import without code changes downstream. We keep our trade-based
metrics (win_rate, profit_factor, expectancy, streaks) since qs doesn't
model round-trip trades.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd
import quantstats as qs


# ---- Equity-curve metrics (qs-backed) ----

def _returns_series(df: pd.DataFrame) -> pd.Series:
    if df.empty or "return" not in df.columns:
        return pd.Series(dtype=float)
    s = df["return"].copy()
    if hasattr(s.index, "tz") and s.index.tz is not None:
        s.index = s.index.tz_localize(None)
    return s


def total_return(df: pd.DataFrame, initial_cash: float) -> float:
    if df.empty:
        return 0.0
    final = float(df["portfolio_value"].iloc[-1])
    return (final / initial_cash) - 1.0


def cagr(df: pd.DataFrame) -> float:
    s = _returns_series(df)
    if s.empty or len(s) < 2:
        return 0.0
    try:
        return float(qs.stats.cagr(s))
    except (ValueError, ZeroDivisionError):
        return 0.0


def volatility(df: pd.DataFrame) -> float:
    s = _returns_series(df)
    if s.empty or len(s) < 2:
        return 0.0
    try:
        return float(qs.stats.volatility(s, annualize=True))
    except (ValueError, ZeroDivisionError):
        return 0.0


def sharpe_ratio(df: pd.DataFrame, risk_free_rate: float = 0.04) -> float:
    s = _returns_series(df)
    if s.empty or len(s) < 2:
        return 0.0
    try:
        result = float(qs.stats.sharpe(s, rf=risk_free_rate))
        return 0.0 if math.isnan(result) or math.isinf(result) else result
    except (ValueError, ZeroDivisionError):
        return 0.0


def sortino_ratio(df: pd.DataFrame, risk_free_rate: float = 0.04) -> float:
    s = _returns_series(df)
    if s.empty or len(s) < 2:
        return 0.0
    try:
        result = float(qs.stats.sortino(s, rf=risk_free_rate))
        return 0.0 if math.isnan(result) else result
    except (ValueError, ZeroDivisionError):
        return 0.0


def calmar_ratio(df: pd.DataFrame) -> float:
    s = _returns_series(df)
    if s.empty or len(s) < 2:
        return 0.0
    try:
        result = float(qs.stats.calmar(s))
        return 0.0 if math.isnan(result) else result
    except (ValueError, ZeroDivisionError):
        return 0.0


def max_drawdown(df: pd.DataFrame) -> dict:
    s = _returns_series(df)
    if s.empty:
        return {"drawdown": 0.0, "date": None}
    try:
        dd_series = qs.stats.to_drawdown_series(s)
        dd = float(abs(dd_series.min()))
        if math.isnan(dd):
            return {"drawdown": 0.0, "date": None}
        return {"drawdown": dd, "date": dd_series.idxmin()}
    except (ValueError, ZeroDivisionError):
        return {"drawdown": 0.0, "date": None}


def romad(df: pd.DataFrame) -> float:
    md = max_drawdown(df)
    if md["drawdown"] == 0:
        return 0.0
    return cagr(df) / md["drawdown"]


def longest_drawdown_days(df: pd.DataFrame) -> int:
    s = _returns_series(df)
    if s.empty:
        return 0
    try:
        dd_series = qs.stats.to_drawdown_series(s)
        details = qs.stats.drawdown_details(dd_series)
        if details is None or len(details) == 0:
            return 0
        return int(details["days"].max())
    except (ValueError, ZeroDivisionError, AttributeError):
        return 0


def top_n_drawdowns(df: pd.DataFrame, n: int = 10) -> list[dict]:
    s = _returns_series(df)
    if s.empty:
        return []
    try:
        dd_series = qs.stats.to_drawdown_series(s)
        details = qs.stats.drawdown_details(dd_series)
        if details is None or len(details) == 0:
            return []
        # Sort by max drawdown magnitude (descending)
        sorted_dd = details.reindex(
            details["max drawdown"].abs().sort_values(ascending=False).index
        )
        out = []
        for _, row in sorted_dd.head(n).iterrows():
            out.append({
                "start": pd.Timestamp(row["start"]).isoformat(),
                "trough": pd.Timestamp(row.get("valley", row["start"])).isoformat(),
                "recovered": pd.Timestamp(row["end"]).isoformat() if pd.notna(row.get("end")) else None,
                "depth": float(abs(row["max drawdown"]) / 100.0),  # qs returns percent
                "days": int(row["days"]),
            })
        return out
    except (ValueError, ZeroDivisionError, AttributeError, KeyError):
        return []


# ---- Trade-based metrics (kept from original module) ----

def round_trip_trades(trades: list[dict]) -> list[dict]:
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


def _safe_qs(fn, *args, default=None, **kwargs):
    """Call a qs.stats function; return default on any failure or NaN/Inf."""
    try:
        v = float(fn(*args, **kwargs))
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def _avg_drawdown_stats(df: pd.DataFrame) -> dict:
    """Average drawdown depth and duration across all drawdown periods."""
    s = _returns_series(df)
    if s.empty:
        return {"depth": 0.0, "days": 0.0}
    try:
        details = qs.stats.drawdown_details(qs.stats.to_drawdown_series(s))
        if details is None or len(details) == 0:
            return {"depth": 0.0, "days": 0.0}
        depth = float(details["max drawdown"].abs().mean()) / 100.0
        days = float(details["days"].mean())
        return {"depth": depth, "days": days}
    except Exception:
        return {"depth": 0.0, "days": 0.0}


def _period_return(daily_df: pd.DataFrame, *, days: int | None = None,
                   ytd: bool = False, annualize_after_days: int | None = None) -> float | None:
    """Return cumulative (or annualized) return over the trailing window.

    - ytd: from Jan 1 of the final year through the end.
    - days: trailing N calendar days through the end.
    - annualize_after_days: if the realized window >= this many days, return CAGR;
      otherwise return cumulative.
    """
    if daily_df.empty:
        return None
    pv = daily_df.set_index("timestamp")["portfolio_value"].sort_index()
    if pv.empty:
        return None
    end = pv.index[-1]
    if ytd:
        start_idx = pv.index[pv.index >= pd.Timestamp(end.year, 1, 1)]
        if len(start_idx) == 0:
            return None
        start_pv = float(pv.loc[start_idx[0]])
    else:
        if days is None:
            return None
        cutoff = end - pd.Timedelta(days=days)
        window = pv.loc[pv.index >= cutoff]
        if len(window) < 2:
            return None
        start_pv = float(window.iloc[0])
    end_pv = float(pv.iloc[-1])
    if start_pv <= 0:
        return None
    cum = end_pv / start_pv - 1.0
    if annualize_after_days is not None and days is not None and days >= annualize_after_days:
        years = days / 365.25
        if years > 0:
            return (end_pv / start_pv) ** (1 / years) - 1.0
    return cum


def compute_all(
    df: pd.DataFrame, trades: list[dict], *,
    initial_cash: float, risk_free_rate: float = 0.04,
) -> dict[str, Any]:
    md = max_drawdown(df)
    s = _returns_series(df)
    avg_dd = _avg_drawdown_stats(df)
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
        # Extended distribution / risk metrics from qs.stats
        "omega": _safe_qs(qs.stats.omega, s, default=0.0) if not s.empty else 0.0,
        "ulcer_index": _safe_qs(qs.stats.ulcer_index, s, default=0.0) if not s.empty else 0.0,
        "daily_var": _safe_qs(qs.stats.value_at_risk, s, default=0.0) if not s.empty else 0.0,
        "daily_cvar": _safe_qs(qs.stats.cvar, s, default=0.0) if not s.empty else 0.0,
        "skew": _safe_qs(qs.stats.skew, s, default=0.0) if not s.empty else 0.0,
        "kurtosis": _safe_qs(qs.stats.kurtosis, s, default=0.0) if not s.empty else 0.0,
        "best_day": _safe_qs(qs.stats.best, s, default=0.0) if not s.empty else 0.0,
        "worst_day": _safe_qs(qs.stats.worst, s, default=0.0) if not s.empty else 0.0,
        "best_month": _safe_qs(qs.stats.best, s, "M", default=0.0) if not s.empty else 0.0,
        "worst_month": _safe_qs(qs.stats.worst, s, "M", default=0.0) if not s.empty else 0.0,
        "time_in_market": _safe_qs(qs.stats.exposure, s, default=0.0) if not s.empty else 0.0,
        "win_days": _safe_qs(qs.stats.win_rate, s, default=0.0) if not s.empty else 0.0,
        "win_month": _safe_qs(qs.stats.win_rate, s, "M", default=0.0) if not s.empty else 0.0,
        "avg_drawdown": avg_dd["depth"],
        "avg_drawdown_days": avg_dd["days"],
        "trade_count": len(round_trip_trades(trades)),
        "win_rate": win_rate(trades),
        "profit_factor": profit_factor(trades),
        "avg_win": avg_win(trades),
        "avg_loss": avg_loss(trades),
        "expectancy": expectancy(trades),
        "longest_drawdown_days": longest_drawdown_days(df),
        "longest_winning_streak": longest_streak(trades, win=True),
        "longest_losing_streak": longest_streak(trades, win=False),
        "drawdown_periods": top_n_drawdowns(df, n=10),
    }
