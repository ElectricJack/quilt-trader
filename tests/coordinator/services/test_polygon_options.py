# tests/coordinator/services/test_polygon_options.py
import pytest
import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock

@pytest.fixture
def mock_http():
    client = AsyncMock()
    return client

def _snapshot_response(results):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"results": results, "status": "OK"}
    return resp

def test_fetch_option_chain_returns_dataframe(mock_http):
    from coordinator.services.data_providers.polygon import PolygonProvider

    snapshot_results = [
        {
            "details": {
                "ticker": "O:SPY250620C00450000",
                "strike_price": 450.0,
                "contract_type": "call",
                "expiration_date": "2025-06-20",
            },
            "day": {"open": 5.0, "high": 5.5, "low": 4.8, "close": 5.2, "volume": 1200},
            "last_quote": {"bid": 5.1, "ask": 5.3},
            "greeks": {"delta": 0.55, "gamma": 0.03, "theta": -0.05, "vega": 0.12},
            "implied_volatility": 0.25,
            "open_interest": 8000,
        },
        {
            "details": {
                "ticker": "O:SPY250620P00450000",
                "strike_price": 450.0,
                "contract_type": "put",
                "expiration_date": "2025-06-20",
            },
            "day": {"open": 4.0, "high": 4.5, "low": 3.8, "close": 4.2, "volume": 900},
            "last_quote": {"bid": 4.1, "ask": 4.3},
            "greeks": {"delta": -0.45, "gamma": 0.03, "theta": -0.04, "vega": 0.11},
            "implied_volatility": 0.27,
            "open_interest": 6000,
        },
    ]

    mock_http.get = AsyncMock(return_value=_snapshot_response(snapshot_results))

    provider = PolygonProvider(api_key="test-key", http_client=mock_http)
    df = asyncio.get_event_loop().run_until_complete(
        provider.fetch_option_chain("SPY", date(2025, 6, 20))
    )

    assert len(df) == 2
    assert "strike" in df.columns
    assert "option_type" in df.columns
    assert "bid" in df.columns
    assert "ask" in df.columns
    assert "implied_volatility" in df.columns

    call_row = df[df["option_type"] == "call"].iloc[0]
    assert call_row["strike"] == 450.0
    assert call_row["bid"] == 5.1
    assert call_row["ask"] == 5.3

def test_fetch_option_chain_empty_results(mock_http):
    from coordinator.services.data_providers.polygon import PolygonProvider

    mock_http.get = AsyncMock(return_value=_snapshot_response([]))
    provider = PolygonProvider(api_key="test-key", http_client=mock_http)
    df = asyncio.get_event_loop().run_until_complete(
        provider.fetch_option_chain("SPY", date(2025, 6, 20))
    )
    assert len(df) == 0
    assert "strike" in df.columns

def test_fetch_option_chain_url_format(mock_http):
    from coordinator.services.data_providers.polygon import PolygonProvider

    mock_http.get = AsyncMock(return_value=_snapshot_response([]))
    provider = PolygonProvider(api_key="test-key", http_client=mock_http)
    asyncio.get_event_loop().run_until_complete(
        provider.fetch_option_chain("SPY", date(2025, 6, 20))
    )

    call_args = mock_http.get.call_args
    url = call_args[0][0] if call_args[0] else call_args[1].get("url", "")
    assert "snapshot" in url or "options" in url
    assert "SPY" in url
