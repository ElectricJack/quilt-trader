from datetime import datetime
from typing import Any, Optional
import logging
import pandas as pd
from sdk.models import Position
from worker.broker_adapter import BrokerAdapter
from worker.data_client import DataClient

logger = logging.getLogger(__name__)


class LiveTickContext:
    def __init__(
        self,
        timestamp: datetime,
        mode: str,
        broker: BrokerAdapter,
        data_client: DataClient,
        buffer: Any = None,
        custom_data: Optional[dict[str, pd.DataFrame]] = None,
    ) -> None:
        self._timestamp = timestamp
        self._mode = mode
        self._broker = broker
        self._data_client = data_client
        self._buffer = buffer
        self._custom_data = custom_data or {}
        self._price_cache: dict[str, float] = {}

    @property
    def timestamp(self) -> datetime:
        return self._timestamp

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def positions(self) -> dict:
        raw = self._broker.get_positions()
        return {
            sym: Position(
                symbol=sym,
                quantity=float(p.get("quantity", 0)),
                avg_cost=float(p.get("avg_price", 0)),
                current_price=float(p.get("current_price", 0)),
                asset_type=p.get("asset_class", "equities"),
            ) if isinstance(p, dict) else p
            for sym, p in raw.items()
        }

    @property
    def account_value(self) -> float:
        return self._broker.get_account_info()["portfolio_value"]

    @property
    def cash(self) -> float:
        return self._broker.get_account_info()["cash"]

    @property
    def buying_power(self) -> float:
        return self._broker.get_account_info()["buying_power"]

    def market_data(self, symbol: str, timeframe: str = "1min", bars: int = 100):
        if self._buffer is not None and self._buffer.has(symbol, timeframe):
            return self._buffer.get(symbol, timeframe, bars)

        if symbol in self._price_cache:
            return self._price_df(symbol)

        positions = self._broker.get_positions()
        pos = positions.get(symbol)
        if pos and pos.get("current_price"):
            self._price_cache[symbol] = float(pos["current_price"])
            return self._price_df(symbol)

        inner = getattr(self._broker, "_inner", self._broker)
        prices = inner.get_latest_prices([symbol])
        if symbol in prices:
            self._price_cache[symbol] = prices[symbol]
            return self._price_df(symbol)

        return None

    def _price_df(self, symbol: str) -> pd.DataFrame:
        price = self._price_cache[symbol]
        return pd.DataFrame([{
            "timestamp": self._timestamp,
            "open": price, "high": price, "low": price,
            "close": price, "volume": 0,
        }])

    def data(self, source_name: str) -> pd.DataFrame:
        if source_name in self._custom_data:
            return self._custom_data[source_name]
        logger.warning("Custom data %r not pre-fetched; returning empty DataFrame", source_name)
        return pd.DataFrame()
