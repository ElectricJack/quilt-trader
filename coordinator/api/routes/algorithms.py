import base64
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_container, get_db
from coordinator.api.serialization import to_iso_utc
from coordinator.database.models import (
    Account,
    Algorithm,
    AlgorithmInstance,
    AlgorithmRun,
    BacktestComparison,
    DecisionLog,
    PDTTracking,
    Position,
    Setting,
    TradeLog,
)
from coordinator.services.github_service import GitHubService
from coordinator.services.package_manager import PackageError, PackageManager
from sdk.manifest import ManifestError, QuiltManifest

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
        "installed_at": to_iso_utc(algo.installed_at),
        "updated_at": to_iso_utc(algo.updated_at),
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
        "created_at": to_iso_utc(inst.created_at),
        "updated_at": to_iso_utc(inst.updated_at),
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


def _full_name_from_url(repo_url: str) -> str | None:
    """Parse 'owner/repo' from a GitHub clone URL."""
    if not repo_url:
        return None
    m = re.match(r"^https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", repo_url)
    return m.group(1) if m else None


@router.get("/api/algorithms/{algorithm_id}/git-status")
async def algorithm_git_status(algorithm_id: str, db: AsyncSession = Depends(get_db)):
    algo = (await db.execute(select(Algorithm).where(Algorithm.id == algorithm_id))).scalar_one_or_none()
    if algo is None:
        raise HTTPException(status_code=404, detail="Algorithm not found")
    full_name = _full_name_from_url(algo.repo_url)
    if not full_name:
        raise HTTPException(status_code=400, detail=f"Unsupported repo URL: {algo.repo_url}")

    # PAT lookup mirrors GitHub repo listing endpoint
    setting = (await db.execute(select(Setting).where(Setting.key == "github_pat"))).scalar_one_or_none()
    if not setting:
        raise HTTPException(status_code=400, detail="GitHub PAT not configured")
    container = get_container()
    pat = container.encryption.decrypt(setting.value)
    gh = GitHubService(pat=pat)
    try:
        status = gh.get_repo_status(full_name, algo.commit_hash)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GitHub error: {e}")
    return status


@router.post("/api/algorithms/{algorithm_id}/update")
async def update_algorithm(algorithm_id: str, db: AsyncSession = Depends(get_db)):
    algo = (await db.execute(select(Algorithm).where(Algorithm.id == algorithm_id))).scalar_one_or_none()
    if algo is None:
        raise HTTPException(status_code=404, detail="Algorithm not found")

    full_name = _full_name_from_url(algo.repo_url)
    if not full_name:
        raise HTTPException(status_code=400, detail=f"Unsupported repo URL: {algo.repo_url}")

    name = full_name.split("/")[-1]
    pm = PackageManager(packages_dir="data/packages")
    try:
        new_sha = pm.update_package(name)
        # Re-validate manifest in case manifest fields changed
        manifest = pm.validate_package(name)
    except PackageError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Update failed: {e}")

    algo.commit_hash = new_sha
    if manifest.get("version"):
        algo.version = manifest["version"]
    if manifest.get("description"):
        algo.description = manifest["description"]
    if manifest.get("name"):
        algo.name = manifest["name"]
    await db.flush()
    return {
        "id": algo.id,
        "name": algo.name,
        "description": algo.description,
        "version": algo.version,
        "commit_hash": algo.commit_hash,
        "repo_url": algo.repo_url,
        "install_status": algo.install_status,
    }


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


class InstallFromUrlRequest(BaseModel):
    repo_url: str


async def _fetch_manifest_yaml(owner: str, repo: str, db: AsyncSession) -> str:
    """Try public raw URL; on 404 fall back to PAT-authenticated contents API."""
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/quilt.yaml"
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(raw_url)
    if r.status_code == 200:
        return r.text
    if r.status_code != 404:
        raise HTTPException(status_code=502, detail=f"Manifest fetch failed: {r.status_code}")

    setting = (await db.execute(
        select(Setting).where(Setting.key == "github_pat")
    )).scalar_one_or_none()
    if setting is None:
        raise HTTPException(
            status_code=400,
            detail="Repository not found or quilt.yaml missing. "
                   "If the repo is private, configure a GitHub PAT in Settings.",
        )
    container = get_container()
    pat = container.encryption.decrypt(setting.value)
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/quilt.yaml"
    headers = {"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json"}
    async with httpx.AsyncClient(timeout=10) as c:
        ar = await c.get(api_url, headers=headers)
    if ar.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail="Repository or quilt.yaml not found, even with configured PAT.",
        )
    body = ar.json()
    if body.get("encoding") != "base64":
        raise HTTPException(status_code=502, detail="Unexpected content encoding")
    return base64.b64decode(body["content"]).decode()


@router.post("/api/algorithms/install-from-url", status_code=201)
async def install_from_url(body: InstallFromUrlRequest, db: AsyncSession = Depends(get_db)):
    full_name = _full_name_from_url(body.repo_url)
    if not full_name:
        raise HTTPException(status_code=400, detail=f"Unsupported repo URL: {body.repo_url}")
    owner, repo = full_name.split("/", 1)

    yaml_text = await _fetch_manifest_yaml(owner, repo, db)
    try:
        manifest = QuiltManifest.from_string(yaml_text)
    except ManifestError as e:
        raise HTTPException(status_code=422, detail=f"Invalid manifest: {e}")
    if manifest.type != "algorithm":
        raise HTTPException(status_code=422,
                            detail=f"That repo is a {manifest.type}, not an algorithm.")

    # Resolve clone url; private repos need PAT
    public_url = f"https://github.com/{owner}/{repo}.git"
    clone_url = public_url
    setting = (await db.execute(
        select(Setting).where(Setting.key == "github_pat")
    )).scalar_one_or_none()
    if setting is not None:
        container = get_container()
        pat = container.encryption.decrypt(setting.value)
        clone_url = f"https://{pat}@github.com/{owner}/{repo}.git"

    pm = PackageManager(packages_dir="data/packages")
    name = repo
    try:
        pm.clone_repo(clone_url, name)
        pm.create_venv(name)
        pm.install_requirements(name)
        manifest_disk = pm.validate_package(name)
        commit_hash = pm.get_commit_hash(name)
    except PackageError as e:
        raise HTTPException(status_code=422, detail=str(e))

    algo = Algorithm(
        repo_url=public_url,
        name=manifest_disk.get("name", manifest.name),
        description=manifest_disk.get("description") or manifest.description,
        version=manifest_disk.get("version") or manifest.version,
        commit_hash=commit_hash,
        install_status="installed",
    )
    db.add(algo)
    await db.flush()
    return _algo_to_response(algo)
