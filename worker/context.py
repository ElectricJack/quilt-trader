import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo
import logging
import pandas as pd
from sdk.context import TickContext
from sdk.models import OptionChain, Position
from worker.broker_adapter import BrokerAdapter
from worker.data_client import DataClient
from coordinator.services.backtest_tick_context import (
    _get_calendar_cached,
    _needs_market_calendar,
    _calendar_name_for,
)

logger = logging.getLogger(__name__)


class LiveTickContext(TickContext):
    def __init__(
        self,
        timestamp: datetime,
        mode: str,
        broker: BrokerAdapter,
        data_client: DataClient,
        buffer: Any = None,
        custom_data: Optional[dict[str, pd.DataFrame]] = None,
        *,
        market_timezone: str = "UTC",
        asset_types: Optional[list[str]] = None,
    ) -> None:
        self._timestamp = timestamp
        self._mode = mode
        self._broker = broker
        self._data_client = data_client
        self._buffer = buffer
        self._custom_data = custom_data or {}
        self._price_cache: dict[str, float] = {}
        # Dataset cache: (name, symbol, columns) -> (monotonic_time, DataFrame)
        # Entries are refreshed when the TTL expires (default 60 s).
        self._dataset_cache: dict[tuple, tuple[float, pd.DataFrame]] = {}
        self._dataset_cache_ttl_s: float = 60.0
        self._market_timezone = market_timezone
        self._asset_types = asset_types or []
        self._needs_calendar = _needs_market_calendar(self._asset_types)
        self._calendar_name = _calendar_name_for(self._asset_types)

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

    def market_time(self) -> datetime:
        tz = ZoneInfo(self._market_timezone)
        ts = self._timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(tz)

    def is_market_open(self) -> bool:
        if not self._needs_calendar:
            return True
        cal = _get_calendar_cached(self._calendar_name)
        now_market = self.market_time()
        schedule = cal.schedule(
            start_date=now_market.date(),
            end_date=now_market.date(),
        )
        if schedule.empty:
            return False
        open_ts = schedule.iloc[0]["market_open"].tz_convert(now_market.tzinfo)
        close_ts = schedule.iloc[0]["market_close"].tz_convert(now_market.tzinfo)
        return open_ts <= now_market < close_ts

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

    def option_chain(self, symbol: str, expiration: Optional[date] = None) -> OptionChain:
        """Return the live option chain for *symbol*, delegating to the broker adapter."""
        exp = expiration or (self._timestamp.date() if self._timestamp else date.today())
        if isinstance(exp, str):
            exp = date.fromisoformat(exp)
        try:
            snapshot = self._broker.get_option_chain(symbol, exp)
            calls = [c for c in snapshot.contracts if c.option_type == "call"]
            puts = [c for c in snapshot.contracts if c.option_type == "put"]
            return OptionChain(underlying=symbol, expiration=exp, calls=calls, puts=puts)
        except (NotImplementedError, Exception):
            return OptionChain(underlying=symbol, expiration=exp, calls=[], puts=[])

    def dataset(
        self,
        name: str,
        *,
        symbol: str | None = None,
        start=None,
        end=None,
        lookback_days: int | None = None,
        lag: timedelta = timedelta(0),
        columns: list[str] | None = None,
    ) -> pd.DataFrame:
        """Cached bitemporal dataset lookup for live contexts.

        Parquet bytes are cached per (name, symbol, columns) key with a 60 s
        TTL (configurable via ``_dataset_cache_ttl_s``). On TTL expiry the file
        is re-read from disk so the live process picks up intraday data refreshes.
        The bitemporal filter is always re-applied after the cache fetch.
        """
        if lag < timedelta(0):
            raise ValueError("lag must be non-negative")
        effective_as_of = self.timestamp - lag
        if lookback_days is not None:
            if start is not None or end is not None:
                raise ValueError("lookback_days is mutually exclusive with start/end")
            end = effective_as_of.date() if hasattr(effective_as_of, "date") else effective_as_of
            start = end - timedelta(days=lookback_days)
        cache_key = (name, symbol, tuple(columns) if columns is not None else None)
        now = time.monotonic()
        entry = self._dataset_cache.get(cache_key)
        if entry is None or (now - entry[0]) > self._dataset_cache_ttl_s:
            from coordinator.services.datasets.storage import _get_service
            from coordinator.services.datasets import registry as _reg
            spec = _reg.get(name)
            path = _get_service()._path_for(spec, symbol)
            df = pd.read_parquet(path, columns=columns) if path.exists() else pd.DataFrame()
            entry = (now, df)
            self._dataset_cache[cache_key] = entry
        df = entry[1]
        from coordinator.services.datasets.storage import _filter_bitemporal
        return _filter_bitemporal(df, as_of=effective_as_of, start=start, end=end)

    def data(self, source_name: str) -> pd.DataFrame:
        if source_name in self._custom_data:
            return self._custom_data[source_name]
        logger.warning("Custom data %r not pre-fetched; returning empty DataFrame", source_name)
        return pd.DataFrame()
