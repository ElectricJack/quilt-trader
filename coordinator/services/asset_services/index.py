"""Index asset service — VIX, SPX, NDX, etc. Read-only (not directly tradeable)."""
from __future__ import annotations

import re
from typing import Any, Optional

from coordinator.services.asset_services.base import (
    AssetType,
    Settlement,
    StreamConfig,
    _bar_lookup,
)
from coordinator.services.asset_services.equity import EquityAssetService

_KNOWN_INDEXES = frozenset({
    # US equity broad-market (15)
    "SPX", "OEX", "MID", "SML",
    "NDX", "COMP",
    "DJI", "DJT", "DJU",
    "RUT", "RUI", "RUA",
    "NYA", "XAX", "VLG",
    # CBOE VIX family (8)
    "VIX", "VIX1D", "VIX9D", "VIX3M", "VIX6M", "VIX1Y", "VVIX", "SKEW",
    # Vol on other underlyings (5)
    "VXN", "RVX", "VXD", "GVZ", "OVX",
    # CBOE Treasury yields (4)
    "IRX", "FVX", "TNX", "TYX",
    # Sector / specialty (5)
    "SOX", "XAU", "HGX", "OSX", "DXY",
})

# Explicit canonical → yfinance overrides. Indexes not listed default to ^<CANONICAL>.
_YFINANCE_OVERRIDES = {
    "SPX": "^GSPC",
    "COMP": "^IXIC",
}

# Explicit canonical → yfinance reverse lookup for canonicalize()
_YFINANCE_REVERSE = {v: k for k, v in _YFINANCE_OVERRIDES.items()}


class IndexAssetService:
    asset_type = AssetType.INDEX
    CANONICAL_RE = re.compile(r"^[A-Z][A-Z0-9]{1,4}$")  # uppercase letters + digits, 2-5 chars

    def classify(self, symbol: str) -> bool:
        return symbol in _KNOWN_INDEXES

    def resolve_symbol(self, canonical: str, provider: str) -> str:
        if not self.classify(canonical):
            raise ValueError(
                f"{canonical!r} is not a canonical index symbol "
                f"(must be in _KNOWN_INDEXES)"
            )
        if provider == "polygon":
            return f"I:{canonical}"
        if provider == "yfinance":
            return _YFINANCE_OVERRIDES.get(canonical, f"^{canonical}")
        return canonical

    def canonicalize(self, provider_form: str, provider: str) -> str:
        """Parse a provider-native index form back to canonical."""
        if provider == "polygon" and provider_form.startswith("I:"):
            candidate = provider_form[2:]
            if candidate in _KNOWN_INDEXES:
                return candidate
        if provider == "yfinance":
            if provider_form in _YFINANCE_REVERSE:
                return _YFINANCE_REVERSE[provider_form]
            if provider_form.startswith("^"):
                candidate = provider_form[1:]
                if candidate in _KNOWN_INDEXES:
                    return candidate
        if provider_form in _KNOWN_INDEXES:
            return provider_form
        raise ValueError(
            f"{provider_form!r} is not a recognized index form for provider {provider!r}"
        )

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
