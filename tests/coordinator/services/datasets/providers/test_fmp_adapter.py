import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock
from coordinator.services.datasets.providers.fmp import FMPAdapter
from coordinator.services.datasets.quota import QuotaTracker, QuotaExhausted
from coordinator.services.datasets.adapter import AdapterAuthError
from coordinator.services.datasets.registry import DatasetSpec, Pagination


def _resp(status: int, json_body=None, body_text: str | None = None):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=json_body or [])
    r.text = body_text or ""
    r.raise_for_status = MagicMock()
    if status >= 400:
        from httpx import HTTPStatusError, Request, Response
        r.raise_for_status.side_effect = HTTPStatusError(
            "err", request=Request("GET", "http://x"), response=Response(status))
    return r


@pytest.fixture
def quota_ok():
    q = MagicMock()
    q.acquire = AsyncMock(return_value=None)
    q.mark_exhausted = AsyncMock(return_value=None)
    return q


@pytest.fixture
def http():
    h = MagicMock()
    h.get = AsyncMock()
    return h


@pytest.fixture
def adapter(quota_ok, http):
    return FMPAdapter(api_key="K", http_client=http, quota_tracker=quota_ok,
                      daily_limit=250, min_request_interval_s=0.0)


@pytest.mark.asyncio
async def test_request_appends_apikey_query_param(adapter, http):
    http.get.return_value = _resp(200, json_body=[])
    await adapter._request("/stable/something", {"page": 0})
    args, kwargs = http.get.call_args
    assert kwargs["params"] == {"page": 0, "apikey": "K"}
    assert args[0] == "https://financialmodelingprep.com/stable/something"


@pytest.mark.asyncio
async def test_request_acquires_quota_before_calling(adapter, http, quota_ok):
    http.get.return_value = _resp(200, json_body=[])
    await adapter._request("/x", {})
    quota_ok.acquire.assert_awaited_once_with("fmp", 250)


@pytest.mark.asyncio
async def test_429_marks_exhausted_and_raises(adapter, http, quota_ok):
    http.get.return_value = _resp(429)
    with pytest.raises(QuotaExhausted):
        await adapter._request("/x", {})
    quota_ok.mark_exhausted.assert_awaited_once_with("fmp")


@pytest.mark.asyncio
async def test_401_raises_adapter_auth_error(adapter, http):
    http.get.return_value = _resp(401)
    with pytest.raises(AdapterAuthError):
        await adapter._request("/x", {})


@pytest.mark.asyncio
async def test_pacing_enforces_minimum_interval(quota_ok, http):
    http.get.return_value = _resp(200, json_body=[])
    a = FMPAdapter(api_key="K", http_client=http, quota_tracker=quota_ok,
                   daily_limit=250, min_request_interval_s=0.1)
    t0 = time.monotonic()
    await a._request("/x", {})
    await a._request("/x", {})
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.1


@pytest.mark.asyncio
async def test_acquire_raising_short_circuits_http(adapter, http, quota_ok):
    quota_ok.acquire.side_effect = QuotaExhausted("fmp", 250, 250)
    with pytest.raises(QuotaExhausted):
        await adapter._request("/x", {})
    http.get.assert_not_awaited()


def _page_spec():
    return DatasetSpec(
        name="fmp.t_page", provider="fmp", endpoint_path="/stable/page-thing",
        event_date_column="d", knowledge_date_column="d",
        symbol_keyed=False, id_columns=("d", "x"),
        columns={"d": "date", "x": "int"}, pagination=Pagination.PAGE, page_size=2,
    )


@pytest.mark.asyncio
async def test_page_pagination_terminates_on_empty(adapter, http):
    http.get.side_effect = [
        _resp(200, [{"d": "2024-01-01", "x": 1}, {"d": "2024-01-02", "x": 2}]),
        _resp(200, [{"d": "2024-01-03", "x": 3}]),
        _resp(200, []),
    ]
    rows = await adapter.fetch_dataset(_page_spec(), {})
    assert len(rows) == 3
    assert http.get.await_count == 3


@pytest.mark.asyncio
async def test_page_pagination_invokes_on_rows_per_page(adapter, http):
    http.get.side_effect = [
        _resp(200, [{"d": "2024-01-01", "x": 1}]),
        _resp(200, []),
    ]
    seen = []
    async def on_rows(rows, page_idx): seen.append((page_idx, len(rows)))
    await adapter.fetch_dataset(_page_spec(), {}, on_rows=on_rows)
    assert seen == [(0, 1)]


@pytest.mark.asyncio
async def test_page_pagination_passes_page_and_limit(adapter, http):
    http.get.side_effect = [_resp(200, [])]
    await adapter.fetch_dataset(_page_spec(), {"symbol": "AAPL"})
    args, kwargs = http.get.call_args_list[0]
    assert kwargs["params"]["page"] == 0
    assert kwargs["params"]["limit"] == 2
    assert kwargs["params"]["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_page_pagination_invokes_on_page_after_each(adapter, http):
    http.get.side_effect = [
        _resp(200, [{"d": "2024-01-01", "x": 1}]),
        _resp(200, [{"d": "2024-01-02", "x": 2}]),
        _resp(200, []),
    ]
    pages = []
    async def on_page(idx, total): pages.append((idx, total))
    await adapter.fetch_dataset(_page_spec(), {}, on_page=on_page)
    assert pages == [(0, 1), (1, 2)]
