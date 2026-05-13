import pytest


@pytest.mark.asyncio
async def test_get_settings_empty(client):
    response = await client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["github_pat_set"] is False
    assert body["discord_bot_token_set"] is False
    assert body["polygon_api_key_set"] is False
    assert body["theta_data_set"] is False


@pytest.mark.asyncio
async def test_set_github_pat(client):
    response = await client.put("/api/settings/github-pat", json={
        "value": "ghp_1234567890abcdef",
    })
    assert response.status_code == 200
    assert response.json()["github_pat_set"] is True

    get_resp = await client.get("/api/settings")
    assert get_resp.json()["github_pat_set"] is True


@pytest.mark.asyncio
async def test_set_discord_token(client):
    response = await client.put("/api/settings/discord-token", json={
        "value": "MTIzNDU2Nzg5.discord.token",
    })
    assert response.status_code == 200
    assert response.json()["discord_bot_token_set"] is True


@pytest.mark.asyncio
async def test_set_polygon_key(client):
    response = await client.put("/api/settings/polygon-key", json={
        "value": "pk_abcdefghij",
    })
    assert response.status_code == 200
    assert response.json()["polygon_api_key_set"] is True


@pytest.mark.asyncio
async def test_set_theta_data_credentials(client):
    response = await client.put("/api/settings/theta-data", json={
        "username": "user@example.com",
        "password": "secret123",
    })
    assert response.status_code == 200
    assert response.json()["theta_data_set"] is True


@pytest.mark.asyncio
async def test_delete_github_pat(client):
    await client.put("/api/settings/github-pat", json={"value": "ghp_test"})
    response = await client.delete("/api/settings/github-pat")
    assert response.status_code == 200
    assert response.json()["github_pat_set"] is False
