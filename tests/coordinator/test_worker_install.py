"""Tests for the worker provisioning flow."""
import tarfile
from io import BytesIO

import pytest


@pytest.mark.asyncio
async def test_create_worker_returns_install_token(client):
    resp = await client.post("/api/workers", json={
        "name": "pi-1",
        "tailscale_ip": "100.64.0.10",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["install_status"] == "pending"
    assert body["install_token"]
    assert len(body["install_token"]) >= 32  # url-safe 32-byte token


@pytest.mark.asyncio
async def test_install_command_renders_one_liner(client):
    create = await client.post("/api/workers", json={"name": "pi-2", "tailscale_ip": "100.64.0.11"})
    wid = create.json()["id"]
    token = create.json()["install_token"]
    resp = await client.get(f"/api/workers/{wid}/install-command")
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "curl -fsSL" in body
    assert "install-worker.sh" in body
    assert f"WORKER_ID='{wid}'" in body
    assert "WORKER_NAME='pi-2'" in body
    assert f"WORKER_TOKEN='{token}'" in body
    assert "TAILSCALE_AUTHKEY=" in body
    assert "sudo -E bash" in body


@pytest.mark.asyncio
async def test_install_command_requires_token(client):
    create = await client.post("/api/workers", json={"name": "pi-3", "tailscale_ip": "100.64.0.12"})
    wid = create.json()["id"]
    # Burn the token via claim.
    token = create.json()["install_token"]
    claim = await client.post(f"/api/workers/install/claim/{wid}?token={token}")
    assert claim.status_code == 200

    resp = await client.get(f"/api/workers/{wid}/install-command")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_package_tarball_is_valid_and_contains_worker(client):
    create = await client.post("/api/workers", json={"name": "pi-4", "tailscale_ip": "100.64.0.13"})
    token = create.json()["install_token"]
    resp = await client.get(f"/api/workers/install/package.tar.gz?token={token}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/gzip")

    # Verify it's a valid tarball containing the worker module.
    buf = BytesIO(resp.content)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        names = tar.getnames()
    assert any(n.endswith("worker/main.py") for n in names)
    assert any(n.endswith("worker/broker_adapter.py") for n in names)
    assert any(n.endswith("pyproject.toml") for n in names)
    # __pycache__ should be filtered out.
    assert not any("__pycache__" in n for n in names)


@pytest.mark.asyncio
async def test_package_rejects_invalid_token(client):
    resp = await client.get("/api/workers/install/package.tar.gz?token=not-a-real-token")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_claim_invalidates_token(client):
    create = await client.post("/api/workers", json={"name": "pi-5", "tailscale_ip": "100.64.0.14"})
    wid = create.json()["id"]
    token = create.json()["install_token"]

    resp = await client.post(f"/api/workers/install/claim/{wid}?token={token}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["already_claimed"] is False

    # Token should now be invalid for package downloads.
    pkg = await client.get(f"/api/workers/install/package.tar.gz?token={token}")
    assert pkg.status_code == 401

    # Re-claim with the same token should fail (token cleared from worker row).
    again = await client.post(f"/api/workers/install/claim/{wid}?token={token}")
    assert again.status_code == 401

    # Worker row reflects claimed status.
    get = await client.get(f"/api/workers/{wid}")
    assert get.json()["install_status"] == "claimed"
    assert get.json()["install_token"] is None


@pytest.mark.asyncio
async def test_regenerate_token_resets_install_state(client):
    create = await client.post("/api/workers", json={"name": "pi-6", "tailscale_ip": "100.64.0.15"})
    wid = create.json()["id"]
    original_token = create.json()["install_token"]
    # Claim first.
    await client.post(f"/api/workers/install/claim/{wid}?token={original_token}")

    # Regenerate.
    resp = await client.post(f"/api/workers/{wid}/regenerate-token")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["install_status"] == "pending"
    assert body["install_token"]
    assert body["install_token"] != original_token


@pytest.mark.asyncio
async def test_claim_404_unknown_worker(client):
    resp = await client.post("/api/workers/install/claim/no-such-id?token=anything")
    assert resp.status_code == 404
