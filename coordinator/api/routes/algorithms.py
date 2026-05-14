from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.database.models import (
    Account,
    Algorithm,
    AlgorithmInstance,
    AlgorithmRun,
    BacktestComparison,
    DecisionLog,
    PDTTracking,
    Position,
    TradeLog,
)

router = APIRouter(tags=["algorithms"])


class AlgorithmCreate(BaseModel):
    repo_url: str
    name: str
    description: Optional[str] = None
    version: Optional[str] = None
    commit_hash: Optional[str] = None
    required_asset_types: Optional[list[str]] = None
    required_options_level: Optional[int] = None
    required_account_features: Optional[list[str]] = None
    supported_brokers: Optional[list[str]] = None
    data_dependencies: Optional[list[dict]] = None
    config_schema: Optional[dict] = None
    custom_events: Optional[list[dict]] = None


class InstanceCreate(BaseModel):
    account_id: str
    worker_id: str
    config_values: Optional[dict] = None


def _algo_to_response(algo: Algorithm) -> dict:
    return {
        "id": algo.id,
        "repo_url": algo.repo_url,
        "name": algo.name,
        "description": algo.description,
        "version": algo.version,
        "commit_hash": algo.commit_hash,
        "required_asset_types": algo.required_asset_types,
        "required_options_level": algo.required_options_level,
        "required_account_features": algo.required_account_features,
        "supported_brokers": algo.supported_brokers,
        "data_dependencies": algo.data_dependencies,
        "config_schema": algo.config_schema,
        "custom_events": algo.custom_events,
        "install_status": algo.install_status,
        "install_error": algo.install_error,
        "installed_at": algo.installed_at.isoformat() if algo.installed_at else None,
        "updated_at": algo.updated_at.isoformat() if algo.updated_at else None,
    }


def _downsample(curve: list[dict], target: int = 20) -> list[float]:
    if not curve:
        return []
    points = [float(p.get("equity", 0.0)) for p in curve]
    if len(points) <= target:
        return points
    step = len(points) / target
    return [points[int(i * step)] for i in range(target)]


async def _enrich_instance(inst: AlgorithmInstance, db: AsyncSession) -> dict:
    # Resolve names
    algo = (await db.execute(
        select(Algorithm).where(Algorithm.id == inst.algorithm_id)
    )).scalar_one_or_none()
    acct = (await db.execute(
        select(Account).where(Account.id == inst.account_id)
    )).scalar_one_or_none()

    # Latest run's equity curve, downsampled
    run = (await db.execute(
        select(AlgorithmRun)
        .where(AlgorithmRun.instance_id == inst.id)
        .order_by(AlgorithmRun.run_number.desc())
        .limit(1)
    )).scalar_one_or_none()
    sparkline = _downsample(run.equity_curve or []) if run else None

    # Today's P&L from trade_log (realized only — unrealized delta is hard without per-tick snapshots)
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    trades_today = (await db.execute(
        select(TradeLog)
        .where(TradeLog.instance_id == inst.id)
        .where(TradeLog.timestamp >= today_start)
    )).scalars().all()
    today_pnl = sum(
        (t.filled_price or 0.0) * (t.quantity or 0.0) * (-1 if (t.side or "").lower() == "buy" else 1)
        for t in trades_today
    )

    return {
        "id": inst.id,
        "algorithm_id": inst.algorithm_id,
        "algorithm_name": algo.name if algo else None,
        "account_id": inst.account_id,
        "account_name": acct.name if acct else None,
        "worker_id": inst.worker_id,
        "status": inst.status,
        "active_run_id": inst.active_run_id,
        "config_values": inst.config_values,
        "persisted_state": inst.persisted_state,
        "state_stale": inst.state_stale,
        "lifetime_metrics": inst.lifetime_metrics,
        "today_pnl": today_pnl,
        "pnl_sparkline": sparkline,
        "created_at": inst.created_at.isoformat() if inst.created_at else None,
        "updated_at": inst.updated_at.isoformat() if inst.updated_at else None,
    }


@router.post("/api/algorithms", status_code=201)
async def create_algorithm(body: AlgorithmCreate, db: AsyncSession = Depends(get_db)):
    algo = Algorithm(
        repo_url=body.repo_url,
        name=body.name,
        description=body.description,
        version=body.version,
        commit_hash=body.commit_hash,
        required_asset_types=body.required_asset_types,
        required_options_level=body.required_options_level,
        required_account_features=body.required_account_features,
        supported_brokers=body.supported_brokers,
        data_dependencies=body.data_dependencies,
        config_schema=body.config_schema,
        custom_events=body.custom_events,
        install_status="installed",
    )
    db.add(algo)
    await db.flush()
    return _algo_to_response(algo)


@router.get("/api/algorithms")
async def list_algorithms(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Algorithm))
    return [_algo_to_response(a) for a in result.scalars().all()]


@router.get("/api/algorithms/{algorithm_id}")
async def get_algorithm(algorithm_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Algorithm).where(Algorithm.id == algorithm_id))
    algo = result.scalar_one_or_none()
    if algo is None:
        raise HTTPException(status_code=404, detail="Algorithm not found")
    return _algo_to_response(algo)


@router.delete("/api/algorithms/{algorithm_id}", status_code=204)
async def delete_algorithm(algorithm_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Algorithm).where(Algorithm.id == algorithm_id))
    algo = result.scalar_one_or_none()
    if algo is None:
        raise HTTPException(status_code=404, detail="Algorithm not found")

    # Collect instance IDs so we can cascade through runs/decisions/comparisons/trades/positions.
    instance_rows = await db.execute(
        select(AlgorithmInstance.id).where(AlgorithmInstance.algorithm_id == algorithm_id)
    )
    instance_ids = [row[0] for row in instance_rows.all()]

    if instance_ids:
        # Null out active_run_id on instances to break the circular FK before deleting runs.
        await db.execute(
            update(AlgorithmInstance)
            .where(AlgorithmInstance.id.in_(instance_ids))
            .values(active_run_id=None)
        )
        await db.execute(
            delete(AlgorithmRun).where(AlgorithmRun.instance_id.in_(instance_ids))
        )
        await db.execute(
            delete(DecisionLog).where(DecisionLog.instance_id.in_(instance_ids))
        )
        await db.execute(
            delete(BacktestComparison).where(BacktestComparison.instance_id.in_(instance_ids))
        )
        # pdt_tracking references trade_log.id — clear referencing rows before deleting trades.
        trade_id_rows = await db.execute(
            select(TradeLog.id).where(TradeLog.instance_id.in_(instance_ids))
        )
        trade_ids = [row[0] for row in trade_id_rows.all()]
        if trade_ids:
            await db.execute(
                delete(PDTTracking).where(PDTTracking.trade_id.in_(trade_ids))
            )
        await db.execute(
            delete(TradeLog).where(TradeLog.instance_id.in_(instance_ids))
        )
        await db.execute(
            delete(Position).where(Position.instance_id.in_(instance_ids))
        )
        # Clear Account.locked_by where it points to any of these instances.
        await db.execute(
            update(Account)
            .where(Account.locked_by.in_(instance_ids))
            .values(locked_by=None)
        )
        await db.flush()
        await db.execute(
            delete(AlgorithmInstance).where(AlgorithmInstance.algorithm_id == algorithm_id)
        )

    # Also delete any BacktestComparison rows tied directly to this algorithm.
    await db.execute(
        delete(BacktestComparison).where(BacktestComparison.algorithm_id == algorithm_id)
    )

    await db.delete(algo)


@router.post("/api/algorithms/{algorithm_id}/instances", status_code=201)
async def create_instance(
    algorithm_id: str, body: InstanceCreate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Algorithm).where(Algorithm.id == algorithm_id))
    algo = result.scalar_one_or_none()
    if algo is None:
        raise HTTPException(status_code=404, detail="Algorithm not found")

    instance = AlgorithmInstance(
        algorithm_id=algorithm_id,
        account_id=body.account_id,
        worker_id=body.worker_id,
        config_values=body.config_values,
        status="stopped",
    )
    db.add(instance)
    await db.flush()
    return await _enrich_instance(instance, db)


@router.get("/api/algorithms/{algorithm_id}/instances")
async def list_instances(algorithm_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.algorithm_id == algorithm_id)
    )
    return [await _enrich_instance(i, db) for i in result.scalars().all()]


@router.get("/api/instances")
async def list_all_instances(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AlgorithmInstance))
    return [await _enrich_instance(i, db) for i in result.scalars().all()]


@router.get("/api/instances/{instance_id}")
async def get_instance(instance_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.id == instance_id)
    )
    inst = result.scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    return await _enrich_instance(inst, db)


class InstanceUpdate(BaseModel):
    config_values: Optional[dict] = None
    status: Optional[str] = None

@router.patch("/api/instances/{instance_id}")
async def update_instance(instance_id: str, body: InstanceUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AlgorithmInstance).where(AlgorithmInstance.id == instance_id))
    inst = result.scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    if body.config_values is not None:
        inst.config_values = body.config_values
    if body.status is not None:
        inst.status = body.status
    return await _enrich_instance(inst, db)


@router.delete("/api/instances/{instance_id}", status_code=204)
async def delete_instance(instance_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AlgorithmInstance).where(AlgorithmInstance.id == instance_id))
    inst = result.scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    await db.delete(inst)
