from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, date
from typing import Optional

import pandas as pd

from sdk.models import Position, OptionChain


class TickContext(ABC):
    """Abstract base providing all data an algorithm needs during a tick.

    Concrete implementations live in the worker (for live trading)
    and in the SDK CLI (for backtesting).
    """

    @property
    @abstractmethod
    def timestamp(self) -> datetime:
        ...

    @property
    @abstractmethod
    def mode(self) -> str:
        ...

    @property
    @abstractmethod
    def positions(self) -> dict[str, Position]:
        ...

    @property
    @abstractmethod
    def account_value(self) -> float:
        ...

    @property
    @abstractmethod
    def cash(self) -> float:
        ...

    @property
    @abstractmethod
    def buying_power(self) -> float:
        ...

    @abstractmethod
    def market_data(self, symbol: str, timeframe: str = "1min", bars: int = 100) -> pd.DataFrame:
        ...

    @abstractmethod
    def data(self, source_name: str) -> pd.DataFrame:
        ...

    @abstractmethod
    def option_chain(self, symbol: str, expiration: Optional[date] = None) -> OptionChain:
        ...
