"""HTTP-layer tests for the 202-Accepted research job endpoints (B6, I18)."""
from unittest.mock import AsyncMock, MagicMock
import pytest
from httpx import ASGITransport, AsyncClient

from sqlalchemy import select

from coordinator.database.models import OptimizationSession, ResearchJob


async def _seed_algorithm(session_factory, *, id="test-algo-fixture") -> str:
    from coordinator.database.models import Algorithm
    async with session_factory() as s:
        # Use merge so repeated calls within the same DB don't violate PK uniqueness.
        s.add(Algorithm(id=id, name=id, repo_url=f"https://github.com/test/{id}"))
        try:
            await s.commit()
        except Exception:
            await s.rollback()
    return id


async def _seed_session(container, *, parameter_space: str = '{"vol_target":[0.1,0.15]}') -> int:
    """Insert an OptimizationSession; return its id."""
    algo_id = await _seed_algorithm(container.session_factory)
    async with container.session_factory() as s:
        sess = OptimizationSession(
            name="t", hypothesis="h",
            parameter_space=parameter_space,
            pre_registered_criteria="{}",
            algorithm_id=algo_id, base_config={},
        )
        s.add(sess)
        await s.commit()
        return sess.id


@pytest.mark.asyncio
async def test_post_sweep_returns_202_with_job_id(test_app, monkeypatch):
    """POST /sweep returns 202 + queued JobResponse; ResearchJobManager.create_sweep_job called."""
    from coordinator.api.dependencies import get_container

    container = get_container()
    session_id = await _seed_session(container)

    # Stub the manager so the real sweep doesn't fire.
    mgr = MagicMock()
    mgr.create_sweep_job = AsyncMock(return_value="job-123")
    mgr.get_job = AsyncMock(return_value={
        "job_id": "job-123", "session_id": session_id, "kind": "sweep",
        "status": "queued", "progress_pct": 0.0, "progress_message": None,
        "run_ids": [], "error_message": None,
        "started_at": None, "completed_at": None, "created_at": None,
    })
    monkeypatch.setattr(container, "research_job_manager", mgr)

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        r = await ac.post(
            f"/api/research/sessions/{session_id}/sweep",
            json={"manifest_path": "x.yaml", "base_config": {}, "search": "grid"},
        )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job_id"] == "job-123"
    assert body["status"] == "queued"
    assert body["kind"] == "sweep"
    mgr.create_sweep_job.assert_called_once()
    # Verify the fallback parameter_space (from session) was forwarded:
    payload = mgr.create_sweep_job.call_args.kwargs["request_payload"]
    assert payload["parameter_space"] == {"vol_target": [0.1, 0.15]}


@pytest.mark.asyncio
async def test_post_walk_forward_returns_202(test_app, monkeypatch):
    """POST /walk-forward returns 202 + queued JobResponse."""
    from coordinator.api.dependencies import get_container

    container = get_container()
    session_id = await _seed_session(container)

    mgr = MagicMock()
    mgr.create_walk_forward_job = AsyncMock(return_value="wf-9")
    mgr.get_job = AsyncMock(return_value={
        "job_id": "wf-9", "session_id": session_id, "kind": "walk-forward",
        "status": "queued", "progress_pct": 0.0, "progress_message": None,
        "run_ids": [], "error_message": None,
        "started_at": None, "completed_at": None, "created_at": None,
    })
    monkeypatch.setattr(container, "research_job_manager", mgr)

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        r = await ac.post(
            f"/api/research/sessions/{session_id}/walk-forward",
            json={"manifest_path": "x.yaml", "base_config": {}, "train_years": 4.0,
                  "test_years": 1.0, "step_months": 6.0, "objective": "sharpe"},
        )
    assert r.status_code == 202, r.text
    assert r.json()["job_id"] == "wf-9"


@pytest.mark.asyncio
async def test_post_sweep_unknown_session_returns_404(test_app, monkeypatch):
    """ResearchJobManager raises ValueError for unknown session; endpoint returns 404."""
    from coordinator.api.dependencies import get_container

    container = get_container()
    mgr = MagicMock()
    mgr.create_sweep_job = AsyncMock(side_effect=ValueError("session 99 not found"))
    monkeypatch.setattr(container, "research_job_manager", mgr)

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        r = await ac.post(
            "/api/research/sessions/99/sweep",
            json={"manifest_path": "x.yaml", "base_config": {}},
        )
    # NOTE: session lookup happens BEFORE the manager call (to resolve
    # parameter_space fallback), so the 404 comes from the existence check,
    # not the ValueError. Either path produces 404.
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_job_returns_current_state(test_app):
    """GET /jobs/{id} reflects the current DB row."""
    from coordinator.api.dependencies import get_container

    container = get_container()
    session_id = await _seed_session(container)
    async with container.session_factory() as s:
        s.add(ResearchJob(id="j", session_id=session_id, kind="sweep",
                          status="running", progress_pct=0.5,
                          progress_message="Trial 1 of 2",
                          request_payload={}, run_ids=["r1"]))
        await s.commit()

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        r = await ac.get(f"/api/research/sessions/{session_id}/jobs/j")
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == "j"
    assert body["status"] == "running"
    assert body["progress_pct"] == 0.5
    assert body["run_ids"] == ["r1"]


@pytest.mark.asyncio
async def test_list_jobs_returns_all_for_session(test_app):
    """GET /jobs returns all jobs for the session, newest first."""
    from coordinator.api.dependencies import get_container

    container = get_container()
    session_id = await _seed_session(container)
    async with container.session_factory() as s:
        for i in range(3):
            s.add(ResearchJob(id=f"j-{i}", session_id=session_id, kind="sweep",
                              status="queued", request_payload={}, run_ids=[]))
        await s.commit()

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        r = await ac.get(f"/api/research/sessions/{session_id}/jobs")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 3
    ids = sorted(j["job_id"] for j in body)
    assert ids == ["j-0", "j-1", "j-2"]


@pytest.mark.asyncio
async def test_delete_job_cancels(test_app):
    """DELETE /jobs/{id} sets status to cancelled."""
    from coordinator.api.dependencies import get_container

    container = get_container()
    session_id = await _seed_session(container)
    async with container.session_factory() as s:
        s.add(ResearchJob(id="j2", session_id=session_id, kind="sweep",
                          status="running", request_payload={}, run_ids=[]))
        await s.commit()

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        r = await ac.delete(f"/api/research/sessions/{session_id}/jobs/j2")
    assert r.status_code == 200

    async with container.session_factory() as s:
        row = (await s.execute(select(ResearchJob).where(ResearchJob.id == "j2"))).scalar_one()
        assert row.status == "cancelled"


@pytest.mark.asyncio
async def test_get_job_404_for_unknown_id(test_app):
    """Unknown job id returns 404."""
    from coordinator.api.dependencies import get_container

    container = get_container()
    session_id = await _seed_session(container)

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        r = await ac.get(f"/api/research/sessions/{session_id}/jobs/missing")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_job_404_when_session_mismatch(test_app):
    """A job belonging to a different session returns 404 (not 200 with leaked row)."""
    from coordinator.api.dependencies import get_container

    container = get_container()
    s1 = await _seed_session(container)
    async with container.session_factory() as s:
        sess2 = OptimizationSession(name="t2", hypothesis="h",
                                    parameter_space="{}", pre_registered_criteria="{}",
                                    algorithm_id="test-algo-fixture", base_config={})
        s.add(sess2)
        await s.flush()
        s2 = sess2.id
        s.add(ResearchJob(id="x", session_id=s2, kind="sweep",
                          status="queued", request_payload={}, run_ids=[]))
        await s.commit()

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        r = await ac.get(f"/api/research/sessions/{s1}/jobs/x")
    assert r.status_code == 404
