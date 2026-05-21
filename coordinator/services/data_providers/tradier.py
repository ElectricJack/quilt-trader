import asyncio
import logging
import time
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable

PageCallback = Callable[[int, int, "float | None"], Awaitable[None]]
# (page_index_zero_based, cumulative_bars_so_far, fraction_of_date_range_or_None) -> awaitable

StatusCallback = Callable[[str], Awaitable[None]]
# (message) -> awaitable

BarsCallback = Callable[[list[dict]], Awaitable[None]]
# (bars) -> awaitable

logger = logging.getLogger(__name__)

LIVE_BASE_URL = "https://api.tradier.com/v1"
SANDBOX_BASE_URL = "https://sandbox.tradier.com/v1"


class TradierProvider:
    def __init__(
        self,
        access_token: str,
        sandbox: bool = False,
        http_client: Any = None,
        *,
        min_request_interval_s: float = 0.2,
    ) -> None:
        self._token = access_token
        self._base_url = SANDBOX_BASE_URL if sandbox else LIVE_BASE_URL
        self._http = http_client
        self._min_interval = min_request_interval_s
        self._last_request_ts: float = 0.0

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

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
        if timeframe != "1day":
            raise ValueError(
                f"TradierProvider only supports '1day' timeframe; got '{timeframe}'. "
                "Tradier's /markets/history endpoint supports daily bars only."
            )

        url = f"{self._base_url}/markets/history"
        params = {
            "symbol": symbol,
            "interval": "daily",
            "start": start.isoformat(),
            "end": end.isoformat(),
        }

        await self._safe_status(on_status, f"Fetching {symbol} daily bars from Tradier")
        await self._rate_limit(on_status)

        response = await self._http.get(url, params=params, headers=self._auth_headers())
        response.raise_for_status()
        data = response.json()

        history = data.get("history")
        if not history:
            bars: list[dict] = []
            if on_page is not None:
                await on_page(0, 0, None)
            return bars

        day_data = history.get("day")
        if not day_data:
            bars = []
            if on_page is not None:
                await on_page(0, 0, None)
            return bars

        # Tradier returns a dict for a single-day result instead of a list
        if isinstance(day_data, dict):
            day_data = [day_data]

        bars = [
            {
                "timestamp": datetime.combine(
                    date.fromisoformat(r["date"]),
                    datetime.min.time(),
                    tzinfo=timezone.utc,
                ).isoformat(),
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["volume"],
            }
            for r in day_data
        ]

        if on_bars is not None and bars:
            await on_bars(bars)

        if on_page is not None:
            await on_page(0, len(bars), 1.0)

        return bars
