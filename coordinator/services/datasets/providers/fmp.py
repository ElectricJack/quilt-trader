from __future__ import annotations
import asyncio
import time
from datetime import date, timedelta
from typing import Any
from coordinator.services.datasets.adapter import (
    DatasetAdapter, AdapterAuthError, PageCallback, StatusCallback, RowsCallback,
)
from coordinator.services.datasets.quota import QuotaTracker, QuotaExhausted
from coordinator.services.datasets.registry import DatasetSpec, Pagination


class FMPAdapter(DatasetAdapter):
    provider = "fmp"
    BASE_URL = "https://financialmodelingprep.com"

    def __init__(
        self,
        api_key: str,
        http_client: Any,
        quota_tracker: QuotaTracker,
        daily_limit: int = 250,
        min_request_interval_s: float = 0.0,
    ):
        self._api_key = api_key
        self._http = http_client
        self._quota = quota_tracker
        self._daily_limit = daily_limit
        self._min_interval = min_request_interval_s
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def fetch_dataset(self, spec, params, *, on_page=None, on_status=None, on_rows=None):
        if spec.pagination == Pagination.PAGE:
            return await self._fetch_paged(spec, params, on_page, on_status, on_rows)
        if spec.pagination == Pagination.SINGLE:
            return await self._fetch_single(spec, params, on_status, on_rows)
        if spec.pagination == Pagination.DATE_RANGE:
            return await self._fetch_date_range(spec, params, on_page, on_status, on_rows)
        raise NotImplementedError(f"pagination={spec.pagination}")

    async def _fetch_paged(self, spec, params, on_page, on_status, on_rows):
        all_rows: list[dict] = []
        page = int(params.pop("_start_page", 0))
        while True:
            page_rows = await self._request(
                spec.endpoint_path,
                {**params, "page": page, "limit": spec.page_size},
            )
            if not page_rows:
                break
            if on_rows is not None:
                await on_rows(page_rows, page)
            all_rows.extend(page_rows)
            if on_page is not None:
                await on_page(page, len(all_rows))
            page += 1
        return all_rows

    async def _fetch_single(self, spec, params, on_status, on_rows):
        payload = await self._request(spec.endpoint_path, params)
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("historical") or payload.get("data") or []
        else:
            rows = []
        if on_rows is not None and rows:
            await on_rows(rows, 0)
        return rows

    async def _fetch_date_range(self, spec, params, on_page, on_status, on_rows):
        start = params.pop("from", None)
        end = params.pop("to", None)
        if start is None or end is None:
            raise ValueError("DATE_RANGE pagination requires 'from' and 'to' in params")
        if isinstance(start, str):
            start = date.fromisoformat(start)
        if isinstance(end, str):
            end = date.fromisoformat(end)

        all_rows: list[dict] = []
        window_idx = 0
        chunk = timedelta(days=spec.date_chunk_days)
        cursor = start
        while cursor <= end:
            window_end = min(cursor + chunk - timedelta(days=1), end)
            page_rows = await self._request(spec.endpoint_path, {
                **params, "from": cursor.isoformat(), "to": window_end.isoformat(),
            })
            if on_rows is not None and page_rows:
                await on_rows(page_rows, window_idx)
            all_rows.extend(page_rows)
            if on_page is not None:
                await on_page(window_idx, len(all_rows))
            window_idx += 1
            cursor = window_end + timedelta(days=1)
        return all_rows

    async def _request(self, endpoint_path: str, params: dict) -> Any:
        async with self._lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            await self._quota.acquire(self.provider, self._daily_limit)
            url = f"{self.BASE_URL}{endpoint_path}"
            qs = {**params, "apikey": self._api_key}
            resp = await self._http.get(url, params=qs, timeout=30.0)
            self._last_call = time.monotonic()

        if resp.status_code == 429:
            await self._quota.mark_exhausted(self.provider)
            raise QuotaExhausted(self.provider, -1, self._daily_limit)
        if resp.status_code == 401:
            raise AdapterAuthError("FMP API key rejected")
        resp.raise_for_status()
        return resp.json()
