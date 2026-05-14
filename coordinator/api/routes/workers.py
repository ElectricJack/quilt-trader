import io
import logging
import secrets
import tarfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.database.models import AlgorithmInstance, Worker

logger = logging.getLogger(__name__)
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
        "install_status": worker.install_status,
        # install_token is included so the UI can render the one-liner after create.
        "install_token": worker.install_token,
        "created_at": worker.created_at.isoformat() if worker.created_at else None,
    }


def _generate_install_token() -> str:
    return secrets.token_urlsafe(32)


@router.post("", status_code=201)
async def create_worker(body: WorkerCreate, db: AsyncSession = Depends(get_db)):
    worker = Worker(
        name=body.name,
        tailscale_ip=body.tailscale_ip,
        max_algorithms=body.max_algorithms,
        install_token=_generate_install_token(),
        install_status="pending",
    )
    db.add(worker)
    await db.flush()
    return _to_response(worker)


@router.get("")
async def list_workers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Worker))
    workers = result.scalars().all()
    return [_to_response(w) for w in workers]


def _repo_root() -> Path:
    """Locate the quilt-trader source root by walking up from this file."""
    return Path(__file__).resolve().parents[3]


def _build_worker_package_tarball() -> bytes:
    """Stream-build a gzipped tarball of the worker subset of the source tree."""
    root = _repo_root()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for sub in ("worker", "sdk", "pyproject.toml"):
            path = root / sub
            if not path.exists():
                continue
            tar.add(
                path,
                arcname=sub,
                # Skip caches and compiled artifacts.
                filter=lambda ti: None if (
                    "__pycache__" in ti.name
                    or ti.name.endswith(".pyc")
                    or ti.name.endswith(".pyo")
                ) else ti,
            )
    return buf.getvalue()


async def _worker_by_token(token: str, db: AsyncSession) -> Worker:
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    worker = (await db.execute(
        select(Worker).where(Worker.install_token == token)
    )).scalar_one_or_none()
    if worker is None:
        raise HTTPException(status_code=401, detail="Invalid or expired install token")
    return worker


@router.get("/install/package.tar.gz")
async def worker_install_package(token: str, db: AsyncSession = Depends(get_db)):
    """Serve the worker source tarball. Token-gated; valid until the worker is claimed."""
    await _worker_by_token(token, db)
    data = _build_worker_package_tarball()
    return Response(
        content=data,
        media_type="application/gzip",
        headers={"Content-Disposition": "attachment; filename=quilt-trader-worker.tar.gz"},
    )


@router.post("/install/claim/{worker_id}")
async def claim_worker(worker_id: str, token: str, db: AsyncSession = Depends(get_db)):
    """Called by the install script once the worker is up. Invalidates the install token.

    Lives under /install/ so it can be auth-exempt (the Pi has the install token, not an API key).
    """
    worker = (await db.execute(
        select(Worker).where(Worker.id == worker_id)
    )).scalar_one_or_none()
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found")
    if worker.install_token != token:
        raise HTTPException(status_code=401, detail="Invalid token")
    if worker.install_status == "claimed":
        return {"ok": True, "already_claimed": True}
    worker.install_status = "claimed"
    worker.install_token = None
    await db.flush()
    return {"ok": True, "already_claimed": False}


@router.post("/{worker_id}/regenerate-token", status_code=200)
async def regenerate_install_token(worker_id: str, db: AsyncSession = Depends(get_db)):
    """Issue a fresh install token (also resets install_status to pending)."""
    worker = (await db.execute(
        select(Worker).where(Worker.id == worker_id)
    )).scalar_one_or_none()
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found")
    worker.install_token = _generate_install_token()
    worker.install_status = "pending"
    await db.flush()
    return _to_response(worker)


@router.get("/{worker_id}/install-command", response_class=PlainTextResponse)
async def worker_install_command(
    worker_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render the one-liner the user pastes into a fresh Pi shell.

    Coordinator host is derived from the request's Host header so the command targets
    whatever URL the user is browsing the dashboard at.
    """
    from coordinator.config import CoordinatorConfig

    worker = (await db.execute(
        select(Worker).where(Worker.id == worker_id)
    )).scalar_one_or_none()
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found")
    if not worker.install_token:
        raise HTTPException(status_code=409, detail="Worker has no active install token; regenerate it first")

    config = CoordinatorConfig()
    bootstrap_url = config.worker_install_script_url
    coord_host = request.headers.get("host", f"{config.host}:{config.port}")
    coord_scheme = "https" if request.url.scheme == "https" else "http"

    command = (
        f"curl -fsSL '{bootstrap_url}' | "
        f"TAILSCALE_AUTHKEY=tskey-CHANGE-ME "
        f"COORDINATOR_URL='{coord_scheme}://{coord_host}' "
        f"WORKER_ID='{worker.id}' "
        f"WORKER_NAME='{worker.name}' "
        f"WORKER_TOKEN='{worker.install_token}' "
        f"sudo -E bash"
    )
    return command


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

    # AlgorithmInstance.worker_id is NOT NULL, so a worker with assigned
    # instances cannot be deleted without first moving or deleting them.
    instance_count = (await db.execute(
        select(func.count(AlgorithmInstance.id))
        .where(AlgorithmInstance.worker_id == worker_id)
    )).scalar_one()
    if instance_count > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete worker: {instance_count} algorithm "
                f"instance{'s' if instance_count != 1 else ''} still "
                "assigned. Move or delete them first."
            ),
        )

    await db.delete(worker)
