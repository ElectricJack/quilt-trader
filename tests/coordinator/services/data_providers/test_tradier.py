import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import date

from coordinator.services.data_providers.tradier import TradierProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int, body: dict) -> MagicMock:
    """Build a mock httpx-like response object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=body)
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


MULTI_DAY_RESPONSE = {
    "history": {
        "day": [
            {"date": "2025-01-02", "open": 100.0, "high": 105.0, "low": 99.0, "close": 103.0, "volume": 1000000},
            {"date": "2025-01-03", "open": 103.0, "high": 107.0, "low": 102.0, "close": 106.0, "volume": 1200000},
            {"date": "2025-01-06", "open": 106.0, "high": 110.0, "low": 105.5, "close": 109.0, "volume": 900000},
        ]
    }
}

SINGLE_DAY_RESPONSE = {
    "history": {
        "day": {"date": "2025-01-02", "open": 100.0, "high": 105.0, "low": 99.0, "close": 103.0, "volume": 1000000}
    }
}

NULL_HISTORY_RESPONSE = {"history": None}

EMPTY_DAY_RESPONSE = {"history": {"day": None}}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multi_day_response_returns_correct_bars():
    """Normal multi-day response returns all bars with correct field mapping."""
    http = AsyncMock()
    http.get.return_value = _make_response(200, MULTI_DAY_RESPONSE)

    provider = TradierProvider(access_token="test-token", http_client=http)
    bars = await provider.fetch_bars(
        symbol="AAPL",
        timeframe="1day",
        start=date(2025, 1, 2),
        end=date(2025, 1, 6),
    )

    assert len(bars) == 3

    # First bar field mapping
    assert bars[0]["open"] == 100.0
    assert bars[0]["high"] == 105.0
    assert bars[0]["low"] == 99.0
    assert bars[0]["close"] == 103.0
    assert bars[0]["volume"] == 1000000
    assert "timestamp" in bars[0]
    assert "2025-01-02" in bars[0]["timestamp"]

    # Second bar
    assert bars[1]["close"] == 106.0
    assert "2025-01-03" in bars[1]["timestamp"]

    # Third bar
    assert bars[2]["close"] == 109.0
    assert "2025-01-06" in bars[2]["timestamp"]


@pytest.mark.asyncio
async def test_multi_day_response_makes_correct_http_request():
    """Verifies the correct URL, params, and auth headers are used."""
    http = AsyncMock()
    http.get.return_value = _make_response(200, MULTI_DAY_RESPONSE)

    provider = TradierProvider(access_token="my-secret-token", http_client=http)
    await provider.fetch_bars(
        symbol="TSLA",
        timeframe="1day",
        start=date(2025, 1, 2),
        end=date(2025, 1, 6),
    )

    http.get.assert_called_once()
    call_kwargs = http.get.call_args

    # URL should contain the markets/history path
    url = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("url", "")
    assert "/markets/history" in url

    # Params should include symbol, interval, start, end
    params = call_kwargs.kwargs.get("params", {})
    assert params.get("symbol") == "TSLA"
    assert params.get("interval") == "daily"
    assert params.get("start") == "2025-01-02"
    assert params.get("end") == "2025-01-06"

    # Auth header should be present
    headers = call_kwargs.kwargs.get("headers", {})
    assert "Authorization" in headers
    assert "my-secret-token" in headers["Authorization"]


@pytest.mark.asyncio
async def test_single_day_response_dict_is_handled():
    """Single-day responses return a dict (not list) for the 'day' field — must be normalized."""
    http = AsyncMock()
    http.get.return_value = _make_response(200, SINGLE_DAY_RESPONSE)

    provider = TradierProvider(access_token="test-token", http_client=http)
    bars = await provider.fetch_bars(
        symbol="AAPL",
        timeframe="1day",
        start=date(2025, 1, 2),
        end=date(2025, 1, 2),
    )

    assert len(bars) == 1
    assert bars[0]["open"] == 100.0
    assert bars[0]["high"] == 105.0
    assert bars[0]["low"] == 99.0
    assert bars[0]["close"] == 103.0
    assert bars[0]["volume"] == 1000000
    assert "2025-01-02" in bars[0]["timestamp"]


@pytest.mark.asyncio
async def test_null_history_returns_empty_list():
    """When history is null/None in the response, return an empty list."""
    http = AsyncMock()
    http.get.return_value = _make_response(200, NULL_HISTORY_RESPONSE)

    provider = TradierProvider(access_token="test-token", http_client=http)
    bars = await provider.fetch_bars(
        symbol="AAPL",
        timeframe="1day",
        start=date(2025, 1, 2),
        end=date(2025, 1, 6),
    )

    assert bars == []


@pytest.mark.asyncio
async def test_null_day_field_returns_empty_list():
    """When history.day is null/None, return an empty list."""
    http = AsyncMock()
    http.get.return_value = _make_response(200, EMPTY_DAY_RESPONSE)

    provider = TradierProvider(access_token="test-token", http_client=http)
    bars = await provider.fetch_bars(
        symbol="AAPL",
        timeframe="1day",
        start=date(2025, 1, 2),
        end=date(2025, 1, 6),
    )

    assert bars == []


@pytest.mark.asyncio
async def test_unsupported_timeframe_raises_value_error():
    """Non-1day timeframes must raise ValueError."""
    http = AsyncMock()
    provider = TradierProvider(access_token="test-token", http_client=http)

    with pytest.raises(ValueError, match="1day"):
        await provider.fetch_bars(
            symbol="AAPL",
            timeframe="1min",
            start=date(2025, 1, 2),
            end=date(2025, 1, 6),
        )

    with pytest.raises(ValueError):
        await provider.fetch_bars(
            symbol="AAPL",
            timeframe="5min",
            start=date(2025, 1, 2),
            end=date(2025, 1, 6),
        )


@pytest.mark.asyncio
async def test_sandbox_uses_sandbox_base_url():
    """When sandbox=True, the sandbox base URL is used."""
    http = AsyncMock()
    http.get.return_value = _make_response(200, MULTI_DAY_RESPONSE)

    provider = TradierProvider(access_token="test-token", sandbox=True, http_client=http)
    await provider.fetch_bars(
        symbol="AAPL",
        timeframe="1day",
        start=date(2025, 1, 2),
        end=date(2025, 1, 6),
    )

    url = http.get.call_args.args[0]
    assert "sandbox.tradier.com" in url


@pytest.mark.asyncio
async def test_live_uses_live_base_url():
    """When sandbox=False (default), the live base URL is used."""
    http = AsyncMock()
    http.get.return_value = _make_response(200, MULTI_DAY_RESPONSE)

    provider = TradierProvider(access_token="test-token", http_client=http)
    await provider.fetch_bars(
        symbol="AAPL",
        timeframe="1day",
        start=date(2025, 1, 2),
        end=date(2025, 1, 6),
    )

    url = http.get.call_args.args[0]
    assert "api.tradier.com" in url


@pytest.mark.asyncio
async def test_callbacks_invoked():
    """on_page, on_status, and on_bars callbacks are invoked during a successful fetch."""
    http = AsyncMock()
    http.get.return_value = _make_response(200, MULTI_DAY_RESPONSE)

    provider = TradierProvider(access_token="test-token", http_client=http)

    page_calls: list[tuple] = []
    status_msgs: list[str] = []
    bars_batches: list[list[dict]] = []

    async def on_page(page_idx: int, total: int, fraction) -> None:
        page_calls.append((page_idx, total, fraction))

    async def on_status(msg: str) -> None:
        status_msgs.append(msg)

    async def on_bars(bars: list[dict]) -> None:
        bars_batches.append(bars)

    bars = await provider.fetch_bars(
        symbol="AAPL",
        timeframe="1day",
        start=date(2025, 1, 2),
        end=date(2025, 1, 6),
        on_page=on_page,
        on_status=on_status,
        on_bars=on_bars,
    )

    assert len(bars) == 3
    # on_page called once (single request, no pagination)
    assert len(page_calls) == 1
    assert page_calls[0][0] == 0
    assert page_calls[0][1] == 3
    # on_bars called once with all bars
    assert len(bars_batches) == 1
    assert len(bars_batches[0]) == 3
