import time
import pytest
import pandas as pd
from worker.data_client import DataClient


class FakeHTTPResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code
    def json(self):
        return self._json
    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class FakeHTTPClient:
    def __init__(self):
        self.call_count = 0
        self.responses = {}
    def set_response(self, url, data):
        self.responses[url] = data
    async def get(self, url, **kwargs):
        self.call_count += 1
        data = self.responses.get(url, {"data": []})
        return FakeHTTPResponse(data)
    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_fetch_market_data():
    http = FakeHTTPClient()
    http.set_response("http://coordinator:8000/api/data/market/AAPL", {
        "data": [
            {"timestamp": "2025-01-01T09:30:00", "open": 150.0, "high": 151.0, "low": 149.0, "close": 150.5, "volume": 1000},
            {"timestamp": "2025-01-01T09:31:00", "open": 150.5, "high": 152.0, "low": 150.0, "close": 151.0, "volume": 1500},
        ]
    })
    client = DataClient(base_url="http://coordinator:8000", cache_ttl=60, http_client=http)
    df = await client.get_market_data("AAPL", timeframe="1min", bars=100)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert "close" in df.columns


@pytest.mark.asyncio
async def test_fetch_custom_data():
    http = FakeHTTPClient()
    http.set_response("http://coordinator:8000/api/data/custom/alpha-picks", {
        "data": [{"symbol": "TSLA", "score": 0.95}, {"symbol": "NVDA", "score": 0.88}]
    })
    client = DataClient(base_url="http://coordinator:8000", cache_ttl=60, http_client=http)
    df = await client.get_custom_data("alpha-picks")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert "symbol" in df.columns


@pytest.mark.asyncio
async def test_cache_prevents_duplicate_requests():
    http = FakeHTTPClient()
    http.set_response("http://coordinator:8000/api/data/custom/alpha-picks", {"data": [{"symbol": "TSLA"}]})
    client = DataClient(base_url="http://coordinator:8000", cache_ttl=60, http_client=http)
    await client.get_custom_data("alpha-picks")
    await client.get_custom_data("alpha-picks")
    assert http.call_count == 1


@pytest.mark.asyncio
async def test_cache_expires():
    http = FakeHTTPClient()
    http.set_response("http://coordinator:8000/api/data/custom/alpha-picks", {"data": [{"symbol": "TSLA"}]})
    client = DataClient(base_url="http://coordinator:8000", cache_ttl=0, http_client=http)
    await client.get_custom_data("alpha-picks")
    await client.get_custom_data("alpha-picks")
    assert http.call_count == 2


@pytest.mark.asyncio
async def test_clear_cache():
    http = FakeHTTPClient()
    http.set_response("http://coordinator:8000/api/data/custom/test", {"data": [{"val": 1}]})
    client = DataClient(base_url="http://coordinator:8000", cache_ttl=60, http_client=http)
    await client.get_custom_data("test")
    client.clear_cache()
    await client.get_custom_data("test")
    assert http.call_count == 2
