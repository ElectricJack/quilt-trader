"""Failing tests (TDD red phase) for correctness-audit findings F4, F5, F6,
F20b (options math / options asset service), F8, F9 (metrics), F10
(bootstrap), F20a (multiple-testing correction).

Source: docs/superpowers/research/2026-06-11-correctness-audit-findings.md
Each test asserts the CORRECT behavior; it fails today because the bug exists
and must pass once the production code is fixed. No production code modified.
"""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from coordinator.services.options_math import bs_iv, bs_price
from coordinator.services.asset_services.options import OptionsAssetService
from coordinator.services.metrics_engine import MetricsEngine
from coordinator.services.validation.bootstrap import (
    MetricCI,
    _annualized_sortino,
    block_bootstrap_sharpe,
)
from coordinator.services.validation.multi_test import correct


# ---------------------------------------------------------------------------
# F4 — options_math.bs_price malformed for sigma <= 0
# ---------------------------------------------------------------------------

def test_f4_bs_price_zero_sigma_itm_call_is_discounted_intrinsic():
    """F4: bs_price has no sigma<=0 branch (options_math.py:19-25 _d1d2
    returns (0,0)), so a sigma=0 call prices at 0.5*S - 0.5*K*e^(-rT).

    Correct deterministic value for sigma=0 is the discounted intrinsic
    S - K*e^(-rT) (see options_mtm.black_scholes_price). Buggy: ~7.20.
    """
    px = bs_price(S=110.0, K=100.0, T=1.0, r=0.045, sigma=0.0, option_type="call")
    expected = 110.0 - 100.0 * math.exp(-0.045 * 1.0)  # ~14.400298
    assert px == pytest.approx(expected, abs=1e-6)


def test_f4_bs_price_zero_sigma_otm_call_is_zero_and_never_negative():
    """F4: bs_price (options_math.py:28-44) returns a NEGATIVE price
    (~-2.78) for an OTM call with sigma=0. An option price must be >= 0;
    the correct sigma=0 value here is max(S - K*e^(-rT), 0) = 0.0.
    """
    px = bs_price(S=90.0, K=100.0, T=1.0, r=0.045, sigma=0.0, option_type="call")
    assert px >= 0.0, f"option price must never be negative, got {px}"
    assert px == pytest.approx(0.0, abs=1e-12)


# ---------------------------------------------------------------------------
# F5 — options_math.bs_iv rejects valid deep-ITM European puts
# ---------------------------------------------------------------------------

def test_f5_bs_iv_accepts_deep_itm_european_put_below_undiscounted_intrinsic():
    """F5: bs_iv (options_math.py:60-62) floors acceptable prices at the
    UNDISCOUNTED intrinsic max(K-S, 0). A European deep-ITM put fairly
    trades between discounted intrinsic K*e^(-rT)-S (~45.60 here) and
    undiscounted K-S (=50), so a 46.0 quote is valid and must invert to a
    positive IV. Buggy: returns None.
    """
    iv = bs_iv(price=46.0, S=50.0, K=100.0, T=1.0, r=0.045, option_type="put")
    assert iv is not None, "valid deep-ITM put quote must produce an IV, not None"
    assert iv > 0.0


# ---------------------------------------------------------------------------
# F6 — handle_expiry masks missing underlying price with the strike
# ---------------------------------------------------------------------------

def test_f6_handle_expiry_missing_underlying_price_raises():
    """F6: asset_services/options.py:176-177 substitutes parsed['strike']
    when the underlying price is unavailable at expiry, forcing intrinsic
    to 0 — a short ITM option's liability is silently erased. Correct
    behavior: a missing underlying price at expiry is an error
    (ValueError). Buggy: returns a worthless-expiry Settlement.
    """
    svc = OptionsAssetService()
    # Short 2 puts, expiry 2024-01-19; ctx=None means no underlying price
    # is resolvable (svc._get_underlying_price returns None).
    with pytest.raises(ValueError):
        svc.handle_expiry(
            symbol="XYZ240119P00100000",
            quantity=-2.0,
            avg_price=1.50,
            sim_time=datetime(2024, 1, 22, 21, 0),
            ctx=None,
        )


# ---------------------------------------------------------------------------
# F20b — compute_unrealized_pnl returns 0.0 for short options
# ---------------------------------------------------------------------------

def test_f20b_compute_unrealized_pnl_short_option_position():
    """F20b: asset_services/options.py:131-137 guards on market_value > 0,
    which is false for shorts (negative market value), so short option
    unrealized PnL is silently reported as 0.0.

    Short 1 contract sold at 5.00, now trading at 3.00, multiplier 100:
    market_value = 3.00 * (-1) * 100 = -300; unrealized must be
    (5 - 3) * 1 * 100 = +200. Buggy: 0.0.
    """
    svc = OptionsAssetService()
    pnl = svc.compute_unrealized_pnl(
        symbol="XYZ240119C00100000",
        quantity=-1.0,
        avg_price=5.0,
        market_value=-300.0,
    )
    assert pnl == pytest.approx(200.0)


# ---------------------------------------------------------------------------
# F8 — Sortino computed two different nonstandard ways
# ---------------------------------------------------------------------------

def _equity_curve_from_returns(returns: list[float], start: float = 100.0) -> list[dict]:
    equities = [start]
    for r in returns:
        equities.append(equities[-1] * (1.0 + r))
    return [
        {"timestamp": f"2024-01-{i + 1:02d}T00:00:00Z", "equity": eq}
        for i, eq in enumerate(equities)
    ]


def _standard_sortino(returns: list[float], rf: float = 0.0) -> float:
    """Standard Sortino: downside_dev = sqrt(mean over ALL returns of
    min(r,0)^2); Sortino = (mean*252 - rf) / (downside_dev * sqrt(252))."""
    arr = np.asarray(returns, dtype=float)
    downside_dev = math.sqrt(float(np.mean(np.minimum(arr, 0.0) ** 2)))
    return (float(arr.mean()) * 252.0 - rf) / (downside_dev * math.sqrt(252.0))


def test_f8_metrics_engine_sortino_uses_standard_downside_deviation():
    """F8: metrics_engine.py:57-61 divides the downside variance by the
    COUNT OF NEGATIVE returns instead of the total count. With returns
    [0.30, -0.10, -0.10]: standard downside_dev = sqrt(0.02/3) ~ 0.081650
    -> Sortino ~ 6.48; buggy uses sqrt(0.02/2) = 0.10 -> 5.29.
    """
    returns = [0.30, -0.10, -0.10]
    metrics = MetricsEngine.compute(
        equity_curve=_equity_curve_from_returns(returns),
        positions=[],
        risk_free_rate=0.0,
    )
    expected = _standard_sortino(returns)  # ~6.4807
    assert metrics["sortino_ratio"] == pytest.approx(expected, abs=0.01)


def test_f8_bootstrap_sortino_matches_standard_definition():
    """F8: validation/bootstrap.py:49-59 _annualized_sortino uses
    np.std(downside, ddof=1) — the centered sample std of only the negative
    returns — a third Sortino definition, inconsistent with the (intended)
    backtest-report formula. It must equal the standard value:
    mean / sqrt(mean over ALL returns of min(r,0)^2) * sqrt(252).
    Buggy here: std of two identical negatives is 0 -> returns 0.0.
    """
    returns = np.array([0.30, -0.10, -0.10])
    result = _annualized_sortino(returns)
    expected = _standard_sortino(list(returns))  # ~6.4807
    assert result == pytest.approx(expected, rel=1e-9)


def test_f8_bootstrap_sortino_finite_with_single_negative_return():
    """F8: validation/bootstrap.py:56 np.std(downside, ddof=1) of a SINGLE
    negative return is NaN, so _annualized_sortino returns NaN. It must
    return a finite number per the standard definition.
    """
    returns = np.array([0.05, -0.10, 0.02])
    result = _annualized_sortino(returns)
    assert math.isfinite(result), f"sortino must be finite, got {result}"
    expected = _standard_sortino(list(returns))  # ~-2.7495
    assert result == pytest.approx(expected, rel=1e-9)


# ---------------------------------------------------------------------------
# F9 — max_dd_duration sentinel bugs in metrics_engine
# ---------------------------------------------------------------------------

def _curve(equities: list[float]) -> list[dict]:
    return [
        {"timestamp": f"2024-01-{i + 1:02d}T00:00:00Z", "equity": eq}
        for i, eq in enumerate(equities)
    ]


def test_f9_max_dd_duration_counts_drawdown_starting_at_index_zero():
    """F9: metrics_engine.py:70-86 uses current_dd_start == 0 as the
    not-in-drawdown sentinel, so a drawdown anchored at the initial peak
    (index 0) is mismeasured. Curve [100, 90, 95, 101]: peak at index 0,
    recovery at index 3 -> duration 3 bars (daily curve -> 3 days).
    Buggy: the sentinel forces the start to index 1 -> reports 2.
    """
    metrics = MetricsEngine.compute(
        equity_curve=_curve([100.0, 90.0, 95.0, 101.0]),
        positions=[],
    )
    assert metrics["max_drawdown_duration_days"] == 3


def test_f9_max_dd_duration_flushes_open_trailing_drawdown():
    """F9: metrics_engine.py:72-86 never flushes a drawdown still open at
    the end of the series. Curve [100, 90, 80, 70]: an unrecovered
    drawdown runs from the peak (index 0) to the end (index 3) -> 3 bars.
    Buggy: 0.
    """
    metrics = MetricsEngine.compute(
        equity_curve=_curve([100.0, 90.0, 80.0, 70.0]),
        positions=[],
    )
    assert metrics["max_drawdown_duration_days"] == 3


# ---------------------------------------------------------------------------
# F10 — bootstrap _block_resample crashes for short series
# ---------------------------------------------------------------------------

def test_f10_block_bootstrap_sharpe_handles_series_shorter_than_block():
    """F10: validation/bootstrap.py:41-46 _block_resample calls
    rng.integers(0, n - block_size + 1), which raises ValueError when the
    default block_size (>= 20) exceeds the number of returns. A 5-point
    equity curve (4 returns) must still produce a MetricCI, not crash.
    """
    equity = pd.Series([100.0, 101.0, 99.0, 102.0, 103.0])
    ci = block_bootstrap_sharpe(equity, n_resamples=25, seed=0)
    assert isinstance(ci, MetricCI)
    assert math.isfinite(ci.point)
    assert ci.lower <= ci.upper
    assert ci.confidence == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# F20a — Benjamini-Hochberg adjusted p-values not monotone
# ---------------------------------------------------------------------------

def test_f20a_bh_corrected_p_values_are_monotone_nondecreasing():
    """F20a: validation/multi_test.py:76-78 computes BH corrected_p as
    raw*n/rank WITHOUT the cumulative-min enforcement, so adjusted
    p-values can decrease as raw p-values increase. For raw
    [0.01, 0.02, 0.025, 0.04], n=4 the correct adjusted values are
    [0.0333.., 0.0333.., 0.0333.., 0.04]; buggy yields
    [0.04, 0.04, 0.0333.., 0.04] (non-monotone).
    """
    raw = [0.01, 0.02, 0.025, 0.04]
    results = correct(raw_p_values=raw, n_tested=4, method="bh")

    by_raw = sorted(results, key=lambda r: r.raw_p)
    corrected_sorted = [r.corrected_p for r in by_raw]
    for i in range(1, len(corrected_sorted)):
        assert corrected_sorted[i] >= corrected_sorted[i - 1] - 1e-12, (
            f"BH adjusted p-values must be non-decreasing in raw-p order, "
            f"got {corrected_sorted}"
        )
    expected = [1.0 / 30.0, 1.0 / 30.0, 1.0 / 30.0, 0.04]
    assert corrected_sorted == pytest.approx(expected)
