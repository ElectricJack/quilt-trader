import logging
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

TIMEFRAME_MAP = {
    "1min": ("1", "minute"),
    "5min": ("5", "minute"),
    "15min": ("15", "minute"),
    "1hour": ("1", "hour"),
    "1day": ("1", "day"),
}


class PolygonProvider:
    BASE_URL = "https://api.polygon.io"

    def __init__(self, api_key: str, http_client: Any = None) -> None:
        self._api_key = api_key
        self._http = http_client

    def _timeframe_params(self, timeframe: str) -> tuple[str, str]:
        if timeframe in TIMEFRAME_MAP:
            return TIMEFRAME_MAP[timeframe]
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    async def fetch_bars(self, symbol: str, timeframe: str, start: date, end: date) -> list[dict]:
        multiplier, span = self._timeframe_params(timeframe)
        url = (
            f"{self.BASE_URL}/v2/aggs/ticker/{symbol}/range"
            f"/{multiplier}/{span}/{start.isoformat()}/{end.isoformat()}"
        )
        response = await self._http.get(
            url, params={"apiKey": self._api_key, "limit": 50000, "sort": "asc"},
        )
        data = response.json()
        results = data.get("results", [])
        return [
            {
                "timestamp": datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc).isoformat(),
                "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"], "volume": r["v"],
            }
            for r in results
        ]
