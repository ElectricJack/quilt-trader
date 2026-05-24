import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import date
from coordinator.services.data_providers.polygon import PolygonProvider


@pytest.mark.asyncio
async def test_discover_option_contracts_returns_occ_symbols():
    mock_http = AsyncMock()
    price_resp = MagicMock()
    price_resp.status_code = 200
    price_resp.json.return_value = {"results": [{"c": 500.0}]}
    contracts_resp = MagicMock()
    contracts_resp.status_code = 200
    contracts_resp.json.return_value = {
        "results": [
            {"ticker": "O:SPY250620C00490000", "strike_price": 490.0, "contract_type": "call"},
            {"ticker": "O:SPY250620P00490000", "strike_price": 490.0, "contract_type": "put"},
            {"ticker": "O:SPY250620C00510000", "strike_price": 510.0, "contract_type": "call"},
        ],
        "next_url": None,
    }
    mock_http.get = AsyncMock(side_effect=[price_resp, contracts_resp])

    provider = PolygonProvider(api_key="test", http_client=mock_http)
    result = await provider.discover_option_contracts("SPY", date(2025, 6, 20))

    assert len(result) == 3
    assert result[0]["ticker"] == "O:SPY250620C00490000"
    assert result[0]["strike_price"] == 490.0


@pytest.mark.asyncio
async def test_discover_option_contracts_respects_max_contracts():
    mock_http = AsyncMock()
    price_resp = MagicMock()
    price_resp.status_code = 200
    price_resp.json.return_value = {"results": [{"c": 500.0}]}
    contracts = [
        {"ticker": f"O:SPY250620C00{480+i:03d}000", "strike_price": 480.0 + i, "contract_type": "call"}
        for i in range(20)
    ]
    contracts_resp = MagicMock()
    contracts_resp.status_code = 200
    contracts_resp.json.return_value = {"results": contracts, "next_url": None}
    mock_http.get = AsyncMock(side_effect=[price_resp, contracts_resp])

    provider = PolygonProvider(api_key="test", http_client=mock_http)
    result = await provider.discover_option_contracts(
        "SPY", date(2025, 6, 20), max_contracts=5,
    )
    assert len(result) == 5
