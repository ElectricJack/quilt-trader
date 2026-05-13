import pytest


@pytest.mark.asyncio
async def test_create_account(client):
    response = await client.post("/api/accounts", json={
        "name": "Alpaca Main",
        "broker_type": "alpaca",
        "credentials": {"api_key": "pk_123", "api_secret": "sk_456"},
        "supported_asset_types": ["equities", "options", "crypto"],
        "options_level": 3,
        "account_features": ["margin"],
        "pdt_mode": "warn",
    })
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Alpaca Main"
    assert body["broker_type"] == "alpaca"
    assert body["supported_asset_types"] == ["equities", "options", "crypto"]
    assert body["options_level"] == 3
    assert body["pdt_mode"] == "warn"
    assert "id" in body
    assert "credentials" not in body


@pytest.mark.asyncio
async def test_list_accounts(client):
    await client.post("/api/accounts", json={
        "name": "Account 1",
        "broker_type": "alpaca",
        "credentials": {"api_key": "k1"},
        "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    await client.post("/api/accounts", json={
        "name": "Account 2",
        "broker_type": "tradier",
        "credentials": {"api_key": "k2"},
        "supported_asset_types": ["equities", "options"],
        "pdt_mode": "block",
    })
    response = await client.get("/api/accounts")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2


@pytest.mark.asyncio
async def test_get_account(client):
    create_resp = await client.post("/api/accounts", json={
        "name": "Get Test",
        "broker_type": "alpaca",
        "credentials": {"api_key": "k"},
        "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    account_id = create_resp.json()["id"]
    response = await client.get(f"/api/accounts/{account_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Get Test"


@pytest.mark.asyncio
async def test_get_account_not_found(client):
    response = await client.get("/api/accounts/nonexistent-id")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_account(client):
    create_resp = await client.post("/api/accounts", json={
        "name": "Before Update",
        "broker_type": "alpaca",
        "credentials": {"api_key": "k"},
        "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    account_id = create_resp.json()["id"]
    response = await client.patch(f"/api/accounts/{account_id}", json={
        "name": "After Update",
        "pdt_mode": "block",
    })
    assert response.status_code == 200
    assert response.json()["name"] == "After Update"
    assert response.json()["pdt_mode"] == "block"


@pytest.mark.asyncio
async def test_delete_account(client):
    create_resp = await client.post("/api/accounts", json={
        "name": "To Delete",
        "broker_type": "alpaca",
        "credentials": {"api_key": "k"},
        "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    account_id = create_resp.json()["id"]
    response = await client.delete(f"/api/accounts/{account_id}")
    assert response.status_code == 204

    get_resp = await client.get(f"/api/accounts/{account_id}")
    assert get_resp.status_code == 404
