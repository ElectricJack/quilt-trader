from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional


@dataclass
class TradeFill:
    symbol: str
    side: str
    quantity: float
    filled_price: float
    fees: float
    slippage: float
    timestamp: datetime
    fee_breakdown: Optional[dict] = None
    metadata: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "filled_price": self.filled_price,
            "fees": self.fees,
            "slippage": self.slippage,
            "timestamp": self.timestamp.isoformat(),
            "fee_breakdown": self.fee_breakdown,
            "metadata": self.metadata,
        }

    @staticmethod
    def from_dict(d: dict) -> TradeFill:
        return TradeFill(
            symbol=d["symbol"],
            side=d["side"],
            quantity=d["quantity"],
            filled_price=d["filled_price"],
            fees=d["fees"],
            slippage=d["slippage"],
            timestamp=datetime.fromisoformat(d["timestamp"]),
            fee_breakdown=d.get("fee_breakdown"),
            metadata=d.get("metadata"),
        )


# Canonical asset_type values. Mirrors coordinator AssetType enum — kept
# inline since the SDK can't import the coordinator. Contract test in
# tests/sdk/test_asset_type_contract.py asserts these stay in sync.
_VALID_ASSET_TYPES = frozenset({"equities", "options", "crypto", "index"})


def _validate_asset_type(value: str) -> str:
    if value not in _VALID_ASSET_TYPES:
        raise ValueError(
            f"asset_type must be one of {sorted(_VALID_ASSET_TYPES)}, got {value!r}"
        )
    return value


@dataclass
class Position:
    symbol: str
    quantity: float
    avg_cost: float
    current_price: float
    asset_type: str = "equities"

    def __post_init__(self) -> None:
        _validate_asset_type(self.asset_type)

    @property
    def market_value(self) -> float:
        return self.quantity * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.avg_cost) * self.quantity

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.avg_cost == 0:
            return 0.0
        return (self.current_price - self.avg_cost) / abs(self.avg_cost) * 100

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "avg_cost": self.avg_cost,
            "current_price": self.current_price,
            "asset_type": self.asset_type,
        }

    @staticmethod
    def from_dict(d: dict) -> Position:
        return Position(
            symbol=d["symbol"],
            quantity=d["quantity"],
            avg_cost=d["avg_cost"],
            current_price=d["current_price"],
            asset_type=d.get("asset_type", "equities"),
        )


@dataclass
class OptionContract:
    symbol: str
    underlying: str
    expiration: date
    strike: float
    option_type: str  # "call" or "put"
    bid: float
    ask: float
    last: float
    volume: int
    open_interest: int
    implied_volatility: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass
class OptionChain:
    underlying: str
    expiration: date
    calls: list[OptionContract] = field(default_factory=list)
    puts: list[OptionContract] = field(default_factory=list)

    @property
    def strikes(self) -> list[float]:
        all_strikes = set()
        for c in self.calls:
            all_strikes.add(c.strike)
        for p in self.puts:
            all_strikes.add(p.strike)
        return sorted(all_strikes)

    def get_call(self, strike: float) -> Optional[OptionContract]:
        for c in self.calls:
            if c.strike == strike:
                return c
        return None

    def get_put(self, strike: float) -> Optional[OptionContract]:
        for p in self.puts:
            if p.strike == strike:
                return p
        return None
