import asyncio
import logging
import time
from datetime import date, datetime, time as dtime, timezone
from typing import Any, Awaitable, Callable

PageCallback = Callable[[int, int, "float | None"], Awaitable[None]]
# (page_index_zero_based, cumulative_bars_so_far, fraction_of_date_range_or_None) -> awaitable

StatusCallback = Callable[[str], Awaitable[None]]
# (message) -> awaitable

BarsCallback = Callable[[list[dict]], Awaitable[None]]
# (bars_for_this_page) -> awaitable — fired before fetching the next page so partial
# data is persisted even if the run is cancelled or the next request fails.

logger = logging.getLogger(__name__)

TIMEFRAME_MAP = {
    "1min": ("1", "minute"),
    "5min": ("5", "minute"),
    "15min": ("15", "minute"),
    "1hour": ("1", "hour"),
    "1day": ("1", "day"),
}

# Maps common index symbols to the ticker format Polygon expects.
INDEX_SYMBOL_MAP: dict[str, str] = {
    "SPX": "I:SPX",
    "NDX": "I:NDX",
    "RUT": "I:RUT",
    "VIX": "I:VIX",
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

    async def _safe_status(self, on_status: StatusCallback | None, msg: str) -> None:
        if on_status is None:
            return
        try:
            await on_status(msg)
        except Exception:
            logger.exception("on_status callback raised")

    async def _sleep_with_status(self, total_s: float, reason: str, on_status: StatusCallback | None) -> None:
        """Sleep total_s seconds, emitting a countdown status each second."""
        remaining = total_s
        while remaining > 0:
            await self._safe_status(on_status, f"{reason} ({int(remaining)}s left)")
            step = min(1.0, remaining)
            await asyncio.sleep(step)
            remaining -= step

    async def _get_with_heartbeat(self, url: str, params: dict, on_status: StatusCallback | None) -> Any:
        if on_status is None:
            return await self._http.get(url, params=params)

        start = time.monotonic()
        cancel = asyncio.Event()

        async def heartbeat():
            # Wait 1.0s before first tick so we don't spam for fast requests
            try:
                await asyncio.wait_for(cancel.wait(), timeout=1.0)
                return
            except asyncio.TimeoutError:
                pass
            while not cancel.is_set():
                elapsed = int(time.monotonic() - start)
                await self._safe_status(on_status, f"Fetching… ({elapsed}s)")
                try:
                    await asyncio.wait_for(cancel.wait(), timeout=1.0)
                    return
                except asyncio.TimeoutError:
                    continue

        hb_task = asyncio.create_task(heartbeat())
        try:
            return await self._http.get(url, params=params)
        finally:
            cancel.set()
            try:
                await hb_task
            except Exception:
                pass

    async def _request_with_retry(
        self,
        url: str,
        params: dict,
        *,
        max_retries: int = 5,
        on_status: StatusCallback | None = None,
    ) -> Any:
        """GET with respect for HTTP 429 Retry-After and basic exponential backoff for 5xx."""
        for attempt in range(max_retries):
            if self._min_interval > 0:
                now = time.monotonic()
                wait = self._min_interval - (now - self._last_request_ts)
                if wait > 0:
                    await self._sleep_with_status(wait, "Pacing", on_status)
                self._last_request_ts = time.monotonic()

            response = await self._get_with_heartbeat(url, params, on_status)

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
                await self._sleep_with_status(retry_after, "Rate limited; retrying", on_status)
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
        multiplier, span = self._timeframe_params(timeframe)
        url = (
            f"{self.BASE_URL}/v2/aggs/ticker/{symbol}/range"
            f"/{multiplier}/{span}/{start.isoformat()}/{end.isoformat()}"
        )
        params = {"apiKey": self._api_key, "limit": 50000, "sort": "asc"}

        all_bars: list[dict] = []
        last_raw_t: int | None = None
        page_index = 0
        while True:
            await self._safe_status(on_status, f"Starting page {page_index + 1}")
            try:
                response = await self._request_with_retry(url, params, on_status=on_status)
            except Exception as exc:
                # Page 0 (initial request) failures must propagate — we have no data.
                # Later-page failures (next_url paginating past the requested range, or
                # into a tier-restricted current/future window) are tolerated: we keep
                # the bars we already persisted and break out cleanly. Polygon's
                # next_url sometimes points at single-day current ranges that the free
                # tier 403s.
                if page_index == 0:
                    raise
                logger.warning(
                    "Polygon pagination stopped at page %d for %s due to %s: %s; "
                    "%d bars already persisted",
                    page_index, symbol, type(exc).__name__, exc, len(all_bars),
                )
                await self._safe_status(
                    on_status,
                    f"Stopped paginating at page {page_index + 1} ({type(exc).__name__}); "
                    f"keeping {len(all_bars)} bars",
                )
                break
            data = response.json()
            results = data.get("results") or []
            page_bars = [
                {
                    "timestamp": datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc).isoformat(),
                    "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"], "volume": r["v"],
                }
                for r in results
            ]
            all_bars.extend(page_bars)
            if results:
                last_raw_t = results[-1].get("t")
            fraction: float | None = None
            if last_raw_t is not None:
                last_ts = datetime.fromtimestamp(last_raw_t / 1000, tz=timezone.utc)
                start_dt = datetime.combine(start, dtime(0, 0), tzinfo=timezone.utc)
                end_dt = datetime.combine(end, dtime(23, 59, 59), tzinfo=timezone.utc)
                total = (end_dt - start_dt).total_seconds()
                elapsed = (last_ts - start_dt).total_seconds()
                if total > 0:
                    fraction = max(0.0, min(1.0, elapsed / total))
            # Persist this page's bars BEFORE following next_url. Guarantees the data is
            # on disk if the loop is cancelled or the next request fails.
            if on_bars is not None and page_bars:
                await on_bars(page_bars)
            if on_page is not None:
                await on_page(page_index, len(all_bars), fraction)
            next_url = data.get("next_url")
            if not next_url:
                break
            url = next_url
            params = {"apiKey": self._api_key}
            page_index += 1
            logger.info("Polygon pagination: %d bars fetched, following next_url for %s", len(all_bars), symbol)

        return all_bars

    async def fetch_option_chain(
        self,
        underlying: str,
        expiration: date,
        on_status: StatusCallback | None = None,
        strike_range_pct: float = 0.05,
        max_contracts: int = 0,
    ) -> "pd.DataFrame":
        """Fetch options chain for a given underlying + expiration.

        Uses two endpoints that work on all Polygon plans:
        1. /v2/aggs/ticker/{underlying} — get current price for ATM filtering
        2. /v3/reference/options/contracts — discover contracts near ATM
        3. /v2/aggs/ticker/{OCC}/range/1/day — get latest daily bar per contract

        ``strike_range_pct`` controls how far from ATM to include (0.05 = ±5%).
        ``max_contracts`` caps how many contracts to fetch bars for (0 = no limit).
        Contracts are selected nearest-to-ATM first.
        """
        import pandas as _pd
        from datetime import timedelta

        empty = _pd.DataFrame(columns=[
            "symbol", "strike", "option_type", "bid", "ask",
            "last", "volume", "open_interest", "implied_volatility",
        ])

        # Step 0: Get underlying price for ATM strike filtering
        await self._safe_status(on_status, f"Fetching {underlying} price for ATM filtering")
        underlying_price = None
        try:
            price_url = (
                f"{self.BASE_URL}/v2/aggs/ticker/{underlying}/range"
                f"/1/day/{(expiration - timedelta(days=7)).isoformat()}/{expiration.isoformat()}"
            )
            price_resp = await self._request_with_retry(
                price_url, {"apiKey": self._api_key, "limit": 5, "sort": "desc"},
                on_status=on_status,
            )
            price_bars = price_resp.json().get("results") or []
            if price_bars:
                underlying_price = price_bars[0].get("c")
        except Exception:
            logger.warning("Could not fetch underlying price for %s", underlying)

        # Step 1: Discover contracts via reference endpoint
        await self._safe_status(on_status, f"Discovering {underlying} contracts for {expiration}")
        contracts: list[dict] = []
        url = f"{self.BASE_URL}/v3/reference/options/contracts"
        params: dict[str, Any] = {
            "apiKey": self._api_key,
            "underlying_ticker": underlying,
            "expiration_date": expiration.isoformat(),
            "as_of": expiration.isoformat(),
            "limit": 1000,
            "sort": "strike_price",
            "order": "asc",
        }
        if underlying_price is not None:
            params["strike_price.gte"] = round(underlying_price * (1 - strike_range_pct))
            params["strike_price.lte"] = round(underlying_price * (1 + strike_range_pct))
        while True:
            resp = await self._request_with_retry(url, params, on_status=on_status)
            data = resp.json()
            for c in data.get("results") or []:
                contracts.append(c)
            next_url = data.get("next_url")
            if not next_url:
                break
            url = next_url
            params = {"apiKey": self._api_key}

        if not contracts:
            return empty

        if max_contracts > 0 and underlying_price is not None and len(contracts) > max_contracts:
            contracts.sort(key=lambda c: abs(c.get("strike_price", 0) - underlying_price))
            contracts = contracts[:max_contracts]

        await self._safe_status(
            on_status, f"Found {len(contracts)} contracts, fetching daily bars..."
        )

        # Step 2: Fetch the last daily bar for each contract
        # Use a date window ending at expiration to get the most recent prices
        bar_end = expiration
        bar_start = expiration - timedelta(days=7)

        rows: list[dict] = []
        for i, c in enumerate(contracts):
            ticker = c["ticker"]
            strike = c.get("strike_price", 0.0)
            contract_type = c.get("contract_type", "")

            await self._safe_status(
                on_status,
                f"Fetching bars: {i+1}/{len(contracts)} contracts ({underlying} {expiration})",
            )

            bar_url = (
                f"{self.BASE_URL}/v2/aggs/ticker/{ticker}/range"
                f"/1/day/{bar_start.isoformat()}/{bar_end.isoformat()}"
            )
            try:
                bar_resp = await self._request_with_retry(
                    bar_url, {"apiKey": self._api_key, "limit": 10, "sort": "desc"},
                    on_status=on_status,
                )
                bar_data = bar_resp.json()
                results = bar_data.get("results") or []
                if results:
                    last_bar = results[0]
                    close = last_bar.get("c", 0.0)
                    volume = last_bar.get("v", 0)
                    high = last_bar.get("h", 0.0)
                    low = last_bar.get("l", 0.0)
                    spread = max((high - low) * 0.1, close * 0.02) if close > 0 else 0.1
                    rows.append({
                        "symbol": ticker,
                        "strike": strike,
                        "option_type": contract_type,
                        "bid": max(0, close - spread / 2),
                        "ask": close + spread / 2,
                        "last": close,
                        "volume": volume,
                        "open_interest": 0,
                        "implied_volatility": 0.0,
                    })
            except Exception:
                logger.debug("Failed to fetch bar for %s, skipping", ticker)
                continue

        if not rows:
            return empty

        return _pd.DataFrame(rows)
