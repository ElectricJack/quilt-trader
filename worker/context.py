from datetime import datetime
from typing import Any
import logging
import pandas as pd
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
    ) -> None:
        self._timestamp = timestamp
        self._mode = mode
        self._broker = broker
        self._data_client = data_client
        self._buffer = buffer

    @property
    def timestamp(self) -> datetime:
        return self._timestamp

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def positions(self) -> dict:
        return self._broker.get_positions()

    @property
    def account_value(self) -> float:
        return self._broker.get_account_info()["portfolio_value"]

    @property
    def cash(self) -> float:
        return self._broker.get_account_info()["cash"]

    @property
    def buying_power(self) -> float:
        return self._broker.get_account_info()["buying_power"]

    async def market_data(self, symbol: str, timeframe: str = "1min", bars: int = 100) -> pd.DataFrame:
        if self._buffer is not None and self._buffer.has(symbol, timeframe):
            return self._buffer.get(symbol, timeframe, bars)
        logger.warning(
            "market_data(%s, %s) not in buffer; HTTP fallback. "
            "Declare this in data_dependencies to avoid the slow path.",
            symbol, timeframe,
        )
        return await self._data_client.get_market_data(symbol, timeframe=timeframe, bars=bars)

    async def data(self, source_name: str) -> pd.DataFrame:
        return await self._data_client.get_custom_data(source_name)
