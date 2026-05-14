import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock

from coordinator.services.data_providers.theta import ThetaDataProvider


@pytest.fixture
def mock_http():
    return AsyncMock()


@pytest.fixture
def provider(mock_http):
    return ThetaDataProvider(username="testuser", password="testpass", http_client=mock_http)


class TestThetaDataProvider:
    @pytest.mark.asyncio
    async def test_auth_called_on_first_request(self, provider, mock_http):
        auth_response = MagicMock()
        auth_response.json.return_value = {"token": "test-token-123"}
        mock_http.post.return_value = auth_response

        eod_response = MagicMock()
        eod_response.json.return_value = {"response": []}
        mock_http.get.return_value = eod_response

        await provider.fetch_bars("AAPL", "1day", date(2024, 1, 1), date(2024, 1, 31))

        mock_http.post.assert_called_once()
        assert provider._token == "test-token-123"

    @pytest.mark.asyncio
    async def test_auth_not_repeated(self, provider, mock_http):
        provider._token = "existing-token"

        eod_response = MagicMock()
        eod_response.json.return_value = {"response": []}
        mock_http.get.return_value = eod_response

        await provider.fetch_bars("AAPL", "1day", date(2024, 1, 1), date(2024, 1, 31))

        mock_http.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_eod_bars(self, provider, mock_http):
        provider._token = "test-token"

        eod_response = MagicMock()
        eod_response.json.return_value = {
            "response": [
                {"date": "2024-01-02", "ms_of_day": 0, "open": 18500, "high": 19000, "low": 18200, "close": 18900, "volume": 50000},
                {"date": "2024-01-03", "ms_of_day": 0, "open": 18900, "high": 19200, "low": 18700, "close": 19100, "volume": 45000},
            ]
        }
        mock_http.get.return_value = eod_response

        bars = await provider.fetch_bars("AAPL", "1day", date(2024, 1, 1), date(2024, 1, 31))

        assert len(bars) == 2
        assert bars[0]["open"] == 185.0
        assert bars[0]["close"] == 189.0
        assert bars[1]["volume"] == 45000

    @pytest.mark.asyncio
    async def test_fetch_intraday_bars(self, provider, mock_http):
        provider._token = "test-token"

        response = MagicMock()
        response.json.return_value = {
            "response": [
                {"date": "2024-01-02", "ms_of_day": 34200000, "open": 18500, "high": 18600, "low": 18400, "close": 18550, "volume": 1000},
            ]
        }
        mock_http.get.return_value = response

        bars = await provider.fetch_bars("AAPL", "1min", date(2024, 1, 2), date(2024, 1, 2))

        assert len(bars) == 1
        assert bars[0]["open"] == 185.0
        assert "timestamp" in bars[0]

    @pytest.mark.asyncio
    async def test_empty_response(self, provider, mock_http):
        provider._token = "test-token"

        response = MagicMock()
        response.json.return_value = {"response": []}
        mock_http.get.return_value = response

        bars = await provider.fetch_bars("AAPL", "1day", date(2024, 1, 1), date(2024, 1, 31))
        assert bars == []

    @pytest.mark.asyncio
    async def test_auth_headers_included(self, provider, mock_http):
        provider._token = "my-token"

        response = MagicMock()
        response.json.return_value = {"response": []}
        mock_http.get.return_value = response

        await provider.fetch_bars("SPY", "1day", date(2024, 1, 1), date(2024, 1, 31))

        call_kwargs = mock_http.get.call_args
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer my-token"
