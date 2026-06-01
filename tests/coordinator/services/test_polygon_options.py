# tests/coordinator/services/test_polygon_options.py
import pytest
import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_http():
    client = AsyncMock()
    return client


def _contracts_response(results):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"results": results, "status": "OK"}
    return resp


def test_discover_option_contracts_returns_list(mock_http):
    from coordinator.services.data_providers.polygon import PolygonProvider

    contract_results = [
        {
            "ticker": "O:SPY250620C00450000",
            "strike_price": 450.0,
            "contract_type": "call",
            "expiration_date": "2025-06-20",
        },
        {
            "ticker": "O:SPY250620P00450000",
            "strike_price": 450.0,
            "contract_type": "put",
            "expiration_date": "2025-06-20",
        },
    ]

    mock_http.get = AsyncMock(return_value=_contracts_response(contract_results))

    provider = PolygonProvider(api_key="test-key", http_client=mock_http)
    contracts = asyncio.get_event_loop().run_until_complete(
        provider.discover_option_contracts(
            "SPY", date(2025, 6, 20),
            strike_range_pct=1.0,  # skip price lookup
        )
    )

    assert len(contracts) == 2
    assert contracts[0]["ticker"] == "O:SPY250620C00450000"
    assert contracts[0]["strike_price"] == 450.0
    assert contracts[0]["contract_type"] == "call"
    assert contracts[1]["contract_type"] == "put"


def test_discover_option_contracts_empty_results(mock_http):
    from coordinator.services.data_providers.polygon import PolygonProvider

    mock_http.get = AsyncMock(return_value=_contracts_response([]))
    provider = PolygonProvider(api_key="test-key", http_client=mock_http)
    contracts = asyncio.get_event_loop().run_until_complete(
        provider.discover_option_contracts(
            "SPY", date(2025, 6, 20),
            strike_range_pct=1.0,
        )
    )
    assert contracts == []


def test_discover_option_contracts_url_format(mock_http):
    from coordinator.services.data_providers.polygon import PolygonProvider

    mock_http.get = AsyncMock(return_value=_contracts_response([]))
    provider = PolygonProvider(api_key="test-key", http_client=mock_http)
    asyncio.get_event_loop().run_until_complete(
        provider.discover_option_contracts(
            "SPY", date(2025, 6, 20),
            strike_range_pct=1.0,
        )
    )

    call_args = mock_http.get.call_args
    url = call_args[0][0] if call_args[0] else call_args[1].get("url", "")
    params = call_args[1].get("params", {}) if call_args[1] else {}
    assert "options" in url or "contracts" in url
    assert params.get("underlying_ticker") == "SPY"
