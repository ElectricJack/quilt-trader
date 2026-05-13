from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.database.models import Worker

router = APIRouter(prefix="/api/workers", tags=["workers"])


class WorkerCreate(BaseModel):
    name: str
    tailscale_ip: str
    max_algorithms: int = 2


class WorkerUpdate(BaseModel):
    name: Optional[str] = None
    tailscale_ip: Optional[str] = None
    max_algorithms: Optional[int] = None


def _to_response(worker: Worker) -> dict:
    return {
        "id": worker.id,
        "name": worker.name,
        "tailscale_ip": worker.tailscale_ip,
        "status": worker.status,
        "last_heartbeat": worker.last_heartbeat.isoformat() if worker.last_heartbeat else None,
        "max_algorithms": worker.max_algorithms,
        "created_at": worker.created_at.isoformat() if worker.created_at else None,
    }


@router.post("", status_code=201)
async def create_worker(body: WorkerCreate, db: AsyncSession = Depends(get_db)):
    worker = Worker(
        name=body.name,
        tailscale_ip=body.tailscale_ip,
        max_algorithms=body.max_algorithms,
    )
    db.add(worker)
    await db.flush()
    return _to_response(worker)


@router.get("")
async def list_workers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Worker))
    workers = result.scalars().all()
    return [_to_response(w) for w in workers]


@router.get("/{worker_id}")
async def get_worker(worker_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Worker).where(Worker.id == worker_id))
    worker = result.scalar_one_or_none()
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found")
    return _to_response(worker)


@router.patch("/{worker_id}")
async def update_worker(
    worker_id: str, body: WorkerUpdate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Worker).where(Worker.id == worker_id))
    worker = result.scalar_one_or_none()
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found")

    if body.name is not None:
        worker.name = body.name
    if body.tailscale_ip is not None:
        worker.tailscale_ip = body.tailscale_ip
    if body.max_algorithms is not None:
        worker.max_algorithms = body.max_algorithms

    await db.flush()
    return _to_response(worker)


@router.delete("/{worker_id}", status_code=204)
async def delete_worker(worker_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Worker).where(Worker.id == worker_id))
    worker = result.scalar_one_or_none()
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found")
    await db.delete(worker)
