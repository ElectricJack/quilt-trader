from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SignalType(Enum):
    BUY = "buy"
    SELL = "sell"
    SELL_SHORT = "sell_short"
    BUY_TO_COVER = "buy_to_cover"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(Enum):
    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"


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
class SignalLeg:
    symbol: str
    signal_type: SignalType
    quantity: float
    asset_type: str = "equities"
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: TimeInForce = TimeInForce.DAY

    def __post_init__(self) -> None:
        _validate_asset_type(self.asset_type)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "signal_type": self.signal_type.value,
            "quantity": self.quantity,
            "asset_type": self.asset_type,
            "order_type": self.order_type.value,
            "limit_price": self.limit_price,
            "stop_price": self.stop_price,
            "time_in_force": self.time_in_force.value,
        }

    @staticmethod
    def from_dict(d: dict) -> SignalLeg:
        return SignalLeg(
            symbol=d["symbol"],
            signal_type=SignalType(d["signal_type"]),
            quantity=d["quantity"],
            asset_type=d.get("asset_type", "equities"),
            order_type=OrderType(d.get("order_type", "market")),
            limit_price=d.get("limit_price"),
            stop_price=d.get("stop_price"),
            time_in_force=TimeInForce(d.get("time_in_force", "DAY")),
        )


@dataclass
class Signal:
    legs: list[SignalLeg]
    strategy_type: str = "single"
    net_debit_limit: Optional[float] = None
    net_credit_limit: Optional[float] = None
    reasoning: Optional[str] = None
    metadata: Optional[dict] = field(default=None)

    @property
    def is_multi_leg(self) -> bool:
        return len(self.legs) > 1

    @staticmethod
    def simple(
        symbol: str,
        signal_type: SignalType,
        quantity: float,
        asset_type: str = "equities",
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
        reasoning: Optional[str] = None,
        time_in_force: TimeInForce = TimeInForce.DAY,
    ) -> Signal:
        return Signal(
            legs=[
                SignalLeg(
                    symbol=symbol,
                    signal_type=signal_type,
                    quantity=quantity,
                    asset_type=asset_type,
                    order_type=order_type,
                    limit_price=limit_price,
                    time_in_force=time_in_force,
                )
            ],
            strategy_type="single",
            reasoning=reasoning,
        )

    def to_dict(self) -> dict:
        return {
            "legs": [leg.to_dict() for leg in self.legs],
            "strategy_type": self.strategy_type,
            "net_debit_limit": self.net_debit_limit,
            "net_credit_limit": self.net_credit_limit,
            "reasoning": self.reasoning,
            "metadata": self.metadata,
        }

    @staticmethod
    def from_dict(d: dict) -> Signal:
        return Signal(
            legs=[SignalLeg.from_dict(leg) for leg in d["legs"]],
            strategy_type=d.get("strategy_type", "single"),
            net_debit_limit=d.get("net_debit_limit"),
            net_credit_limit=d.get("net_credit_limit"),
            reasoning=d.get("reasoning"),
            metadata=d.get("metadata"),
        )
