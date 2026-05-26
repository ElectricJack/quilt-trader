import asyncio
import logging
import time
from datetime import date, datetime, timezone
from typing import Awaitable, Callable

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

PageCallback = Callable[[int, int, "float | None"], Awaitable[None]]
# (page_index_zero_based, cumulative_bars_so_far, fraction_of_date_range_or_None) -> awaitable

StatusCallback = Callable[[str], Awaitable[None]]
# (message) -> awaitable

BarsCallback = Callable[[list[dict]], Awaitable[None]]
# (bars) -> awaitable

logger = logging.getLogger(__name__)

_TF_MAP: dict[str, TimeFrame] = {
    "1min": TimeFrame.Minute,
    "5min": TimeFrame(5, TimeFrameUnit.Minute),
    "15min": TimeFrame(15, TimeFrameUnit.Minute),
    "1hour": TimeFrame.Hour,
    "1day": TimeFrame.Day,
}


class AlpacaProvider:
    supported_timeframes = ["1min", "5min", "15min", "1hour", "1day"]
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        *,
        min_request_interval_s: float = 0.2,
    ) -> None:
        self._client = StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)
        self._min_interval = min_request_interval_s
        self._last_request_ts: float = 0.0

    async def _safe_status(self, on_status: StatusCallback | None, msg: str) -> None:
        if on_status is None:
            return
        try:
            await on_status(msg)
        except Exception:
            logger.exception("on_status callback raised")

    async def _rate_limit(self, on_status: StatusCallback | None) -> None:
        if self._min_interval <= 0:
            return
        now = time.monotonic()
        wait = self._min_interval - (now - self._last_request_ts)
        if wait > 0:
            await self._safe_status(on_status, f"Pacing ({wait:.1f}s)")
            await asyncio.sleep(wait)
        self._last_request_ts = time.monotonic()

    async def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: date,
        end: date,
        on_page: PageCallback | None = None,
        on_status: StatusCallback | None = None,
        on_bars: BarsCallback | None = None,
    ) -> list[dict]:
        tf = _TF_MAP.get(timeframe)
        if tf is None:
            raise ValueError(
                f"AlpacaProvider unsupported timeframe '{timeframe}'. "
                f"Supported timeframes: {sorted(_TF_MAP.keys())}"
            )

        await self._safe_status(on_status, f"Fetching {symbol} {timeframe} bars from Alpaca")
        await self._rate_limit(on_status)

        req = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=tf,
            start=datetime(start.year, start.month, start.day),
            end=datetime(end.year, end.month, end.day),
        )

        raw = await asyncio.to_thread(self._client.get_stock_bars, req)
        raw_bars = list(raw.get(symbol, []))

        if not raw_bars:
            if on_page is not None:
                await on_page(0, 0, None)
            return []

        bars: list[dict] = [
            {
                "timestamp": (
                    b.timestamp.astimezone(timezone.utc).isoformat()
                    if b.timestamp.tzinfo is not None
                    else b.timestamp.replace(tzinfo=timezone.utc).isoformat()
                ),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in raw_bars
        ]

        if on_bars is not None:
            await on_bars(bars)

        if on_page is not None:
            await on_page(0, len(bars), 1.0)

        return bars
