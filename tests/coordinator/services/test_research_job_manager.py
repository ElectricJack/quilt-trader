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


# ---------------------------------------------------------------------------
# Helpers for on_job_update tests (use db_session_factory fixture directly)
# ---------------------------------------------------------------------------

async def _seed_session_sf(db_session_factory) -> int:
    from coordinator.database.models import OptimizationSession
    import uuid as _uuid
    async with db_session_factory() as s:
        row = OptimizationSession(
            name=f"smoke-{_uuid.uuid4().hex[:6]}",
            hypothesis="ws update smoke",
            parameter_space='{"x": [1]}',
            pre_registered_criteria='{"min_sharpe": 0.0}',
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
        return row.id


async def _seed_queued_job(db_session_factory, session_id: int, *, kind: str) -> str:
    import uuid as _uuid
    job_id = _uuid.uuid4().hex
    async with db_session_factory() as s:
        row = ResearchJob(
            id=job_id, session_id=session_id, kind=kind,
            status="queued", request_payload={}, run_ids=[],
        )
        s.add(row)
        await s.commit()
    return job_id


# ---------------------------------------------------------------------------
# on_job_update tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_job_update_called_when_marking_running(db_session_factory):
    from coordinator.services.research_job_manager import ResearchJobManager
    on_update = AsyncMock(return_value=None)
    mgr = ResearchJobManager(
        session_factory=db_session_factory,
        sweep_fn=AsyncMock(return_value=None),
        walk_forward_fn=AsyncMock(return_value=None),
        runner_factory=AsyncMock(return_value=None),
        sync_session_factory=None,
        on_job_update=on_update,
    )
    session_id = await _seed_session_sf(db_session_factory)
    job_id = await _seed_queued_job(db_session_factory, session_id, kind="sweep")
    await mgr._mark_running(job_id)
    on_update.assert_awaited()
    payload = on_update.call_args[0][0]
    assert payload["job_id"] == job_id
    assert payload["session_id"] == session_id
    assert payload["status"] == "running"


@pytest.mark.asyncio
async def test_on_job_update_called_on_terminal_status(db_session_factory):
    from coordinator.services.research_job_manager import ResearchJobManager
    on_update = AsyncMock(return_value=None)
    mgr = ResearchJobManager(
        session_factory=db_session_factory,
        sweep_fn=AsyncMock(), walk_forward_fn=AsyncMock(),
        runner_factory=AsyncMock(), sync_session_factory=None,
        on_job_update=on_update,
    )
    session_id = await _seed_session_sf(db_session_factory)
    job_id = await _seed_queued_job(db_session_factory, session_id, kind="sweep")
    await mgr._mark_terminal(job_id, "completed")
    on_update.assert_awaited()
    payload = on_update.call_args[0][0]
    assert payload["status"] == "completed"
    assert payload["completed_at"] is not None
    assert payload["progress_pct"] == 1.0


@pytest.mark.asyncio
async def test_on_job_update_called_from_progress_callback(db_session_factory):
    from coordinator.services.research_job_manager import ResearchJobManager
    on_update = AsyncMock(return_value=None)
    mgr = ResearchJobManager(
        session_factory=db_session_factory,
        sweep_fn=AsyncMock(), walk_forward_fn=AsyncMock(),
        runner_factory=AsyncMock(), sync_session_factory=None,
        on_job_update=on_update,
    )
    session_id = await _seed_session_sf(db_session_factory)
    job_id = await _seed_queued_job(db_session_factory, session_id, kind="sweep")
    cancel_flag = asyncio.Event()
    cb = mgr._make_progress_callback(job_id, cancel_flag)
    await cb(0.5, "trial 5 / 10", ["run-1", "run-2"])
    on_update.assert_awaited()
    payload = on_update.call_args[0][0]
    assert payload["progress_pct"] == 0.5
    assert payload["progress_message"] == "trial 5 / 10"
    assert "run-1" in payload["run_ids"]


@pytest.mark.asyncio
async def test_on_job_update_exception_is_logged_not_swallowed(db_session_factory, caplog):
    """If the broadcaster raises, the commit still succeeds and the error
    is logged."""
    from coordinator.services.research_job_manager import ResearchJobManager

    async def broken_on_update(payload):
        raise RuntimeError("ws broadcaster boom")

    mgr = ResearchJobManager(
        session_factory=db_session_factory,
        sweep_fn=AsyncMock(), walk_forward_fn=AsyncMock(),
        runner_factory=AsyncMock(), sync_session_factory=None,
        on_job_update=broken_on_update,
    )
    session_id = await _seed_session_sf(db_session_factory)
    job_id = await _seed_queued_job(db_session_factory, session_id, kind="sweep")
    await mgr._mark_running(job_id)  # must not raise
    assert "broadcaster" in caplog.text.lower() or "ws" in caplog.text.lower()
    async with db_session_factory() as s:
        row = await s.get(ResearchJob, job_id)
        assert row.status == "running"


@pytest.mark.asyncio
async def test_on_job_update_optional(db_session_factory):
    """Constructing without on_job_update still works (CLI path)."""
    from coordinator.services.research_job_manager import ResearchJobManager
    mgr = ResearchJobManager(
        session_factory=db_session_factory,
        sweep_fn=AsyncMock(), walk_forward_fn=AsyncMock(),
        runner_factory=AsyncMock(), sync_session_factory=None,
    )
    session_id = await _seed_session_sf(db_session_factory)
    job_id = await _seed_queued_job(db_session_factory, session_id, kind="sweep")
    await mgr._mark_running(job_id)  # must not raise


@pytest.mark.asyncio
async def test_progress_callback_run_ids_replace_not_append(db_session_factory):
    """Callers pass the cumulative run_ids list each tick. The callback must
    REPLACE the DB column, not append — otherwise the row's run_ids grows
    O(N^2) with duplicates.
    """
    from coordinator.services.research_job_manager import ResearchJobManager
    on_update = AsyncMock(return_value=None)
    mgr = ResearchJobManager(
        session_factory=db_session_factory,
        sweep_fn=AsyncMock(), walk_forward_fn=AsyncMock(),
        runner_factory=AsyncMock(), sync_session_factory=None,
        on_job_update=on_update,
    )
    session_id = await _seed_session_sf(db_session_factory)
    job_id = await _seed_queued_job(db_session_factory, session_id, kind="sweep")
    cancel_flag = asyncio.Event()
    cb = mgr._make_progress_callback(job_id, cancel_flag)

    # Simulate three sequential trials with cumulative run_id lists, as the
    # real sweep_fn does (run_ids.append(...) then pass list(run_ids)).
    await cb(0.33, "trial 1/3", ["r1"])
    await cb(0.66, "trial 2/3", ["r1", "r2"])
    await cb(1.00, "trial 3/3", ["r1", "r2", "r3"])

    from sqlalchemy import select
    async with db_session_factory() as s:
        row = await s.get(ResearchJob, job_id)
        assert row.run_ids == ["r1", "r2", "r3"], (
            f"Expected exactly 3 unique ids, got {row.run_ids} "
            f"(len={len(row.run_ids)}; quadratic accumulation bug?)"
        )
