"""Equity asset service — US stocks and ETFs."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from coordinator.services.asset_services.base import (
    AssetType,
    Settlement,
    StreamConfig,
    _bar_lookup,
)

_OCC_RE = re.compile(r"^(?:O:)?[A-Z]{1,6}\d{6}[CP]\d{8}$")
_CRYPTO_SUFFIXES = ("USD", "USDT")
_KNOWN_INDEXES = {"VIX", "SPX", "NDX", "RUT", "DJI", "GSPC", "IXIC"}


def _is_dst_us_eastern(ts_utc: datetime) -> bool:
    """Rough check: US Eastern observes DST from 2nd Sunday in March to
    1st Sunday in November."""
    y = ts_utc.year
    march_start = datetime(y, 3, 8, tzinfo=timezone.utc)
    while march_start.weekday() != 6:
        march_start += timedelta(days=1)
    nov_end = datetime(y, 11, 1, tzinfo=timezone.utc)
    while nov_end.weekday() != 6:
        nov_end += timedelta(days=1)
    return march_start <= ts_utc < nov_end


def _utc_to_et(ts: datetime) -> datetime:
    """Convert a UTC datetime to ET (naive)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    offset = timedelta(hours=4) if _is_dst_us_eastern(ts) else timedelta(hours=5)
    return (ts - offset).replace(tzinfo=None)


class EquityAssetService:
    asset_type = AssetType.EQUITIES

    def classify(self, symbol: str) -> bool:
        if not symbol:
            return False
        if _OCC_RE.match(symbol):
            return False
        if symbol in _KNOWN_INDEXES:
            return False
        if symbol.startswith("I:") or symbol.startswith("^"):
            return False
        if symbol.endswith(_CRYPTO_SUFFIXES) and symbol not in ("USD", "USDT"):
            return False
        return True

    def resolve_symbol(self, symbol: str, provider: str) -> str:
        return symbol

    def compose_order_symbol(self, leg: Any) -> str:
        return leg.symbol

    def get_multiplier(self) -> int:
        return 1

    def get_price(self, symbol: str, sim_time: Any, ctx: Any) -> Optional[float]:
        if ctx is None or not hasattr(ctx, "_bars"):
            return None
        for (_src, sym, _tf), df in ctx._bars.items():
            if sym == symbol:
                return _bar_lookup(df, sim_time)
        return None

    def get_fill_price(
        self, symbol: str, side: str, sim_time: Any, ctx: Any,
    ) -> Optional[float]:
        return self.get_price(symbol, sim_time, ctx)

    def compute_unrealized_pnl(
        self, symbol: str, quantity: float, avg_price: float, market_value: float,
    ) -> float:
        cost = avg_price * abs(quantity)
        return market_value - cost if market_value > 0 else 0.0

    def risk_contribution(
        self, symbol: str, market_value: float,
        data_service: Any = None, lookback_days: int = 60,
    ) -> float:
        if data_service is None:
            return market_value * 0.02
        import numpy as np
        for provider in ("polygon", "tradier", "yfinance", "alpaca_live", "tradier_live"):
            df = data_service.load_market_data(provider, symbol, "1day")
            if df is None or len(df) < 10:
                continue
            closes = df["close"].astype(float).values[-lookback_days:]
            if len(closes) < 10:
                continue
            returns = np.diff(np.log(closes))
            var_5 = np.percentile(returns, 5)
            return abs(var_5) * market_value
        return market_value * 0.02

    def handle_expiry(
        self, symbol: str, quantity: float, avg_price: float,
        sim_time: Any, ctx: Any,
    ) -> Optional[Settlement]:
        return None

    def time_in_force(self) -> str:
        return "DAY"

    def supports_multileg(self) -> bool:
        return False

    def required_order_fields(self) -> set[str]:
        return set()

    def is_pdt_exempt(self) -> bool:
        return False

    def is_market_open(self, timestamp: Any) -> bool:
        if not isinstance(timestamp, datetime):
            raise TypeError(f"is_market_open requires datetime, got {type(timestamp).__name__}")
        et = _utc_to_et(timestamp)
        if et.weekday() >= 5:
            return False
        minutes = et.hour * 60 + et.minute
        return 9 * 60 + 30 <= minutes < 16 * 60

    def stream_config(self, broker: str) -> StreamConfig:
        if broker == "polygon":
            return StreamConfig(True, "stock", "identity", 30, cluster="stocks")
        if broker == "alpaca":
            return StreamConfig(True, "stock", "identity", 30)
        if broker == "tradier":
            return StreamConfig(True, "stock", "identity", 30)
        if broker == "thetadata":
            return StreamConfig(True, "stock", "identity", 30)
        return StreamConfig(False, "", "identity", 0)

    def supports_provider(self, provider: str) -> bool:
        return provider in ("polygon", "alpaca", "tradier", "thetadata", "yfinance")

    async def discover_contracts(
        self, underlying: str, start: Any, end: Any,
        config: dict, provider: Any,
    ) -> list[str]:
        return [underlying]
