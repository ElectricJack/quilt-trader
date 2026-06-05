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
