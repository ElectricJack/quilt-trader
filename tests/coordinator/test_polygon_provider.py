import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import date
from coordinator.services.data_providers.polygon import PolygonProvider

@pytest.fixture
def mock_http():
    http = AsyncMock()
    http.get.return_value = MagicMock(
        status_code=200,
        json=MagicMock(return_value={
            "results": [
                {"t": 1704067200000, "o": 150.0, "h": 151.0, "l": 149.0, "c": 150.5, "v": 1000},
                {"t": 1704067260000, "o": 150.5, "h": 152.0, "l": 150.0, "c": 151.0, "v": 1500},
            ],
            "resultsCount": 2,
        }),
    )
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
    mock_http.get.return_value = MagicMock(
        status_code=200, json=MagicMock(return_value={"results": [], "resultsCount": 0}),
    )
    provider = PolygonProvider(api_key="test-key", http_client=mock_http)
    bars = await provider.fetch_bars("AAPL", "1day", date(2025, 1, 1), date(2025, 1, 1))
    assert bars == []

def test_timeframe_to_polygon_multiplier():
    provider = PolygonProvider(api_key="test")
    assert provider._timeframe_params("1min") == ("1", "minute")
    assert provider._timeframe_params("5min") == ("5", "minute")
    assert provider._timeframe_params("1hour") == ("1", "hour")
    assert provider._timeframe_params("1day") == ("1", "day")
