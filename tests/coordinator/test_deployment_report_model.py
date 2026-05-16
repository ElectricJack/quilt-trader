import pytest
from sqlalchemy import select
from coordinator.database.models import (
    AlgorithmDeploymentReport, AlgorithmInstance, Algorithm, Account, Worker,
)


@pytest.mark.asyncio
async def test_deployment_report_round_trip(db_session):
    algo = Algorithm(repo_url="x", name="A")
    acct = Account(name="A", broker_type="alpaca", credentials="{}", supported_asset_types=["equities"])
    w = Worker(name="W", status="online")
    db_session.add_all([algo, acct, w])
    await db_session.flush()
    inst = AlgorithmInstance(
        algorithm_id=algo.id, account_id=acct.id, worker_id=w.id, status="stopped",
    )
    db_session.add(inst)
    await db_session.flush()

    db_session.add(AlgorithmDeploymentReport(
        deployment_id=inst.id,
        total_return=0.05,
        sharpe_ratio=1.2,
        equity_curve=[{"timestamp": "2026-05-16T12:00:00Z", "portfolio_value": 100.0}],
        runs_index=[{"run_id": "r1", "run_number": 1, "status": "running"}],
    ))
    await db_session.commit()

    got = (await db_session.execute(
        select(AlgorithmDeploymentReport)
        .where(AlgorithmDeploymentReport.deployment_id == inst.id)
    )).scalar_one()
    assert got.total_return == 0.05
    assert got.sharpe_ratio == 1.2
    assert got.equity_curve[0]["portfolio_value"] == 100.0
    assert got.runs_index[0]["run_id"] == "r1"
