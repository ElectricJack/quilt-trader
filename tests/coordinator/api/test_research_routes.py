"""Integration tests for /api/research/* endpoints."""
import json
import uuid
from datetime import date
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from coordinator.database.models import Algorithm, OptimizationSession


@pytest.mark.asyncio
async def test_create_session_endpoint(test_app, seeded_algorithm):
    """POST /api/research/sessions creates a session and returns it."""
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        resp = await client.post(
            "/api/research/sessions",
            json={
                "name": "ep-test-001",
                "hypothesis": "endpoint test hypothesis",
                "algorithm_id": seeded_algorithm.id,
                "base_config": {},
                "parameter_space": {"vol_target": [0.10, 0.15]},
                "pre_registered_criteria": {"oos_sharpe_lci": 0.5},
                "date_range_start": "2023-01-01",
                "date_range_end": "2024-12-31",
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "ep-test-001"
    assert body["status"] == "open"
    assert body["n_runs"] == 0


@pytest.mark.asyncio
async def test_list_and_get_sessions(test_app, seeded_algorithm):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        # Create one
        await client.post(
            "/api/research/sessions",
            json={
                "name": "ep-test-list-001",
                "hypothesis": "h",
                "algorithm_id": seeded_algorithm.id,
                "base_config": {},
                "parameter_space": {},
                "pre_registered_criteria": {},
                "date_range_start": "2023-01-01",
                "date_range_end": "2024-12-31",
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
async def test_create_session_duplicate_name_rejected(test_app, seeded_algorithm):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        payload = {
            "name": "ep-test-dup-001",
            "hypothesis": "h",
            "algorithm_id": seeded_algorithm.id,
            "base_config": {},
            "parameter_space": {},
            "pre_registered_criteria": {},
            "date_range_start": "2023-01-01",
            "date_range_end": "2024-12-31",
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
async def seeded_session(db_session_factory, seeded_algorithm):
    """Insert an OptimizationSession and yield it."""
    async with db_session_factory() as s:
        sess = OptimizationSession(
            name=f"test-sess-{uuid.uuid4().hex[:8]}",
            hypothesis="test hypothesis",
            algorithm_id=seeded_algorithm.id,
            base_config={"vol_target": 0.10},
            parameter_space=json.dumps({"lookback": [20, 50]}),
            pre_registered_criteria=json.dumps({"min_sharpe": 0.0}),
            status="open",
            date_range_start=date(2023, 1, 1),
            date_range_end=date(2024, 12, 31),
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
# Task 4 — sweep payload shrinks to execution-only fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_request_rejects_legacy_fields(test_client, seeded_session):
    """Sweep no longer accepts manifest_path / algorithm_id / base_config /
    parameter_space — they live on the session now."""
    resp = await test_client.post(
        f"/api/research/sessions/{seeded_session.id}/sweep",
        json={"manifest_path": "/x/quilt.yaml", "search": "grid"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_sweep_uses_session_algorithm_and_base_config(
    test_client, seeded_session, db_session_factory,
):
    """Sweep payload omits algorithm/base_config; ResearchJob.request_payload
    must contain manifest_path resolved from session.algorithm_id and
    base_config copied from session.base_config."""
    resp = await test_client.post(
        f"/api/research/sessions/{seeded_session.id}/sweep",
        json={"search": "grid", "max_trials": 5},
    )
    assert resp.status_code == 202, resp.text
    from sqlalchemy import select
    from coordinator.database.models import ResearchJob, OptimizationSession
    async with db_session_factory() as s:
        sess = await s.get(OptimizationSession, seeded_session.id)
        jobs = (await s.execute(
            select(ResearchJob)
            .where(ResearchJob.session_id == seeded_session.id)
            .where(ResearchJob.kind == "sweep")
        )).scalars().all()
        assert len(jobs) >= 1
        latest = jobs[-1]
        assert "manifest_path" in latest.request_payload
        assert latest.request_payload["manifest_path"].endswith("/quilt.yaml")
        assert latest.request_payload["base_config"] == sess.base_config
        import json
        assert latest.request_payload["parameter_space"] == json.loads(sess.parameter_space)


@pytest.mark.asyncio
async def test_sweep_returns_404_for_unknown_session(test_client):
    resp = await test_client.post(
        "/api/research/sessions/99999/sweep",
        json={"search": "grid", "max_trials": 5},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_walk_forward_request_rejects_legacy_fields(
    test_client, seeded_session,
):
    resp = await test_client.post(
        f"/api/research/sessions/{seeded_session.id}/walk-forward",
        json={"manifest_path": "/x/quilt.yaml"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_walk_forward_uses_session_algorithm_and_base_config(
    test_client, seeded_session, db_session_factory,
):
    resp = await test_client.post(
        f"/api/research/sessions/{seeded_session.id}/walk-forward",
        json={"train_years": 4.0, "test_years": 1.0,
              "step_months": 6.0, "objective": "sharpe"},
    )
    assert resp.status_code == 202, resp.text
    from sqlalchemy import select
    from coordinator.database.models import ResearchJob, OptimizationSession
    async with db_session_factory() as s:
        sess = await s.get(OptimizationSession, seeded_session.id)
        jobs = (await s.execute(
            select(ResearchJob)
            .where(ResearchJob.session_id == seeded_session.id)
            .where(ResearchJob.kind == "walk-forward")
        )).scalars().all()
        assert len(jobs) >= 1
        latest = jobs[-1]
        assert latest.request_payload["manifest_path"].endswith("/quilt.yaml")
        assert latest.request_payload["base_config"] == sess.base_config
        import json
        assert latest.request_payload["parameter_space"] == json.loads(sess.parameter_space)


@pytest.mark.asyncio
async def test_walk_forward_returns_404_for_unknown_session(test_client):
    resp = await test_client.post(
        "/api/research/sessions/99999/walk-forward",
        json={},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Task 3 — algorithm_id + base_config binding on session create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_requires_algorithm_id(test_client, seeded_algorithm):
    """Omitting algorithm_id from the body returns 422."""
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-no-algo",
        "hypothesis": "h",
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_session_rejects_unknown_algorithm_id(test_client):
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-unknown-algo",
        "hypothesis": "h",
        "algorithm_id": "no-such-algorithm",
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_session_rejects_algorithm_with_null_source_path(
    test_client, db_session_factory,
):
    """An algorithm row without source_path can't be the subject of an
    experiment — sweeps can't resolve a manifest from it."""
    from coordinator.database.models import Algorithm
    async with db_session_factory() as s:
        s.add(Algorithm(
            id="orphan-algo",
            name="Orphan",
            repo_url="https://github.com/test/orphan-algo",
            source_path=None,
            install_status="failed",
        ))
        await s.commit()
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-orphan",
        "hypothesis": "h",
        "algorithm_id": "orphan-algo",
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_session_accepts_empty_base_config(test_client, seeded_algorithm):
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-empty-base",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["base_config"] == {}


@pytest.mark.asyncio
async def test_session_response_includes_algorithm_id_and_base_config(
    test_client, seeded_algorithm,
):
    create_resp = await test_client.post("/api/research/sessions", json={
        "name": "t-roundtrip",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {"vol": 0.10, "k": "v"},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
    })
    assert create_resp.status_code == 200
    sid = create_resp.json()["id"]
    get_resp = await test_client.get(f"/api/research/sessions/{sid}")
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["algorithm_id"] == seeded_algorithm.id
    assert body["base_config"] == {"vol": 0.10, "k": "v"}


# ---------------------------------------------------------------------------
# Task 3 (scope) — CreateSessionRequest + SessionResponse new fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_requires_date_range_start(test_client, seeded_algorithm):
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-no-start",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_end": "2024-12-31",
        # date_range_start omitted
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_session_requires_date_range_end(test_client, seeded_algorithm):
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-no-end",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2023-01-01",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_session_rejects_end_before_start(test_client, seeded_algorithm):
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-bad-range",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2024-12-31",
        "date_range_end": "2023-01-01",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_session_rejects_unpaired_benchmark(test_client, seeded_algorithm):
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-unpaired",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
        "benchmark_symbol": "SPY",
        # benchmark_source omitted
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_session_accepts_default_initial_cash_and_cost_profile(
    test_client, seeded_algorithm,
):
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-defaults",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
        # initial_cash + cost_profile omitted — server applies defaults
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["initial_cash"] == 10000.0
    assert body["cost_profile"] == "default"


@pytest.mark.asyncio
async def test_session_response_includes_all_six_new_fields(
    test_client, seeded_algorithm,
):
    create_resp = await test_client.post("/api/research/sessions", json={
        "name": "t-roundtrip-scope",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
        "initial_cash": 25000.0,
        "cost_profile": "paid_tier",
        "benchmark_symbol": "QQQ",
        "benchmark_source": "yfinance",
    })
    assert create_resp.status_code == 200
    sid = create_resp.json()["id"]
    get_resp = await test_client.get(f"/api/research/sessions/{sid}")
    body = get_resp.json()
    assert body["date_range_start"] == "2023-01-01"
    assert body["date_range_end"] == "2024-12-31"
    assert body["initial_cash"] == 25000.0
    assert body["cost_profile"] == "paid_tier"
    assert body["benchmark_symbol"] == "QQQ"
    assert body["benchmark_source"] == "yfinance"


@pytest.mark.asyncio
async def test_create_session_defaults_mtm_realism_to_zero(
    test_client, seeded_algorithm,
):
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-mtm-default",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
        # mtm_realism omitted → server defaults to 0.0
    })
    assert resp.status_code == 200
    assert resp.json()["mtm_realism"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_create_session_accepts_explicit_mtm_realism(
    test_client, seeded_algorithm,
):
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-mtm-explicit",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
        "mtm_realism": 0.5,
    })
    assert resp.status_code == 200
    sid = resp.json()["id"]
    # Round-trip via GET
    get_resp = await test_client.get(f"/api/research/sessions/{sid}")
    assert get_resp.json()["mtm_realism"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_create_session_rejects_mtm_realism_above_one(
    test_client, seeded_algorithm,
):
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-mtm-bad-high",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
        "mtm_realism": 1.5,
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_session_rejects_mtm_realism_below_zero(
    test_client, seeded_algorithm,
):
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-mtm-bad-low",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
        "mtm_realism": -0.1,
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_session_accepts_endpoints_0_and_1(
    test_client, seeded_algorithm,
):
    for value in (0.0, 1.0):
        resp = await test_client.post("/api/research/sessions", json={
            "name": f"t-mtm-{value}",
            "hypothesis": "h",
            "algorithm_id": seeded_algorithm.id,
            "base_config": {},
            "parameter_space": {"x": [1]},
            "pre_registered_criteria": {"min_sharpe": 0.0},
            "date_range_start": "2023-01-01",
            "date_range_end": "2024-12-31",
            "mtm_realism": value,
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["mtm_realism"] == pytest.approx(value)
