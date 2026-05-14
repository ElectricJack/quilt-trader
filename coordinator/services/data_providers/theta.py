import logging
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

TIMEFRAME_MAP = {
    "1min": 60000,
    "5min": 300000,
    "15min": 900000,
    "1hour": 3600000,
    "1day": 0,
}


class ThetaDataProvider:
    BASE_URL = "https://api.thetadata.us"

    def __init__(self, username: str, password: str, http_client: Any = None) -> None:
        self._username = username
        self._password = password
        self._http = http_client
        self._token: str | None = None

    async def _ensure_auth(self) -> None:
        if self._token is not None:
            return
        response = await self._http.post(
            f"{self.BASE_URL}/v2/auth",
            json={"username": self._username, "password": self._password},
        )
        data = response.json()
        self._token = data.get("token", "")

    def _auth_headers(self) -> dict[str, str]:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    async def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: date,
        end: date,
        **_kwargs: Any,
    ) -> list[dict]:
        # Accepts on_page/on_status/on_bars kwargs for signature compatibility with
        # PolygonProvider, but Theta returns a single non-paginated response so they
        # are not invoked.
        await self._ensure_auth()

        if timeframe == "1day":
            return await self._fetch_eod(symbol, start, end)
        return await self._fetch_intraday(symbol, timeframe, start, end)

    async def _fetch_eod(self, symbol: str, start: date, end: date) -> list[dict]:
        url = f"{self.BASE_URL}/v2/hist/stock/eod"
        response = await self._http.get(
            url,
            params={"root": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")},
            headers=self._auth_headers(),
        )
        data = response.json()
        results = data.get("response", [])
        bars = []
        for r in results:
            if isinstance(r, dict) and "ms_of_day" in r:
                bars.append({
                    "timestamp": datetime.combine(
                        date.fromisoformat(str(r.get("date", ""))), datetime.min.time(), tzinfo=timezone.utc
                    ).isoformat() if r.get("date") else None,
                    "open": r.get("open", 0) / 100,
                    "high": r.get("high", 0) / 100,
                    "low": r.get("low", 0) / 100,
                    "close": r.get("close", 0) / 100,
                    "volume": r.get("volume", 0),
                })
        return bars

    async def _fetch_intraday(self, symbol: str, timeframe: str, start: date, end: date) -> list[dict]:
        ivl_ms = TIMEFRAME_MAP.get(timeframe, 60000)
        url = f"{self.BASE_URL}/v2/hist/stock/trade"
        response = await self._http.get(
            url,
            params={
                "root": symbol,
                "start_date": start.strftime("%Y%m%d"),
                "end_date": end.strftime("%Y%m%d"),
                "ivl": ivl_ms,
            },
            headers=self._auth_headers(),
        )
        data = response.json()
        results = data.get("response", [])
        bars = []
        for r in results:
            if isinstance(r, dict) and "ms_of_day" in r:
                bar_date = r.get("date", "")
                ms = r.get("ms_of_day", 0)
                ts = datetime.combine(
                    date.fromisoformat(str(bar_date)), datetime.min.time(), tzinfo=timezone.utc
                )
                ts = ts.replace(
                    hour=ms // 3600000,
                    minute=(ms % 3600000) // 60000,
                    second=(ms % 60000) // 1000,
                )
                bars.append({
                    "timestamp": ts.isoformat(),
                    "open": r.get("open", 0) / 100,
                    "high": r.get("high", 0) / 100,
                    "low": r.get("low", 0) / 100,
                    "close": r.get("close", 0) / 100,
                    "volume": r.get("volume", 0),
                })
        return bars
