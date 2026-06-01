"""Options asset service — US equity options (OCC format).

Owns: OCC parsing, symbol composition from order legs, pricing, fill
estimation (bid/ask or spread model), delta-adjusted risk, expiry
settlement, contract discovery.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional

import pandas as pd

from coordinator.services.asset_services.base import (
    AssetType,
    Settlement,
    StreamConfig,
    _bar_lookup,
)
from coordinator.services.asset_services.equity import EquityAssetService
from coordinator.services.chain_builder import parse_occ_symbol


class OptionsAssetService:
    asset_type = AssetType.OPTIONS
    CANONICAL_RE = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")

    def classify(self, symbol: str) -> bool:
        return bool(symbol and self.CANONICAL_RE.match(symbol))

    def parse_symbol(self, symbol: str) -> dict | None:
        return parse_occ_symbol(symbol)

    def resolve_symbol(self, canonical: str, provider: str) -> str:
        if not self.CANONICAL_RE.match(canonical):
            raise ValueError(
                f"{canonical!r} is not a canonical option symbol "
                f"(expected OCC format e.g. 'AAPL240119C00150000')"
            )
        if provider == "polygon":
            return f"O:{canonical}"
        return canonical

    def canonicalize(self, provider_form: str, provider: str) -> str:
        """Strip provider prefix from an option symbol to recover canonical OCC."""
        candidate = provider_form
        if provider == "polygon" and candidate.startswith("O:"):
            candidate = candidate[2:]
        if self.CANONICAL_RE.match(candidate):
            return candidate
        raise ValueError(
            f"{provider_form!r} is not a recognized option form for provider {provider!r}"
        )

    def compose_order_symbol(self, leg: Any) -> str:
        if not (leg.expiry and leg.strike is not None and leg.right):
            raise ValueError(
                f"options leg {leg.symbol} requires expiry/strike/right",
            )
        expiry_str = leg.expiry if isinstance(leg.expiry, str) else leg.expiry.isoformat()
        y, m, d = expiry_str.split("-")
        right_ch = "C" if str(leg.right).lower().startswith("c") else "P"
        strike_int = int(round(float(leg.strike) * 1000))
        return f"{leg.symbol}{y[2:]}{m}{d}{right_ch}{strike_int:08d}"

    def get_multiplier(self) -> int:
        return 100

    def get_price(self, symbol: str, sim_time: Any, ctx: Any) -> Optional[float]:
        if ctx is None:
            return None
        ds = getattr(ctx, "_data_service", None)
        if ds is None:
            return None
        raw = symbol.removeprefix("O:")
        source = getattr(ctx, "_default_source", None) or "polygon"
        df = ds.load_market_data(source, raw, "1day")
        return _bar_lookup(df, sim_time) if df is not None else None

    def get_fill_price(
        self, symbol: str, side: str, sim_time: Any, ctx: Any,
    ) -> Optional[float]:
        if ctx is None:
            return None
        ds = getattr(ctx, "_data_service", None)
        if ds is None:
            return None
        raw = symbol.removeprefix("O:")
        source = getattr(ctx, "_default_source", None) or "polygon"
        df = ds.load_market_data(source, raw, "1day")
        if df is None or df.empty:
            return self._lookup_chain_fill(symbol, side, ctx)
        ts = pd.to_datetime(df["timestamp"])
        if ts.dt.tz is not None:
            ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
        cutoff = pd.Timestamp(sim_time)
        if cutoff.tz is not None:
            cutoff = cutoff.tz_convert("UTC").tz_localize(None)
        import numpy as np
        ns = ts.values.view("int64")
        cutoff_ns = np.datetime64(cutoff).view("int64")
        idx = int(np.searchsorted(ns, cutoff_ns, side="right")) - 1
        if idx < 0:
            return None
        bar = df.iloc[idx]
        close = float(bar["close"])
        if "bid" in df.columns and "ask" in df.columns and pd.notna(bar["bid"]):
            return float(bar["ask"]) if side == "buy" else float(bar["bid"])
        from coordinator.services.options_math import estimate_spread
        vol = int(bar.get("volume", 0))
        spread = estimate_spread(close, vol)
        return (close + spread / 2) if side == "buy" else max(0.0, close - spread / 2)

    def _lookup_chain_fill(
        self, symbol: str, side: str, ctx: Any,
    ) -> Optional[float]:
        cache = getattr(ctx, "_option_chain_cache", {}) or {}
        raw = symbol.removeprefix("O:")
        for chain_df in cache.values():
            if chain_df is None or chain_df.empty:
                continue
            for col in ("ticker", "symbol"):
                if col in chain_df.columns:
                    match = chain_df[chain_df[col] == raw]
                    if not match.empty:
                        row = match.iloc[0]
                        return float(row.get("ask", 0)) if side == "buy" else float(row.get("bid", 0))
        return None

    def compute_unrealized_pnl(
        self, symbol: str, quantity: float, avg_price: float, market_value: float,
    ) -> float:
        cost = avg_price * abs(quantity) * self.get_multiplier()
        if market_value > 0 and cost > 0:
            return market_value - cost
        return 0.0

    def risk_contribution(
        self, symbol: str, market_value: float,
        data_service: Any = None, lookback_days: int = 60,
    ) -> float:
        parsed = self.parse_symbol(symbol)
        if not parsed:
            return market_value * 0.05
        underlying = parsed["underlying"]
        equity_risk = EquityAssetService().risk_contribution(
            underlying, market_value, data_service=data_service, lookback_days=lookback_days,
        )
        try:
            from coordinator.services.options_math import bs_greeks
            exp = date.fromisoformat(parsed["expiration"])
            T = max((exp - date.today()).days, 1) / 365.0
            greeks = bs_greeks(
                S=100, K=100, T=T, r=0.04, sigma=0.25,
                option_type=parsed["option_type"],
            )
            delta = abs(greeks["delta"])
        except Exception:
            delta = 0.5
        return equity_risk * delta

    def handle_expiry(
        self, symbol: str, quantity: float, avg_price: float,
        sim_time: Any, ctx: Any,
    ) -> Optional[Settlement]:
        parsed = self.parse_symbol(symbol)
        if not parsed:
            return None
        exp = date.fromisoformat(parsed["expiration"])
        sim_date = sim_time.date() if hasattr(sim_time, "date") else sim_time
        if sim_date <= exp:
            return None

        underlying_price = self._get_underlying_price(parsed["underlying"], sim_time, ctx)
        if underlying_price is None:
            underlying_price = parsed["strike"]

        if parsed["option_type"] == "call":
            intrinsic = max(0.0, underlying_price - parsed["strike"])
        else:
            intrinsic = max(0.0, parsed["strike"] - underlying_price)

        multiplier = self.get_multiplier()
        qty = abs(quantity)
        is_short = quantity < 0

        if intrinsic > 0:
            if is_short:
                realized = (avg_price - intrinsic) * qty * multiplier
                side = "buy"
            else:
                realized = (intrinsic - avg_price) * qty * multiplier
                side = "sell"
        else:
            if is_short:
                realized = avg_price * qty * multiplier
                side = "buy"
            else:
                realized = -(avg_price * qty * multiplier)
                side = "sell"

        return Settlement(
            symbol=symbol, side=side, quantity=qty,
            fill_price=intrinsic, realized_pnl=realized,
        )

    def _get_underlying_price(
        self, underlying: str, sim_time: Any, ctx: Any,
    ) -> Optional[float]:
        if ctx is None or not hasattr(ctx, "_bars"):
            return None
        for (_src, sym, _tf), df in ctx._bars.items():
            if sym == underlying:
                return _bar_lookup(df, sim_time)
        return None

    def time_in_force(self) -> str:
        return "DAY"

    def supports_multileg(self) -> bool:
        return True

    def required_order_fields(self) -> set[str]:
        return {"expiry", "strike", "right"}

    def is_pdt_exempt(self) -> bool:
        return False

    def is_market_open(self, timestamp: Any) -> bool:
        return EquityAssetService().is_market_open(timestamp)

    def stream_config(self, broker: str) -> StreamConfig:
        if broker == "polygon":
            return StreamConfig(True, "options", "occ_prefix", 30, cluster="options")
        if broker == "alpaca":
            return StreamConfig(True, "options", "occ_prefix", 30)
        if broker == "tradier":
            return StreamConfig(True, "options", "identity", 30)
        if broker == "thetadata":
            return StreamConfig(True, "options", "identity", 30)
        return StreamConfig(False, "", "identity", 0)

    def supports_provider(self, provider: str) -> bool:
        return provider in ("polygon", "alpaca", "tradier", "thetadata")

    async def discover_contracts(
        self, underlying: str, start: Any, end: Any,
        config: dict, provider: Any,
    ) -> list[str]:
        if provider is None or not hasattr(provider, "discover_option_contracts"):
            return []
        strike_range = config.get("strike_range", "atm5")
        strike_pct = {"atm5": 0.05, "atm15": 0.15, "all": 1.0}.get(strike_range, 0.05)
        max_contracts = config.get("max_contracts_per_exp", 60)
        underlying_price = config.get("underlying_price")
        contracts = await provider.discover_option_contracts(
            underlying, end, strike_range_pct=strike_pct,
            max_contracts=max_contracts, underlying_price=underlying_price,
        )
        return [c["ticker"].removeprefix("O:") for c in contracts]
