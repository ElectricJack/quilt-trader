"""Pure Black-Scholes math for options pricing.

No pandas, no framework dependencies. Floats in, floats out.
All times are in years (T=0.25 means 3 months).
"""
from __future__ import annotations

import math
from typing import Optional


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1d2(S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, float]:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0, 0.0
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    return d1, d2


def bs_price(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call",
) -> float:
    """European option price via Black-Scholes."""
    if T <= 1e-10:
        if option_type == "call":
            return max(0.0, S - K)
        return max(0.0, K - S)

    d1, d2 = _d1d2(S, K, T, r, sigma)
    discount = math.exp(-r * T)

    if option_type == "call":
        return S * _norm_cdf(d1) - K * discount * _norm_cdf(d2)
    else:
        return K * discount * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_iv(
    price: float, S: float, K: float, T: float, r: float,
    option_type: str = "call",
    tol: float = 1e-6,
    max_sigma: float = 5.0,
) -> Optional[float]:
    """Invert Black-Scholes to find implied volatility.

    Returns None if no valid IV exists.
    """
    if price <= 0 or T <= 1e-10 or S <= 0 or K <= 0:
        return None

    intrinsic = max(0.0, S - K) if option_type == "call" else max(0.0, K - S)
    if price < intrinsic - tol:
        return None

    from scipy.optimize import brentq

    def objective(sigma):
        return bs_price(S, K, T, r, sigma, option_type) - price

    try:
        low_val = objective(1e-6)
        high_val = objective(max_sigma)
        if low_val * high_val > 0:
            return None
        return brentq(objective, 1e-6, max_sigma, xtol=tol)
    except (ValueError, RuntimeError):
        return None


def bs_greeks(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call",
) -> dict[str, float]:
    """Compute BS Greeks: delta, gamma, theta (per day), vega (per 1% vol)."""
    if T <= 1e-10 or sigma <= 0:
        delta = 1.0 if (option_type == "call" and S > K) else (-1.0 if (option_type == "put" and S < K) else 0.0)
        return {"delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0}

    d1, d2 = _d1d2(S, K, T, r, sigma)
    sqrt_t = math.sqrt(T)
    discount = math.exp(-r * T)
    pdf_d1 = _norm_pdf(d1)

    gamma = pdf_d1 / (S * sigma * sqrt_t)
    vega = S * pdf_d1 * sqrt_t / 100.0

    if option_type == "call":
        delta = _norm_cdf(d1)
        theta = (
            -(S * pdf_d1 * sigma) / (2.0 * sqrt_t)
            - r * K * discount * _norm_cdf(d2)
        ) / 365.0
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (
            -(S * pdf_d1 * sigma) / (2.0 * sqrt_t)
            + r * K * discount * _norm_cdf(-d2)
        ) / 365.0

    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


def estimate_spread(price: float, volume: int, base_pct: float = 0.10) -> float:
    """Estimate bid-ask spread from price and volume.

    Model: spread = price * base_pct / (1 + ln(1 + volume))
    Minimum $0.01, capped at 2 * price.
    """
    if price <= 0:
        return 0.01
    log_factor = 1.0 + math.log(1.0 + volume) if volume > 0 else 1.0
    spread = price * base_pct / log_factor
    spread = max(spread, 0.01)
    spread = min(spread, 2.0 * price)
    return spread
