"""Integration tests for /api/research/* endpoints."""
import json
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_create_session_endpoint(test_app):
    """POST /api/research/sessions creates a session and returns it."""
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        resp = await client.post(
            "/api/research/sessions",
            json={
                "name": "ep-test-001",
                "hypothesis": "endpoint test hypothesis",
                "parameter_space": {"vol_target": [0.10, 0.15]},
                "pre_registered_criteria": {"oos_sharpe_lci": 0.5},
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "ep-test-001"
    assert body["status"] == "open"
    assert body["n_runs"] == 0


@pytest.mark.asyncio
async def test_list_and_get_sessions(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        # Create one
        await client.post(
            "/api/research/sessions",
            json={
                "name": "ep-test-list-001",
                "hypothesis": "h",
                "parameter_space": {},
                "pre_registered_criteria": {},
            },
        )
        # List
        list_resp = await client.get("/api/research/sessions")
        assert list_resp.status_code == 200
        sessions = list_resp.json()
        assert any(s["name"] == "ep-test-list-001" for s in sessions)

        # Get by ID
        sess_id = next(s["id"] for s in sessions if s["name"] == "ep-test-list-001")
        get_resp = await client.get(f"/api/research/sessions/{sess_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["name"] == "ep-test-list-001"


@pytest.mark.asyncio
async def test_get_session_not_found(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        resp = await client.get("/api/research/sessions/999999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_session_duplicate_name_rejected(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        payload = {
            "name": "ep-test-dup-001",
            "hypothesis": "h",
            "parameter_space": {},
            "pre_registered_criteria": {},
        }
        r1 = await client.post("/api/research/sessions", json=payload)
        assert r1.status_code == 200
        r2 = await client.post("/api/research/sessions", json=payload)
        assert r2.status_code == 400
