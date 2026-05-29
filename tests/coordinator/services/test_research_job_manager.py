"""Unit tests for ResearchJobManager — DB-level state-machine semantics."""
import asyncio
from unittest.mock import AsyncMock
import pytest

from sqlalchemy import select

from coordinator.database.models import ResearchJob, OptimizationSession


async def _seed_session(container) -> int:
    """Insert an OptimizationSession; return its id."""
    async with container.session_factory() as s:
        sess = OptimizationSession(
            name="t", hypothesis="h",
            parameter_space="{}", pre_registered_criteria="{}",
        )
        s.add(sess)
        await s.commit()
        return sess.id


@pytest.mark.asyncio
async def test_create_sweep_job_inserts_queued_row(test_app):
    """create_sweep_job inserts a queued row and returns its id."""
    from coordinator.api.dependencies import get_container
    from coordinator.services.research_job_manager import ResearchJobManager

    container = get_container()
    session_id = await _seed_session(container)

    mgr = ResearchJobManager(
        session_factory=container.session_factory,
        sweep_fn=AsyncMock(),
        walk_forward_fn=AsyncMock(),
        runner_factory=AsyncMock(),
    )
    job_id = await mgr.create_sweep_job(
        session_id=session_id,
        request_payload={"manifest_path": "x", "base_config": {}, "search": "grid"},
    )
    # Cancel immediately so the background task (which calls sweep_fn with no
    # sync_session_factory) doesn't blow up the test.
    await mgr.cancel_job(job_id)

    async with container.session_factory() as s:
        row = (await s.execute(select(ResearchJob).where(ResearchJob.id == job_id))).scalar_one()
        assert row.kind == "sweep"
        assert row.session_id == session_id
        assert row.request_payload["manifest_path"] == "x"


@pytest.mark.asyncio
async def test_get_job_returns_dict_or_none(test_app):
    """get_job returns None for an unknown job id."""
    from coordinator.api.dependencies import get_container
    from coordinator.services.research_job_manager import ResearchJobManager

    mgr = ResearchJobManager(
        session_factory=get_container().session_factory,
        sweep_fn=AsyncMock(), walk_forward_fn=AsyncMock(),
        runner_factory=AsyncMock(),
    )
    assert await mgr.get_job("missing") is None


@pytest.mark.asyncio
async def test_recover_orphaned_jobs_marks_queued_and_running_failed(test_app):
    """At startup, any job left in queued/running is marked failed; completed
    rows are untouched."""
    from coordinator.api.dependencies import get_container
    from coordinator.services.research_job_manager import ResearchJobManager

    container = get_container()
    session_id = await _seed_session(container)

    async with container.session_factory() as s:
        s.add(ResearchJob(id="a", session_id=session_id, kind="sweep",
                          status="queued", request_payload={}, run_ids=[]))
        s.add(ResearchJob(id="b", session_id=session_id, kind="walk-forward",
                          status="running", request_payload={}, run_ids=[]))
        s.add(ResearchJob(id="c", session_id=session_id, kind="sweep",
                          status="completed", request_payload={}, run_ids=[]))
        await s.commit()

    mgr = ResearchJobManager(
        session_factory=container.session_factory,
        sweep_fn=AsyncMock(), walk_forward_fn=AsyncMock(),
        runner_factory=AsyncMock(),
    )
    count = await mgr.recover_orphaned_jobs()
    assert count == 2

    async with container.session_factory() as s:
        rows = {r.id: r for r in (await s.execute(select(ResearchJob))).scalars().all()}
        assert rows["a"].status == "failed"
        assert rows["b"].status == "failed"
        assert rows["c"].status == "completed"
        assert "orphan" in (rows["a"].error_message or "").lower()


@pytest.mark.asyncio
async def test_cancel_job_flips_status_to_cancelled(test_app):
    """cancel_job on a running row flips it to cancelled."""
    from coordinator.api.dependencies import get_container
    from coordinator.services.research_job_manager import ResearchJobManager

    container = get_container()
    session_id = await _seed_session(container)

    async with container.session_factory() as s:
        s.add(ResearchJob(id="job-x", session_id=session_id, kind="sweep",
                          status="running", request_payload={}, run_ids=[]))
        await s.commit()

    mgr = ResearchJobManager(
        session_factory=container.session_factory,
        sweep_fn=AsyncMock(), walk_forward_fn=AsyncMock(),
        runner_factory=AsyncMock(),
    )
    ok = await mgr.cancel_job("job-x")
    assert ok is True

    async with container.session_factory() as s:
        row = (await s.execute(select(ResearchJob).where(ResearchJob.id == "job-x"))).scalar_one()
        assert row.status == "cancelled"
