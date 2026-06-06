"""Conservative options-MTM helper for backtest valuation.

Used when live chain mid is unavailable. Produces a Black-Scholes
estimate with a direction-aware envelope so no algorithm can exploit
chain-data sparseness to mis-size positions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from scipy.stats import norm

RISK_FREE_RATE = 0.045
FALLBACK_SIGMA = 0.40


@dataclass
class _IVCacheEntry:
    sim_time: datetime
    iv: float


@dataclass
class _MidCacheEntry:
    sim_time: datetime
    mid: float


def black_scholes_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str,
) -> float:
    """Black-Scholes price for a European option.

    Args:
        S: underlying price
        K: strike
        T: time to expiry in years (≤ 0 returns intrinsic)
        r: risk-free rate
        sigma: implied volatility (≤ 0 returns discounted intrinsic)
        option_type: "call"/"C" or "put"/"P" (case-insensitive)

    Returns:
        Theoretical option price ≥ 0.
    """
    is_call = option_type[0].upper() == "C"

    # Expiration / past-expiration: return intrinsic
    if T <= 0:
        if is_call:
            return max(S - K, 0.0)
        return max(K - S, 0.0)

    # Zero vol: discounted intrinsic (the deterministic value)
    if sigma <= 0:
        if is_call:
            return max(S - K * math.exp(-r * T), 0.0)
        return max(K * math.exp(-r * T) - S, 0.0)

    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    if is_call:
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


class OptionsMTMHelper:
    """Per-run helper: caches IVs/mids from live chain reads and produces
    a conservative MTM estimate when chain data is unavailable.

    Construct one per BacktestEngine.run(). No persistence; rebuilt each
    run.
    """

    def __init__(self) -> None:
        # Tier 1: exact OCC symbol → most recent IV observation
        self._iv_by_symbol: dict[str, _IVCacheEntry] = {}
        # Tier 2: (underlying, expiration ISO date) → most recent IV
        self._iv_by_expiry: dict[tuple[str, str], _IVCacheEntry] = {}
        # Tier 3: underlying → most recent ATM-ish IV (any contract seen)
        self._iv_by_underlying: dict[str, _IVCacheEntry] = {}
        # Last-known mid per OCC symbol
        self._mid_by_symbol: dict[str, _MidCacheEntry] = {}

    def observe(
        self,
        symbol: str,
        mid: float,
        iv: float,
        sim_time: datetime,
        underlying: str,
        expiration_str: str,
    ) -> None:
        """Populate caches from a successful live chain read.

        Non-positive iv or mid is dropped to avoid poisoning the cache
        with bad data — but the two are independent (a row with good mid
        and bad iv still updates the mid cache).
        """
        if mid > 0:
            self._mid_by_symbol[symbol] = _MidCacheEntry(sim_time=sim_time, mid=mid)
        if iv > 0:
            entry = _IVCacheEntry(sim_time=sim_time, iv=iv)
            self._iv_by_symbol[symbol] = entry
            self._iv_by_expiry[(underlying, expiration_str)] = entry
            self._iv_by_underlying[underlying] = entry

    def _resolve_iv(
        self, symbol: str, underlying: str, expiration_str: str,
    ) -> float:
        """Walk the three-tier cache; return FALLBACK_SIGMA on full miss."""
        entry = self._iv_by_symbol.get(symbol)
        if entry is not None:
            return entry.iv
        entry = self._iv_by_expiry.get((underlying, expiration_str))
        if entry is not None:
            return entry.iv
        entry = self._iv_by_underlying.get(underlying)
        if entry is not None:
            return entry.iv
        return FALLBACK_SIGMA

    @staticmethod
    def _apply_envelope(
        bs_or_intrinsic: float,
        intrinsic: float,
        last_known_mid: Optional[float],
        position_quantity: float,
        alpha: float,
    ) -> float:
        """Apply the direction-aware envelope, lerped by alpha ∈ [0, 1].

        alpha=0.0: full envelope (most conservative for the position).
        alpha=1.0: no envelope (unbiased BS).
        alpha in between: linear interpolation.

        position_quantity == 0 bypasses the envelope entirely.
        """
        if position_quantity == 0:
            return max(bs_or_intrinsic, 0.0)

        # Long: no envelope cap. A LONG position's worst case is the option
        # decaying to zero, which BS already captures correctly bar-by-bar.
        # Capping LONG MTM at the entry-bar's mid would freeze the equity
        # curve for the whole hold period; use unbiased BS instead.
        if position_quantity > 0:
            return max(bs_or_intrinsic, 0.0)
        else:
            # Short: floor at max(BS, intrinsic, last_known_mid or 0)
            floor_components = [bs_or_intrinsic, intrinsic]
            if last_known_mid is not None:
                floor_components.append(last_known_mid)
            else:
                floor_components.append(0.0)
            conservative = max(floor_components)

        mtm = alpha * bs_or_intrinsic + (1.0 - alpha) * conservative
        return max(mtm, 0.0)

    def mtm_price(
        self,
        symbol: str,
        sim_time: datetime,
        underlying_price: float,
        position_quantity: float,
        occ_parsed: dict,
        alpha: float = 0.0,
    ) -> float:
        """Conservative MTM price for a single option.

        Args:
            symbol: OCC symbol (e.g. "O:SPY240621C00500000")
            sim_time: current sim datetime (UTC)
            underlying_price: most recent underlying close at sim_time
            position_quantity: signed share count (>0 long, <0 short)
            occ_parsed: dict with keys 'underlying' (str),
              'expiration' (ISO 'YYYY-MM-DD' str), 'option_type'
              ('call'/'put' or 'C'/'P'), 'strike' (float)
            alpha: session mtm_realism in [0, 1]. 0 = full envelope,
              1 = unbiased BS.

        Returns:
            Non-negative MTM price per share/contract unit.
        """
        underlying = occ_parsed["underlying"]
        expiration_str = occ_parsed["expiration"]
        option_type = occ_parsed["option_type"]
        K = float(occ_parsed["strike"])

        # Parse expiration → date → days
        expiration_date = date.fromisoformat(expiration_str)
        sim_date = sim_time.date() if hasattr(sim_time, "date") else sim_time
        days_to_expiry = (expiration_date - sim_date).days
        T = max(days_to_expiry, 0) / 365.0

        # Compute intrinsic (always needed for envelope floor)
        is_call = option_type[0].upper() == "C"
        if is_call:
            intrinsic = max(underlying_price - K, 0.0)
        else:
            intrinsic = max(K - underlying_price, 0.0)

        if days_to_expiry <= 0:
            bs_or_intrinsic = intrinsic
        else:
            # Layer 2: Black-Scholes with carry-forward IV
            sigma = self._resolve_iv(symbol, underlying, expiration_str)
            bs_or_intrinsic = black_scholes_price(
                S=underlying_price, K=K, T=T, r=RISK_FREE_RATE,
                sigma=sigma, option_type=option_type,
            )

        last_known_mid_entry = self._mid_by_symbol.get(symbol)
        last_known_mid = (
            last_known_mid_entry.mid if last_known_mid_entry is not None else None
        )

        return self._apply_envelope(
            bs_or_intrinsic=bs_or_intrinsic,
            intrinsic=intrinsic,
            last_known_mid=last_known_mid,
            position_quantity=position_quantity,
            alpha=alpha,
        )
