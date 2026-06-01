"""Asset service registry — symbol → service dispatch.

Single entry point for all asset-type-specific operations. Callers
never need to check asset_type themselves; they call methods on the
registry and the registry routes to the correct service.

Classification order matters: options first (OCC symbols have letters
that could match equity classifier), then crypto, then index, then
equities (the default fallback).
"""
from __future__ import annotations

from typing import Any

from coordinator.services.asset_services.base import (
    AssetService,
    AssetType,
    StreamConfig,
)
from coordinator.services.asset_services.crypto import CryptoAssetService
from coordinator.services.asset_services.equity import EquityAssetService
from coordinator.services.asset_services.index import IndexAssetService
from coordinator.services.asset_services.options import OptionsAssetService


class AssetServiceRegistry:
    def __init__(self) -> None:
        self._options = OptionsAssetService()
        self._crypto = CryptoAssetService()
        self._index = IndexAssetService()
        self._equity = EquityAssetService()
        self._services = [self._options, self._crypto, self._index, self._equity]

    def classify(self, symbol: str) -> AssetType:
        for svc in self._services:
            if svc.classify(symbol):
                return svc.asset_type
        return AssetType.EQUITIES

    def get_service(self, symbol: str) -> AssetService:
        for svc in self._services:
            if svc.classify(symbol):
                return svc
        return self._equity

    def get_service_by_type(self, asset_type: AssetType | str) -> AssetService:
        t = AssetType(asset_type) if isinstance(asset_type, str) else asset_type
        if t == AssetType.OPTIONS:
            return self._options
        if t == AssetType.CRYPTO:
            return self._crypto
        if t == AssetType.INDEX:
            return self._index
        return self._equity

    def resolve_symbol(self, symbol: str, provider: str) -> str:
        return self.get_service(symbol).resolve_symbol(symbol, provider)

    def get_multiplier(self, symbol: str) -> int:
        return self.get_service(symbol).get_multiplier()

    def time_in_force(self, symbol: str) -> str:
        return self.get_service(symbol).time_in_force()

    def is_market_open(self, symbol: str, timestamp: Any) -> bool:
        return self.get_service(symbol).is_market_open(timestamp)

    def compose_order_symbol(self, leg: Any) -> str:
        # For options legs, leg.symbol carries the *underlying* (e.g. "SPY")
        # plus expiry/strike/right — the registry must route by leg.asset_type
        # rather than by classifying the symbol.
        at = getattr(leg, "asset_type", None)
        if at:
            return self.get_service_by_type(at).compose_order_symbol(leg)
        return self.get_service(leg.symbol).compose_order_symbol(leg)

    def supports_provider(self, symbol: str, provider: str) -> bool:
        return self.get_service(symbol).supports_provider(provider)

    def stream_config(self, symbol: str, broker: str) -> StreamConfig:
        return self.get_service(symbol).stream_config(broker)

    def validate(self, symbol: str) -> None:
        """Raise ValueError if symbol matches no canonical form."""
        for svc in self._services:
            if svc.classify(symbol):
                return
        raise ValueError(
            f"{symbol!r} is not a canonical symbol. "
            f"Crypto canonical form is e.g. 'BTCUSD'. "
            f"Equity canonical form is e.g. 'AAPL'. "
            f"Index canonical form is one of "
            f"{{VIX, SPX, NDX, COMP, DJI, RUT, ...}} (see _KNOWN_INDEXES). "
            f"Options canonical form is OCC e.g. 'AAPL240119C00150000'."
        )

    def canonicalize(self, provider_form: str, provider: str) -> str:
        """Try each AssetService.canonicalize in classification order;
        return the first successful canonical form."""
        for svc in self._services:
            try:
                canonical = svc.canonicalize(provider_form, provider)
            except (ValueError, KeyError):
                continue
            else:
                return canonical
        raise ValueError(
            f"{provider_form!r} (provider={provider!r}) could not be canonicalized "
            f"by any asset service"
        )


_default_registry: AssetServiceRegistry | None = None


def get_default_registry() -> AssetServiceRegistry:
    """Process-wide singleton — most callers should use this rather than
    instantiating their own AssetServiceRegistry."""
    global _default_registry
    if _default_registry is None:
        _default_registry = AssetServiceRegistry()
    return _default_registry
