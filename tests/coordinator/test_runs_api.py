import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def seed_instance(client):
    acct = await client.post("/api/accounts", json={
        "name": "Run Acct", "broker_type": "alpaca",
        "credentials": {"k": "v"}, "supported_asset_types": ["equities"], "pdt_mode": "off",
    })
    worker = await client.post("/api/workers", json={"name": "Run Pi", "tailscale_ip": "100.64.0.10"})
    algo = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/run-algo", "name": "run-algo",
    })
    inst = await client.post(f"/api/algorithms/{algo.json()['id']}/instances", json={
        "account_id": acct.json()["id"], "worker_id": worker.json()["id"],
    })
    return inst.json()["id"]


@pytest.mark.asyncio
async def test_list_runs_empty(client, seed_instance):
    response = await client.get(f"/api/instances/{seed_instance}/runs")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_and_list_run(client, seed_instance):
    create_resp = await client.post(f"/api/instances/{seed_instance}/runs", json={"starting_equity": 50000.0})
    assert create_resp.status_code == 201
    body = create_resp.json()
    assert body["run_number"] == 1
    assert body["status"] == "running"
    assert body["starting_equity"] == 50000.0
    list_resp = await client.get(f"/api/instances/{seed_instance}/runs")
    assert len(list_resp.json()) == 1
