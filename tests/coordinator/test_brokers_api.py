import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_asset_types_alpaca(client: AsyncClient):
    r = await client.get("/api/brokers/alpaca/asset-types")
    assert r.status_code == 200
    assert r.json() == {"asset_types": ["equities", "options", "crypto"]}


@pytest.mark.asyncio
async def test_get_asset_types_unknown_broker_404(client: AsyncClient):
    r = await client.get("/api/brokers/ibkr/asset-types")
    assert r.status_code == 404
