import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from sqlalchemy import select


@pytest.mark.asyncio
async def test_finalizer_writes_report_for_running_deployment(test_app, db_session, tmp_path):
    from coordinator.api.dependencies import get_container
    from coordinator.services.live_sample_sink import LiveSampleSink
    from coordinator.services.live_finalizer import LiveFinalizer
    from coordinator.database.models import (
        Algorithm, Account, Worker, AlgorithmInstance, AlgorithmRun,
        AlgorithmDeploymentReport,
    )

    container = get_container()
    container.live_sample_sink = LiveSampleSink(
        base_dir=tmp_path, buffer_size=1, flush_interval_seconds=60,
    )

    algo = Algorithm(repo_url="x", name="A")
    acct = Account(name="A", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
    w = Worker(name="W", status="online")
    db_session.add_all([algo, acct, w])
    await db_session.flush()
    inst = AlgorithmInstance(
        algorithm_id=algo.id, account_id=acct.id, worker_id=w.id, status="running",
    )
    db_session.add(inst)
    await db_session.flush()
    run = AlgorithmRun(
        instance_id=inst.id, run_number=1, status="running",
        started_at=datetime.now(timezone.utc) - timedelta(days=10),
    )
    db_session.add(run)
    inst.active_run_id = run.id
    await db_session.commit()
    did, rid = inst.id, run.id

    t0 = datetime.now(timezone.utc) - timedelta(days=10)
    for i in range(10):
        await container.live_sample_sink.add_equity_sample(did, rid, {
            "timestamp": (t0 + timedelta(days=i)).isoformat(),
            "portfolio_value": 100.0 * (1 + 0.01 * i),
            "cash": 50.0,
        })
    await container.live_sample_sink.flush()

    fin = LiveFinalizer(
        session_factory=container.session_factory,
        sink=container.live_sample_sink,
        base_dir=tmp_path,
    )
    await fin.finalize_one(did)

    async with container.session_factory() as session:
        rep = (await session.execute(
            select(AlgorithmDeploymentReport).where(
                AlgorithmDeploymentReport.deployment_id == did
            )
        )).scalar_one()
        assert rep.equity_curve is not None
        assert len(rep.equity_curve) >= 1
        assert rep.runs_index and rep.runs_index[0]["run_id"] == rid
        assert rep.total_return is not None
