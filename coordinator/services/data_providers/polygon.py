import asyncio
import logging
import time
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

    def __init__(self, api_key: str, http_client: Any = None, *, min_request_interval_s: float = 0.0) -> None:
        self._api_key = api_key
        self._http = http_client
        self._min_interval = min_request_interval_s
        self._last_request_ts: float = 0.0

    def _timeframe_params(self, timeframe: str) -> tuple[str, str]:
        if timeframe in TIMEFRAME_MAP:
            return TIMEFRAME_MAP[timeframe]
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    async def _request_with_retry(self, url: str, params: dict, *, max_retries: int = 5) -> Any:
        """GET with respect for HTTP 429 Retry-After and basic exponential backoff for 5xx."""
        for attempt in range(max_retries):
            if self._min_interval > 0:
                now = time.monotonic()
                wait = self._min_interval - (now - self._last_request_ts)
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last_request_ts = time.monotonic()

            response = await self._http.get(url, params=params)

            if response.status_code == 429:
                # Honor Retry-After header; default to 13 s (free-tier is 5 calls/min ≈ 12 s)
                retry_after_raw = response.headers.get("Retry-After")
                try:
                    retry_after = int(retry_after_raw) if retry_after_raw else 13
                except (TypeError, ValueError):
                    retry_after = 13
                logger.warning(
                    "Polygon 429 rate limit; sleeping %ds before retry %d/%d",
                    retry_after, attempt + 1, max_retries,
                )
                await asyncio.sleep(retry_after)
                continue

            if 500 <= response.status_code < 600:
                backoff = min(60, 2 ** attempt)
                logger.warning(
                    "Polygon %d server error; backing off %ds (retry %d/%d)",
                    response.status_code, backoff, attempt + 1, max_retries,
                )
                await asyncio.sleep(backoff)
                continue

            response.raise_for_status()
            return response

        # Last attempt — raise on whatever the final status was
        raise RuntimeError(f"Polygon request failed after {max_retries} retries: HTTP {response.status_code}")

    async def fetch_bars(self, symbol: str, timeframe: str, start: date, end: date) -> list[dict]:
        multiplier, span = self._timeframe_params(timeframe)
        url = (
            f"{self.BASE_URL}/v2/aggs/ticker/{symbol}/range"
            f"/{multiplier}/{span}/{start.isoformat()}/{end.isoformat()}"
        )
        params = {"apiKey": self._api_key, "limit": 50000, "sort": "asc"}

        all_results = []
        while True:
            response = await self._request_with_retry(url, params)
            data = response.json()
            all_results.extend(data.get("results") or [])
            next_url = data.get("next_url")
            if not next_url:
                break
            url = next_url
            params = {"apiKey": self._api_key}  # next_url has the cursor; only re-add apiKey
            logger.info(
                "Polygon pagination: fetched %d so far, following next_url for %s",
                len(all_results), symbol,
            )

        return [
            {
                "timestamp": datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc).isoformat(),
                "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"], "volume": r["v"],
            }
            for r in all_results
        ]
