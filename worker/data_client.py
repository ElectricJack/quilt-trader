import time
from typing import Any, Optional
import httpx
import pandas as pd


class DataClient:
    def __init__(self, base_url: str, cache_ttl: int = 60, http_client: Any = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._cache_ttl = cache_ttl
        self._http = http_client
        self._own_http: Optional[httpx.AsyncClient] = None
        self._cache: dict[str, tuple[float, pd.DataFrame]] = {}

    async def _ensure_http(self) -> Any:
        if self._http is not None:
            return self._http
        if self._own_http is None:
            self._own_http = httpx.AsyncClient(timeout=30.0)
        return self._own_http

    def _get_cached(self, key: str) -> Optional[pd.DataFrame]:
        if key in self._cache:
            ts, df = self._cache[key]
            if time.monotonic() - ts < self._cache_ttl:
                return df
            del self._cache[key]
        return None

    def _set_cached(self, key: str, df: pd.DataFrame) -> None:
        self._cache[key] = (time.monotonic(), df)

    def clear_cache(self) -> None:
        self._cache.clear()

    async def get_market_data(self, symbol: str, timeframe: str = "1min", bars: int = 100, source: Optional[str] = None) -> pd.DataFrame:
        url = f"{self._base_url}/api/data/market/{symbol}"
        cache_key = f"market:{symbol}:{timeframe}:{bars}:{source or '_default'}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        params: dict = {"timeframe": timeframe, "bars": bars}
        if source is not None:
            params["source"] = source
        http = await self._ensure_http()
        response = await http.get(url, params=params)
        response.raise_for_status()
        data = response.json().get("data", [])
        df = pd.DataFrame(data)
        self._set_cached(cache_key, df)
        return df

    async def get_custom_data(self, source_name: str) -> pd.DataFrame:
        url = f"{self._base_url}/api/data/custom/{source_name}"
        cache_key = f"custom:{source_name}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        http = await self._ensure_http()
        response = await http.get(url)
        response.raise_for_status()
        data = response.json().get("data", [])
        df = pd.DataFrame(data)
        self._set_cached(cache_key, df)
        return df
