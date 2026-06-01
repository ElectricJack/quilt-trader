import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def seed_account(client):
    resp = await client.post("/api/accounts", json={
        "name": "CF Acct", "broker_type": "alpaca",
        "credentials": {"api_key": "k", "secret_key": "v"}, "supported_asset_types": ["equities"], "pdt_mode": "off",
    })
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_create_cash_flow(client, seed_account):
    response = await client.post(f"/api/accounts/{seed_account}/cash-flows", json={
        "type": "deposit", "amount": 10000.0, "notes": "Initial deposit",
    })
    assert response.status_code == 201
    assert response.json()["type"] == "deposit"
    assert response.json()["amount"] == 10000.0


@pytest.mark.asyncio
async def test_list_cash_flows(client, seed_account):
    await client.post(f"/api/accounts/{seed_account}/cash-flows", json={"type": "deposit", "amount": 10000.0})
    await client.post(f"/api/accounts/{seed_account}/cash-flows", json={"type": "withdrawal", "amount": -2000.0})
    response = await client.get(f"/api/accounts/{seed_account}/cash-flows")
    assert response.status_code == 200
    assert len(response.json()) == 2
