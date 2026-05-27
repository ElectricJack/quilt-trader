"""Index asset service — VIX, SPX, NDX, etc. Read-only (not directly tradeable)."""
from __future__ import annotations

from typing import Any, Optional

from coordinator.services.asset_services.base import (
    AssetType,
    Settlement,
    StreamConfig,
    _bar_lookup,
)
from coordinator.services.asset_services.equity import EquityAssetService

_KNOWN_INDEXES = {"VIX", "SPX", "NDX", "RUT", "DJI", "GSPC", "IXIC"}

_POLYGON_MAP = {
    "SPX": "I:SPX", "NDX": "I:NDX", "RUT": "I:RUT",
    "VIX": "I:VIX", "DJI": "I:DJI",
}

_YFINANCE_MAP = {
    "VIX": "^VIX", "SPX": "^GSPC", "NDX": "^IXIC",
    "RUT": "^RUT", "DJI": "^DJI",
}


class IndexAssetService:
    asset_type = AssetType.INDEX

    def classify(self, symbol: str) -> bool:
        if not symbol:
            return False
        if symbol in _KNOWN_INDEXES:
            return True
        if symbol.startswith("I:") or symbol.startswith("^"):
            return True
        return False

    def resolve_symbol(self, symbol: str, provider: str) -> str:
        if provider == "polygon":
            if symbol.startswith("I:"):
                return symbol
            return _POLYGON_MAP.get(symbol, symbol)
        if provider == "yfinance":
            if symbol.startswith("^"):
                return symbol
            return _YFINANCE_MAP.get(symbol, symbol)
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
        return EquityAssetService().risk_contribution(
            symbol, market_value, data_service=data_service, lookback_days=lookback_days,
        )

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
        return EquityAssetService().is_market_open(timestamp)

    def stream_config(self, broker: str) -> StreamConfig:
        if broker == "polygon":
            return StreamConfig(True, "stock", "identity", 30, cluster="stocks")
        if broker == "yfinance":
            return StreamConfig(True, "stock", "identity", 30)
        return StreamConfig(False, "", "identity", 0)

    def supports_provider(self, provider: str) -> bool:
        return provider in ("polygon", "yfinance")

    async def discover_contracts(
        self, underlying: str, start: Any, end: Any,
        config: dict, provider: Any,
    ) -> list[str]:
        return [underlying]
