import pytest


@pytest.mark.asyncio
async def test_create_worker(client):
    response = await client.post("/api/workers", json={
        "name": "Pi Living Room",
        "tailscale_ip": "100.64.0.1",
        "max_algorithms": 3,
    })
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Pi Living Room"
    assert body["tailscale_ip"] == "100.64.0.1"
    assert body["status"] == "offline"
    assert body["max_algorithms"] == 3
    assert "id" in body


@pytest.mark.asyncio
async def test_list_workers(client):
    await client.post("/api/workers", json={
        "name": "Pi A",
        "tailscale_ip": "100.64.0.1",
    })
    await client.post("/api/workers", json={
        "name": "Pi B",
        "tailscale_ip": "100.64.0.2",
    })
    response = await client.get("/api/workers")
    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.asyncio
async def test_get_worker(client):
    create_resp = await client.post("/api/workers", json={
        "name": "Get Test Pi",
        "tailscale_ip": "100.64.0.3",
    })
    worker_id = create_resp.json()["id"]
    response = await client.get(f"/api/workers/{worker_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Get Test Pi"


@pytest.mark.asyncio
async def test_get_worker_not_found(client):
    response = await client.get("/api/workers/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_worker(client):
    create_resp = await client.post("/api/workers", json={
        "name": "Old Name",
        "tailscale_ip": "100.64.0.4",
    })
    worker_id = create_resp.json()["id"]
    response = await client.patch(f"/api/workers/{worker_id}", json={
        "name": "New Name",
        "max_algorithms": 5,
    })
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"
    assert response.json()["max_algorithms"] == 5


@pytest.mark.asyncio
async def test_delete_worker(client):
    create_resp = await client.post("/api/workers", json={
        "name": "To Delete",
        "tailscale_ip": "100.64.0.5",
    })
    worker_id = create_resp.json()["id"]
    response = await client.delete(f"/api/workers/{worker_id}")
    assert response.status_code == 204

    get_resp = await client.get(f"/api/workers/{worker_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_worker_with_instance_conflicts(client):
    """DELETE /api/workers/:id should return 409 while instances are assigned,
    and succeed once they are removed."""
    worker_resp = await client.post("/api/workers", json={
        "name": "Worker Conflict",
        "tailscale_ip": "100.64.0.99",
    })
    worker_id = worker_resp.json()["id"]

    acct_resp = await client.post("/api/accounts", json={
        "name": "Test Acct",
        "broker_type": "alpaca",
        "credentials": {"api_key": "k"},
        "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    account_id = acct_resp.json()["id"]

    algo_resp = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/blocker",
        "name": "blocker",
    })
    algo_id = algo_resp.json()["id"]

    inst_resp = await client.post(
        f"/api/algorithms/{algo_id}/instances",
        json={"account_id": account_id, "worker_id": worker_id},
    )
    instance_id = inst_resp.json()["id"]

    # Delete should now be rejected with 409.
    conflict = await client.delete(f"/api/workers/{worker_id}")
    assert conflict.status_code == 409
    assert "still" in conflict.json()["detail"].lower()

    # After deleting the instance, the worker can be removed.
    inst_del = await client.delete(f"/api/instances/{instance_id}")
    assert inst_del.status_code == 204

    ok = await client.delete(f"/api/workers/{worker_id}")
    assert ok.status_code == 204
