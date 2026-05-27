"""Base protocol and shared types for the Asset Service Layer.

Every asset type (equities, options, crypto, indexes) implements the
AssetService protocol. Callers route through AssetServiceRegistry which
returns the correct service for a given symbol — no more scattered
if/else on asset_type.

Services do NOT inherit from each other. Shared logic (e.g. bar lookup)
lives as free functions in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Protocol

import numpy as np
import pandas as pd


class AssetType(str, Enum):
    EQUITIES = "equities"
    OPTIONS = "options"
    CRYPTO = "crypto"
    INDEX = "index"


@dataclass(frozen=True)
class Settlement:
    """Result of expiring an option position. None means not expired."""
    symbol: str
    side: str
    quantity: float
    fill_price: float
    realized_pnl: float


@dataclass(frozen=True)
class StreamConfig:
    """Per-broker streaming configuration for an asset class."""
    supported: bool
    stream_class: str
    symbol_transform: str
    cap: int
    cluster: Optional[str] = None


def _bar_lookup(df: pd.DataFrame, sim_time: Any) -> Optional[float]:
    """Return the close price of the last bar at or before ``sim_time``.

    Handles tz-naive and tz-aware timestamps on either side by normalizing
    both to UTC-naive before comparing. Returns None if df is empty or no
    bar exists at/before sim_time.
    """
    if df is None or len(df) == 0:
        return None
    ts = pd.to_datetime(df["timestamp"])
    if ts.dt.tz is not None:
        ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
    cutoff = pd.Timestamp(sim_time)
    if cutoff.tz is not None:
        cutoff = cutoff.tz_convert("UTC").tz_localize(None)
    ns = ts.values.view("int64")
    cutoff_ns = np.datetime64(cutoff).view("int64")
    idx = int(np.searchsorted(ns, cutoff_ns, side="right")) - 1
    if idx < 0:
        return None
    return float(df.iloc[idx]["close"])


class AssetService(Protocol):
    asset_type: AssetType

    def classify(self, symbol: str) -> bool: ...
    def resolve_symbol(self, symbol: str, provider: str) -> str: ...
    def compose_order_symbol(self, leg: Any) -> str: ...

    def get_multiplier(self) -> int: ...
    def get_price(self, symbol: str, sim_time: Any, ctx: Any) -> Optional[float]: ...
    def get_fill_price(
        self, symbol: str, side: str, sim_time: Any, ctx: Any,
    ) -> Optional[float]: ...

    def compute_unrealized_pnl(
        self, symbol: str, quantity: float, avg_price: float, market_value: float,
    ) -> float: ...
    def risk_contribution(
        self, symbol: str, market_value: float,
        data_service: Any = None, lookback_days: int = 60,
    ) -> float: ...

    def handle_expiry(
        self, symbol: str, quantity: float, avg_price: float,
        sim_time: Any, ctx: Any,
    ) -> Optional[Settlement]: ...

    def time_in_force(self) -> str: ...
    def supports_multileg(self) -> bool: ...
    def required_order_fields(self) -> set[str]: ...
    def is_pdt_exempt(self) -> bool: ...

    def is_market_open(self, timestamp: Any) -> bool: ...

    def stream_config(self, broker: str) -> StreamConfig: ...
    def supports_provider(self, provider: str) -> bool: ...

    async def discover_contracts(
        self, underlying: str, start: Any, end: Any,
        config: dict, provider: Any,
    ) -> list[str]: ...
