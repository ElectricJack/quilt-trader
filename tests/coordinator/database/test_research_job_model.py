import pytest

from sqlalchemy import select


@pytest.mark.asyncio
async def test_research_job_round_trips(test_app):
    """Insert + read back a ResearchJob; verify JSON columns round-trip
    correctly and the FK relationship to OptimizationSession holds."""
    from coordinator.api.dependencies import get_container
    from coordinator.database.models import OptimizationSession, ResearchJob

    container = get_container()
    # Seed an Algorithm first (FK requirement).
    from coordinator.database.models import Algorithm
    async with container.session_factory() as s:
        s.add(Algorithm(id="test-algo-rjm", name="test-algo-rjm",
                        repo_url="https://github.com/test/test-algo-rjm"))
        await s.commit()
    # Insert an OptimizationSession + ResearchJob in one transaction.
    async with container.session_factory() as s:
        sess = OptimizationSession(
            name="t", hypothesis="h",
            parameter_space="{}", pre_registered_criteria="{}",
            algorithm_id="test-algo-rjm", base_config={},
        )
        s.add(sess)
        await s.flush()
        session_id = sess.id
        job = ResearchJob(
            id="job-1", session_id=session_id, kind="sweep",
            status="queued", progress_pct=0.0,
            request_payload={"manifest_path": "x", "base_config": {}},
            run_ids=[],
        )
        s.add(job)
        await s.commit()

    # Read it back in a fresh session.
    async with container.session_factory() as s:
        row = (await s.execute(
            select(ResearchJob).where(ResearchJob.id == "job-1")
        )).scalar_one()
        assert row.kind == "sweep"
        assert row.status == "queued"
        assert row.request_payload["manifest_path"] == "x"
        assert row.run_ids == []
        assert row.session_id == session_id
