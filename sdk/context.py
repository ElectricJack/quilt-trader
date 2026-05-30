from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, date, timedelta
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
    def market_data(self, symbol: str, timeframe: str = "1min", bars: int = 100, source: Optional[str] = None) -> pd.DataFrame:
        ...

    @abstractmethod
    def data(self, source_name: str) -> pd.DataFrame:
        ...

    @abstractmethod
    def option_chain(self, symbol: str, expiration: Optional[date] = None) -> OptionChain:
        ...

    def dataset(
        self,
        name: str,
        *,
        symbol: str | None = None,
        start: date | None = None,
        end: date | None = None,
        lookback_days: int | None = None,
        lag: timedelta = timedelta(0),
        columns: list[str] | None = None,
    ) -> pd.DataFrame:
        """Load a registered bitemporal dataset, filtered to what was knowable as-of
        the runtime clock (minus optional ``lag``).

        There is NO ``as_of`` parameter — the runtime clock is the only source of truth.
        ``lag`` must be >= 0; it can only delay, never peek ahead.
        """
        if lag < timedelta(0):
            raise ValueError("lag must be non-negative")
        effective_as_of = self.timestamp - lag
        if lookback_days is not None:
            if start is not None or end is not None:
                raise ValueError("lookback_days is mutually exclusive with start/end")
            end = effective_as_of.date() if hasattr(effective_as_of, "date") else effective_as_of
            start = end - timedelta(days=lookback_days)
        from coordinator.services.datasets.storage import load_dataset  # lazy — sdk can't import coordinator at module level
        return load_dataset(name, as_of=effective_as_of, symbol=symbol,
                            start=start, end=end, columns=columns)
