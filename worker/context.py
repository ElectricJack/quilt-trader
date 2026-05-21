from datetime import datetime
from typing import Any, Optional
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

        try:
            from alpaca.data.requests import StockLatestTradeRequest
            inner = getattr(self._broker, "_inner", self._broker)
            inner._ensure_clients()
            resp = inner._data_client.get_stock_latest_trade(
                StockLatestTradeRequest(symbol_or_symbols=[symbol])
            )
            if symbol in resp:
                self._price_cache[symbol] = float(resp[symbol].price)
                return self._price_df(symbol)
        except Exception:
            logger.warning("Failed to fetch latest price for %s", symbol, exc_info=True)

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
