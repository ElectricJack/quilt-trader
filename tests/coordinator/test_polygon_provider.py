import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date
from coordinator.services.data_providers.polygon import PolygonProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int, body: dict, headers: dict | None = None) -> MagicMock:
    """Build a mock httpx-like response object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=body)
    resp.headers = headers or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


PAGE_1_BAR = {"t": 1704067200000, "o": 150.0, "h": 151.0, "l": 149.0, "c": 150.5, "v": 1000}
PAGE_2_BAR = {"t": 1704067260000, "o": 150.5, "h": 152.0, "l": 150.0, "c": 151.0, "v": 1500}


# ---------------------------------------------------------------------------
# Original tests (kept + adapted to new signature / _request_with_retry path)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_http():
    http = AsyncMock()
    http.get.return_value = _make_response(200, {
        "results": [PAGE_1_BAR, PAGE_2_BAR],
        "resultsCount": 2,
    })
    return http


@pytest.mark.asyncio
async def test_fetch_bars(mock_http):
    provider = PolygonProvider(api_key="test-key", http_client=mock_http)
    bars = await provider.fetch_bars(symbol="AAPL", timeframe="1min", start=date(2025, 1, 1), end=date(2025, 1, 2))
    assert len(bars) == 2
    assert bars[0]["open"] == 150.0
    assert bars[0]["close"] == 150.5
    assert "timestamp" in bars[0]
    mock_http.get.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_bars_empty_response(mock_http):
    mock_http.get.return_value = _make_response(200, {"results": [], "resultsCount": 0})
    provider = PolygonProvider(api_key="test-key", http_client=mock_http)
    bars = await provider.fetch_bars("AAPL", "1day", date(2025, 1, 1), date(2025, 1, 1))
    assert bars == []


def test_timeframe_to_polygon_multiplier():
    provider = PolygonProvider(api_key="test")
    assert provider._timeframe_params("1min") == ("1", "minute")
    assert provider._timeframe_params("5min") == ("5", "minute")
    assert provider._timeframe_params("1hour") == ("1", "hour")
    assert provider._timeframe_params("1day") == ("1", "day")


# ---------------------------------------------------------------------------
# Task 5 — new tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pagination_follows_next_url():
    """Two pages: first response has next_url, second does not. All bars concatenated."""
    http = AsyncMock()
    page1 = _make_response(200, {
        "results": [PAGE_1_BAR],
        "next_url": "https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/minute/2025-01-01/2025-01-02?cursor=abc123",
    })
    page2 = _make_response(200, {
        "results": [PAGE_2_BAR],
        # no next_url — signals end of pages
    })
    http.get.side_effect = [page1, page2]

    provider = PolygonProvider(api_key="test-key", http_client=http)
    bars = await provider.fetch_bars("AAPL", "1min", date(2025, 1, 1), date(2025, 1, 2))

    assert len(bars) == 2
    assert http.get.call_count == 2

    # Second call must use next_url and only re-add apiKey
    second_call_url = http.get.call_args_list[1].args[0]
    assert "cursor=abc123" in second_call_url
    second_call_params = http.get.call_args_list[1].kwargs.get("params", {})
    assert second_call_params == {"apiKey": "test-key"}


@pytest.mark.asyncio
async def test_rate_limit_retry_succeeds(monkeypatch):
    """First call returns 429 (Retry-After: 1), second returns 200. Succeeds with correct bars."""
    sleep_mock = AsyncMock()
    monkeypatch.setattr("coordinator.services.data_providers.polygon.asyncio.sleep", sleep_mock)

    http = AsyncMock()
    resp_429 = _make_response(429, {}, headers={"Retry-After": "1"})
    resp_200 = _make_response(200, {"results": [PAGE_1_BAR]})
    http.get.side_effect = [resp_429, resp_200]

    provider = PolygonProvider(api_key="test-key", http_client=http)
    bars = await provider.fetch_bars("AAPL", "1min", date(2025, 1, 1), date(2025, 1, 2))

    assert len(bars) == 1
    assert http.get.call_count == 2
    # asyncio.sleep was called at least once for the 429 back-off (via _sleep_with_status)
    sleep_mock.assert_awaited()


@pytest.mark.asyncio
async def test_rate_limit_exhausted_raises(monkeypatch):
    """All retries return 429 — RuntimeError must be raised."""
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    http = AsyncMock()
    http.get.return_value = _make_response(429, {}, headers={"Retry-After": "0"})

    provider = PolygonProvider(api_key="test-key", http_client=http)
    with pytest.raises(RuntimeError, match="Polygon request failed after"):
        await provider.fetch_bars("AAPL", "1min", date(2025, 1, 1), date(2025, 1, 2))

    # All max_retries attempts were made
    assert http.get.call_count == 5  # default max_retries=5


@pytest.mark.asyncio
async def test_min_request_interval_pacing():
    """With min_request_interval_s set, two sequential calls are spaced by at least that interval."""
    http = AsyncMock()
    http.get.return_value = _make_response(200, {"results": [PAGE_1_BAR]})

    interval = 0.05  # 50 ms — fast enough for tests, real enough to measure
    provider = PolygonProvider(api_key="test-key", http_client=http, min_request_interval_s=interval)

    t0 = time.monotonic()
    await provider._request_with_retry("https://example.com", {"apiKey": "test-key"})
    await provider._request_with_retry("https://example.com", {"apiKey": "test-key"})
    elapsed = time.monotonic() - t0

    # The second call should have been delayed by ~interval; total elapsed >= interval
    assert elapsed >= interval, f"Expected >= {interval}s elapsed, got {elapsed:.4f}s"


@pytest.mark.asyncio
async def test_pagination_emits_on_page_callback():
    """on_page callback is called once per page with correct (page_index, cumulative_bars) args."""
    page1 = _make_response(200, {
        "results": [{"t": 1000, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}],
        "next_url": "https://api.polygon.io/next?cursor=abc",
    })
    page2 = _make_response(200, {
        "results": [{"t": 2000, "o": 2, "h": 2, "l": 2, "c": 2, "v": 2}],
    })

    http = AsyncMock()
    http.get.side_effect = [page1, page2]

    callback_calls: list[tuple[int, int]] = []

    async def cb(page_idx: int, total: int) -> None:
        callback_calls.append((page_idx, total))

    provider = PolygonProvider(api_key="k", http_client=http)
    bars = await provider.fetch_bars("X", "1day", date(2025, 1, 1), date(2025, 1, 2), on_page=cb)

    assert len(bars) == 2
    assert callback_calls == [(0, 1), (1, 2)]


@pytest.mark.asyncio
async def test_pacing_emits_status_each_second():
    """Pacing sleep emits at least 2 'Pacing' countdown messages for a 2s interval."""
    http = AsyncMock()
    http.get.return_value = _make_response(200, {"results": [PAGE_1_BAR]})

    status_messages: list[str] = []

    async def on_status(msg: str) -> None:
        status_messages.append(msg)

    provider = PolygonProvider(api_key="test-key", http_client=http, min_request_interval_s=2.0)

    # First call sets _last_request_ts; second call triggers the pacing wait
    await provider._request_with_retry("https://example.com", {"apiKey": "test-key"}, on_status=on_status)
    status_messages.clear()  # discard any "Fetching…" heartbeats from the first call
    await provider._request_with_retry("https://example.com", {"apiKey": "test-key"}, on_status=on_status)

    pacing_msgs = [m for m in status_messages if "Pacing" in m]
    assert len(pacing_msgs) >= 2, f"Expected >= 2 Pacing messages, got: {pacing_msgs}"
    # Messages should contain countdown values
    assert any("s left" in m for m in pacing_msgs)


@pytest.mark.asyncio
async def test_429_retry_emits_status_each_second():
    """429 with Retry-After: 2 emits at least 2 'Rate limited' countdown messages."""
    http = AsyncMock()
    resp_429 = _make_response(429, {}, headers={"Retry-After": "2"})
    resp_200 = _make_response(200, {"results": [PAGE_1_BAR]})
    http.get.side_effect = [resp_429, resp_200]

    status_messages: list[str] = []

    async def on_status(msg: str) -> None:
        status_messages.append(msg)

    provider = PolygonProvider(api_key="test-key", http_client=http)
    bars = await provider.fetch_bars(
        "AAPL", "1min", date(2025, 1, 1), date(2025, 1, 2), on_status=on_status
    )

    assert len(bars) == 1
    rate_limit_msgs = [m for m in status_messages if "Rate limited" in m]
    assert len(rate_limit_msgs) >= 2, f"Expected >= 2 Rate limited messages, got: {rate_limit_msgs}"
    assert any("s left" in m for m in rate_limit_msgs)
