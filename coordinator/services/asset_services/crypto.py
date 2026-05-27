"""Crypto asset service — BTC, ETH, etc. 24/7 markets, no expiry, GTC orders."""
from __future__ import annotations

from typing import Any, Optional

from coordinator.services.asset_services.base import (
    AssetType,
    Settlement,
    StreamConfig,
    _bar_lookup,
)

_KNOWN_CRYPTO = {
    "BTCUSD", "ETHUSD", "SOLUSD", "DOGEUSD", "AVAXUSD", "LINKUSD",
    "BTCUSDT", "ETHUSDT", "SOLUSDT",
}
_YFINANCE_MAP = {
    "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD", "SOLUSD": "SOL-USD",
    "DOGEUSD": "DOGE-USD", "AVAXUSD": "AVAX-USD", "LINKUSD": "LINK-USD",
}


def _to_canonical(symbol: str) -> str:
    """Normalize 'BTC/USD', 'BTC-USD', 'BTCUSD' all → 'BTCUSD'."""
    return symbol.replace("/", "").replace("-", "")


def _to_slash(symbol: str) -> str:
    if "/" in symbol:
        return symbol
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}/USDT"
    return f"{symbol[:-3]}/{symbol[-3:]}"


def _to_dash(symbol: str) -> str:
    if "-" in symbol:
        return symbol
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}-USDT"
    return f"{symbol[:-3]}-{symbol[-3:]}"


class CryptoAssetService:
    asset_type = AssetType.CRYPTO

    def classify(self, symbol: str) -> bool:
        if not symbol:
            return False
        normalized = symbol.replace("/", "").replace("-", "")
        if normalized in _KNOWN_CRYPTO:
            return True
        return normalized.endswith("USD") or normalized.endswith("USDT")

    def resolve_symbol(self, symbol: str, provider: str) -> str:
        canon = _to_canonical(symbol)
        if provider == "yfinance":
            return _YFINANCE_MAP.get(canon, _to_dash(canon))
        if provider == "alpaca_stream":
            return _to_slash(canon)
        if provider == "alpaca":
            return _to_slash(canon)  # Alpaca spot crypto uses slash form
        if provider == "coinbase":
            return _to_dash(canon)
        return symbol  # unknown provider — pass through

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
            return market_value * 0.05
        import numpy as np
        for provider in ("polygon", "yfinance", "coinbase"):
            df = data_service.load_market_data(provider, symbol, "1day")
            if df is None or len(df) < 10:
                continue
            closes = df["close"].astype(float).values[-lookback_days:]
            if len(closes) < 10:
                continue
            returns = np.diff(np.log(closes))
            var_5 = np.percentile(returns, 5)
            return abs(var_5) * market_value
        return market_value * 0.05

    def handle_expiry(
        self, symbol: str, quantity: float, avg_price: float,
        sim_time: Any, ctx: Any,
    ) -> Optional[Settlement]:
        return None

    def time_in_force(self) -> str:
        return "GTC"

    def supports_multileg(self) -> bool:
        return False

    def required_order_fields(self) -> set[str]:
        return set()

    def is_pdt_exempt(self) -> bool:
        return True

    def is_market_open(self, timestamp: Any) -> bool:
        return True

    def stream_config(self, broker: str) -> StreamConfig:
        if broker == "alpaca":
            return StreamConfig(True, "crypto", "crypto_slash", 30)
        if broker == "coinbase":
            return StreamConfig(True, "crypto", "crypto_dash", 30)
        if broker == "polygon":
            return StreamConfig(True, "crypto", "polygon_x_prefix", 30, cluster="crypto")
        return StreamConfig(False, "", "identity", 0)

    def supports_provider(self, provider: str) -> bool:
        return provider in ("alpaca", "coinbase", "polygon", "yfinance")

    async def discover_contracts(
        self, underlying: str, start: Any, end: Any,
        config: dict, provider: Any,
    ) -> list[str]:
        return [underlying]
