import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def seed_entities(client):
    """Create account and worker needed for algorithm instances."""
    acct_resp = await client.post("/api/accounts", json={
        "name": "Test Acct",
        "broker_type": "alpaca",
        "credentials": {"api_key": "k"},
        "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    worker_resp = await client.post("/api/workers", json={
        "name": "Test Pi",
        "tailscale_ip": "100.64.0.1",
    })
    return {
        "account_id": acct_resp.json()["id"],
        "worker_id": worker_resp.json()["id"],
    }


@pytest.mark.asyncio
async def test_create_algorithm(client):
    response = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/ElectricJack/momentum-scalper",
        "name": "momentum-scalper",
        "description": "Intraday momentum",
        "version": "1.0.0",
        "commit_hash": "abc123",
        "required_asset_types": ["equities"],
        "config_schema": {"parameters": []},
    })
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "momentum-scalper"
    assert body["install_status"] == "installed"


@pytest.mark.asyncio
async def test_list_algorithms(client):
    await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/algo1",
        "name": "algo-1",
    })
    await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/algo2",
        "name": "algo-2",
    })
    response = await client.get("/api/algorithms")
    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.asyncio
async def test_get_algorithm(client):
    create_resp = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/algo",
        "name": "test-algo",
    })
    algo_id = create_resp.json()["id"]
    response = await client.get(f"/api/algorithms/{algo_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "test-algo"


@pytest.mark.asyncio
async def test_delete_algorithm(client):
    create_resp = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/delete-me",
        "name": "delete-me",
    })
    algo_id = create_resp.json()["id"]
    response = await client.delete(f"/api/algorithms/{algo_id}")
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_create_instance(client, seed_entities):
    algo_resp = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/inst-algo",
        "name": "inst-algo",
    })
    algo_id = algo_resp.json()["id"]

    response = await client.post(f"/api/algorithms/{algo_id}/instances", json={
        "account_id": seed_entities["account_id"],
        "worker_id": seed_entities["worker_id"],
        "config_values": {"risk_per_trade": 0.02},
    })
    assert response.status_code == 201
    body = response.json()
    assert body["algorithm_id"] == algo_id
    assert body["account_id"] == seed_entities["account_id"]
    assert body["status"] == "stopped"
    assert body["config_values"] == {"risk_per_trade": 0.02}


@pytest.mark.asyncio
async def test_list_instances_for_algorithm(client, seed_entities):
    algo_resp = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/list-inst",
        "name": "list-inst",
    })
    algo_id = algo_resp.json()["id"]

    await client.post(f"/api/algorithms/{algo_id}/instances", json={
        "account_id": seed_entities["account_id"],
        "worker_id": seed_entities["worker_id"],
    })
    response = await client.get(f"/api/algorithms/{algo_id}/instances")
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_get_instance(client, seed_entities):
    algo_resp = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/get-inst",
        "name": "get-inst",
    })
    algo_id = algo_resp.json()["id"]

    inst_resp = await client.post(f"/api/algorithms/{algo_id}/instances", json={
        "account_id": seed_entities["account_id"],
        "worker_id": seed_entities["worker_id"],
    })
    inst_id = inst_resp.json()["id"]
    response = await client.get(f"/api/instances/{inst_id}")
    assert response.status_code == 200
    assert response.json()["id"] == inst_id
