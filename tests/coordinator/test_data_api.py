import pytest
from unittest.mock import patch, MagicMock
import pandas as pd


@pytest.mark.asyncio
async def test_get_market_data(client, tmp_path):
    df = pd.DataFrame({
        "timestamp": ["2025-01-01", "2025-01-02"],
        "open": [150.0, 151.0], "high": [151.0, 152.0],
        "low": [149.0, 150.0], "close": [150.5, 151.5], "volume": [1000, 1500],
    })
    with patch("coordinator.api.routes.data.get_data_service") as mock:
        svc = MagicMock()
        svc.load_market_data.return_value = df
        mock.return_value = svc
        response = await client.get("/api/data/market/AAPL?timeframe=1day")
        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 2
        assert body["data"][0]["close"] == 150.5
        # Windowing metadata
        assert body["total"] == 2
        assert body["truncated"] is False


@pytest.mark.asyncio
async def test_get_market_data_limit(client):
    """Endpoint should return at most `limit` rows (the most-recent ones)."""
    rows = 10
    df = pd.DataFrame({
        "timestamp": [f"2025-01-{i+1:02d}" for i in range(rows)],
        "open": [float(i) for i in range(rows)],
        "high": [float(i) for i in range(rows)],
        "low": [float(i) for i in range(rows)],
        "close": [float(i) for i in range(rows)],
        "volume": [100] * rows,
    })
    with patch("coordinator.api.routes.data.get_data_service") as mock:
        svc = MagicMock()
        svc.load_market_data.return_value = df
        mock.return_value = svc
        response = await client.get("/api/data/market/AAPL?timeframe=1day&limit=3")
        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 3
        assert body["total"] == rows
        assert body["truncated"] is True
        # Should be the 3 most-recent rows
        assert body["data"][-1]["close"] == float(rows - 1)


@pytest.mark.asyncio
async def test_get_market_data_meta(client):
    df = pd.DataFrame({
        "timestamp": ["2025-01-01", "2025-06-01"],
        "open": [150.0, 151.0], "high": [151.0, 152.0],
        "low": [149.0, 150.0], "close": [150.5, 151.5], "volume": [1000, 1500],
    })
    with patch("coordinator.api.routes.data.get_data_service") as mock:
        svc = MagicMock()
        svc.load_market_data.return_value = df
        mock.return_value = svc
        response = await client.get("/api/data/market/AAPL/meta?timeframe=1day")
        assert response.status_code == 200
        body = response.json()
        assert body["total_bars"] == 2
        assert body["first_timestamp"] is not None
        assert body["last_timestamp"] is not None


@pytest.mark.asyncio
async def test_get_market_data_not_found(client):
    with patch("coordinator.api.routes.data.get_data_service") as mock:
        svc = MagicMock()
        svc.load_market_data.return_value = None
        mock.return_value = svc
        response = await client.get("/api/data/market/MISSING?timeframe=1day")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_custom_data(client):
    df = pd.DataFrame({"symbol": ["TSLA"], "score": [0.95]})
    with patch("coordinator.api.routes.data.get_data_service") as mock:
        svc = MagicMock()
        svc.load_custom_data.return_value = df
        mock.return_value = svc
        response = await client.get("/api/data/custom/alpha-picks")
        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["symbol"] == "TSLA"


@pytest.mark.asyncio
async def test_list_available_data(client):
    with patch("coordinator.api.routes.data.get_data_service") as mock:
        svc = MagicMock()
        svc.list_available_market_data.return_value = [
            {"provider": "polygon", "symbol": "AAPL", "timeframe": "1day", "size_bytes": 1024},
        ]
        mock.return_value = svc
        response = await client.get("/api/data/available")
        assert response.status_code == 200
        assert len(response.json()) == 1
