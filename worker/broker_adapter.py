from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, date as _date
from typing import Optional


@dataclass
class OrderResult:
    symbol: str
    side: str
    quantity: float
    order_type: str
    filled_price: float
    fees: float = 0.0
    fee_breakdown: Optional[dict] = None
    broker_order_id: Optional[str] = None


@dataclass
class BrokerTransaction:
    """Normalized broker transaction. Used by the sync flow.

    `type` is one of: fill, dividend, interest, deposit, withdrawal, fee, other.
    For fills, symbol/side/quantity/price are populated. For cash flows, only `amount` is required.
    `amount` is signed: positive = cash in, negative = cash out.
    """
    broker_id: str
    type: str
    timestamp: datetime
    amount: float = 0.0
    symbol: Optional[str] = None
    side: Optional[str] = None
    quantity: Optional[float] = None
    price: Optional[float] = None
    fees: float = 0.0
    description: Optional[str] = None
    raw: dict = field(default_factory=dict)


@dataclass
class MultilegLegSpec:
    """Input shape for one leg of a multi-leg order. Matches Spec A's API LegSpec."""
    symbol: str
    asset_type: str               # "equities" | "options" | "crypto"
    side: str                     # "buy" | "sell"
    quantity: float
    expiry: Optional[str] = None  # YYYY-MM-DD, options only
    strike: Optional[float] = None
    right: Optional[str] = None   # "call" | "put"


@dataclass
class MultilegLegResult:
    index: int
    status: str                              # "filled" | "rejected" | "pending"
    filled_price: Optional[float] = None
    fees: Optional[float] = None
    error: Optional[str] = None
    broker_order_id: Optional[str] = None


@dataclass
class MultilegOrderResult:
    broker_order_id: Optional[str]           # parent order id when atomic
    legs: list[MultilegLegResult]
    atomic: bool                             # True if filled via native multi-leg endpoint


@dataclass
class OptionContract:
    strike: float
    right: str                               # "call" | "put"
    occ_symbol: str
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float]
    iv: Optional[float]
    delta: Optional[float]
    gamma: Optional[float]
    theta: Optional[float]
    vega: Optional[float]
    open_interest: Optional[int]
    volume: Optional[int]


@dataclass
class OptionChainSnapshot:
    underlying: str
    spot: float
    expiry: _date
    contracts: list[OptionContract]
    as_of: Optional[datetime]


class BrokerAdapter(ABC):
    @abstractmethod
    def get_positions(self) -> dict[str, dict]: ...
    @abstractmethod
    def get_account_info(self) -> dict: ...
    @abstractmethod
    def submit_order(self, symbol: str, side: str, quantity: float, order_type: str,
                     limit_price: Optional[float] = None, stop_price: Optional[float] = None) -> OrderResult: ...

    def get_transactions(self, since: datetime) -> list[BrokerTransaction]:
        """Fetch broker activity since `since`. Default: not implemented."""
        return []

    # ---- Multi-leg orders (Spec A) ----
    def supports_multileg_orders(self, legs: list[MultilegLegSpec]) -> bool:
        """Whether this adapter can submit `legs` as a single atomic ticket."""
        return False

    def compose_symbol(self, leg: MultilegLegSpec) -> str:
        """Format a leg into a broker-specific symbol (OCC for options)."""
        return leg.symbol

    def submit_multileg_order(
        self,
        legs: list[MultilegLegSpec],
        order_type: str,
        limit_price: Optional[float],
    ) -> MultilegOrderResult:
        """Submit `legs` as one atomic broker order. Raises if unsupported."""
        raise NotImplementedError

    # ---- Options chain (Spec C) ----
    def list_option_expiries(self, underlying: str) -> list[_date]:
        """Return available option expirations for the underlying."""
        raise NotImplementedError

    def get_option_chain(self, underlying: str, expiry: _date) -> OptionChainSnapshot:
        """Return the full chain for one expiry."""
        raise NotImplementedError


class MockBrokerAdapter(BrokerAdapter):
    def __init__(self) -> None:
        self._positions: dict[str, dict] = {}
        self._account_info: dict = {"cash": 100000.0, "portfolio_value": 100000.0, "buying_power": 200000.0}
        self._fill_price: float = 0.0
        self._fees: float = 0.0
        self.order_history: list[OrderResult] = []

    def set_positions(self, positions: dict[str, dict]) -> None:
        self._positions = positions

    def set_account_info(self, cash: float, portfolio_value: float, buying_power: float) -> None:
        self._account_info = {"cash": cash, "portfolio_value": portfolio_value, "buying_power": buying_power}

    def set_fill_price(self, price: float) -> None:
        self._fill_price = price

    def set_fees(self, fees: float) -> None:
        self._fees = fees

    def get_positions(self) -> dict[str, dict]:
        return dict(self._positions)

    def get_account_info(self) -> dict:
        return dict(self._account_info)

    def submit_order(self, symbol: str, side: str, quantity: float, order_type: str,
                     limit_price: Optional[float] = None, stop_price: Optional[float] = None) -> OrderResult:
        result = OrderResult(symbol=symbol, side=side, quantity=quantity, order_type=order_type,
                           filled_price=self._fill_price, fees=self._fees)
        self.order_history.append(result)
        return result
