import pytest
import pytest_asyncio

@pytest_asyncio.fixture
async def seed_comparison(client):
    acct = await client.post("/api/accounts", json={
        "name": "BT Acct", "broker_type": "alpaca",
        "credentials": {"k": "v"}, "supported_asset_types": ["equities"], "pdt_mode": "off",
    })
    worker = await client.post("/api/workers", json={"name": "BT Pi", "tailscale_ip": "100.64.0.20"})
    algo = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/bt-algo", "name": "bt-algo",
    })
    inst = await client.post(f"/api/algorithms/{algo.json()['id']}/instances", json={
        "account_id": acct.json()["id"], "worker_id": worker.json()["id"],
    })
    return {"algorithm_id": algo.json()["id"], "instance_id": inst.json()["id"]}

@pytest.mark.asyncio
async def test_list_comparisons_empty(client):
    response = await client.get("/api/backtests")
    assert response.status_code == 200
    assert response.json() == []

@pytest.mark.asyncio
async def test_create_and_list_comparison(client, seed_comparison):
    response = await client.post("/api/backtests", json={
        "instance_id": seed_comparison["instance_id"],
        "algorithm_id": seed_comparison["algorithm_id"],
        "time_range_start": "2025-01-01T00:00:00+00:00",
        "time_range_end": "2025-01-02T00:00:00+00:00",
        "total_ticks": 100, "matching_ticks": 95, "match_percentage": 95.0,
        "divergences": [{"timestamp": "2025-01-01T10:00:00", "reason": "Signal mismatch"}],
        "summary": "5% divergence in afternoon session",
    })
    assert response.status_code == 201
    list_resp = await client.get("/api/backtests")
    assert len(list_resp.json()) == 1
    assert list_resp.json()[0]["match_percentage"] == 95.0
