from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.database.models import Algorithm, AlgorithmInstance

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


def _instance_to_response(inst: AlgorithmInstance) -> dict:
    return {
        "id": inst.id,
        "algorithm_id": inst.algorithm_id,
        "account_id": inst.account_id,
        "worker_id": inst.worker_id,
        "status": inst.status,
        "active_run_id": inst.active_run_id,
        "config_values": inst.config_values,
        "persisted_state": inst.persisted_state,
        "state_stale": inst.state_stale,
        "lifetime_metrics": inst.lifetime_metrics,
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
    return _instance_to_response(instance)


@router.get("/api/algorithms/{algorithm_id}/instances")
async def list_instances(algorithm_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.algorithm_id == algorithm_id)
    )
    return [_instance_to_response(i) for i in result.scalars().all()]


@router.get("/api/instances")
async def list_all_instances(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AlgorithmInstance))
    return [_instance_to_response(i) for i in result.scalars().all()]


@router.get("/api/instances/{instance_id}")
async def get_instance(instance_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.id == instance_id)
    )
    inst = result.scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    return _instance_to_response(inst)
