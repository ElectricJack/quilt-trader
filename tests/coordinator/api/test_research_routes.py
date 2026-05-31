"""Integration tests for /api/research/* endpoints."""
import json
import uuid
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from coordinator.database.models import Algorithm, OptimizationSession


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


# ---------------------------------------------------------------------------
# Fixtures for algorithm_id / manifest_path resolution tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def test_client(test_app):
    """Async HTTP client for the test app."""
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def db_session_factory(test_app):
    """Return the app container's async session factory so test writes share the same DB."""
    from coordinator.api.dependencies import get_container
    return get_container().session_factory


@pytest_asyncio.fixture
async def seeded_session(db_session_factory):
    """Insert an OptimizationSession and yield it."""
    async with db_session_factory() as s:
        sess = OptimizationSession(
            name=f"test-sess-{uuid.uuid4().hex[:8]}",
            hypothesis="test hypothesis",
            parameter_space='{"k":[1,2]}',
            pre_registered_criteria="{}",
        )
        s.add(sess)
        await s.commit()
        await s.refresh(sess)
        yield sess


@pytest_asyncio.fixture
async def seeded_algorithm(db_session_factory):
    """Insert an Algorithm with a non-null source_path and yield it."""
    async with db_session_factory() as s:
        algo = Algorithm(
            id=f"algo-{uuid.uuid4().hex[:8]}",
            repo_url="https://example.com/repo",
            name="test-algo",
            source_path="/tmp/algo-x",
        )
        s.add(algo)
        await s.commit()
        await s.refresh(algo)
        yield algo


# ---------------------------------------------------------------------------
# algorithm_id / manifest_path tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_accepts_algorithm_id_and_resolves_manifest(
    test_client, seeded_session, seeded_algorithm, db_session_factory,
):
    resp = await test_client.post(
        f"/api/research/sessions/{seeded_session.id}/sweep",
        json={
            "algorithm_id": seeded_algorithm.id,
            "base_config": {},
            "search": "grid",
            "max_trials": 5,
        },
    )
    assert resp.status_code in (200, 202), resp.text
    from sqlalchemy import select
    from coordinator.database.models import ResearchJob
    async with db_session_factory() as s:
        rows = (await s.execute(select(ResearchJob))).scalars().all()
        jobs = [r for r in rows if r.kind == "sweep"]
        assert len(jobs) >= 1
        latest = jobs[-1]
        payload = latest.request_payload
        assert payload["manifest_path"] == f"{seeded_algorithm.source_path}/quilt.yaml"
        assert "algorithm_id" not in payload


@pytest.mark.asyncio
async def test_sweep_rejects_both_manifest_and_algorithm_id(test_client, seeded_session):
    resp = await test_client.post(
        f"/api/research/sessions/{seeded_session.id}/sweep",
        json={
            "manifest_path": "/some/path/quilt.yaml",
            "algorithm_id": "abc123",
            "base_config": {},
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_sweep_rejects_neither_manifest_nor_algorithm_id(test_client, seeded_session):
    resp = await test_client.post(
        f"/api/research/sessions/{seeded_session.id}/sweep",
        json={"base_config": {}},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_sweep_rejects_unknown_algorithm_id(test_client, seeded_session):
    resp = await test_client.post(
        f"/api/research/sessions/{seeded_session.id}/sweep",
        json={"algorithm_id": "no-such-algorithm", "base_config": {}},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_walk_forward_accepts_algorithm_id(
    test_client, seeded_session, seeded_algorithm, db_session_factory,
):
    resp = await test_client.post(
        f"/api/research/sessions/{seeded_session.id}/walk-forward",
        json={"algorithm_id": seeded_algorithm.id, "base_config": {}},
    )
    assert resp.status_code in (200, 202), resp.text
    from sqlalchemy import select
    from coordinator.database.models import ResearchJob
    async with db_session_factory() as s:
        rows = (await s.execute(select(ResearchJob))).scalars().all()
        wf = [r for r in rows if r.kind == "walk-forward"]
        assert len(wf) >= 1
        payload = wf[-1].request_payload
        assert payload["manifest_path"] == f"{seeded_algorithm.source_path}/quilt.yaml"
