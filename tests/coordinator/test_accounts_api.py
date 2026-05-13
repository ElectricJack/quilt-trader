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


@pytest.mark.asyncio
async def test_accounts_snapshots_latest(client, db_session):
    from datetime import datetime, timedelta, timezone
    from coordinator.database.models import Account, AccountSnapshot

    acct = Account(
        name="Alpaca Main", broker_type="alpaca",
        supported_asset_types=["equities"], pdt_mode="off",
        credentials="{}",
    )
    db_session.add(acct)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add(AccountSnapshot(
        account_id=acct.id, timestamp=now - timedelta(hours=25),
        total_value=10000.0, cash=4000.0, positions_value=6000.0,
        source="seed",
    ))
    db_session.add(AccountSnapshot(
        account_id=acct.id, timestamp=now,
        total_value=10500.0, cash=4000.0, positions_value=6500.0,
        source="seed",
    ))
    await db_session.commit()

    response = await client.get("/api/accounts/snapshots/latest")
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["account_name"] == "Alpaca Main"
    assert item["latest"]["total_value"] == 10500.0
    assert item["prior"]["total_value"] == 10000.0
    assert item["day_pct"] == pytest.approx(5.0)
