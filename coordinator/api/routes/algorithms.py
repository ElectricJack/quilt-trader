import base64
import hashlib
import io
import re
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
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
    ParameterSet,
    Position,
    Setting,
    TradeLog,
    Worker,
)
from coordinator.services.github_service import GitHubService
from coordinator.services.package_manager import PackageError, PackageManager
from sdk.manifest import ManifestError, QuiltManifest
from sdk.validation import validate_algorithm_package

router = APIRouter(tags=["algorithms"])

# Override in tests via monkeypatch.
PACKAGE_ROOT = Path("data/packages")

from coordinator.services.asset_services import AssetType as _AssetType
_VALID_ASSET_CLASSES = {t.value for t in _AssetType}


def _validate_assets(raw: list) -> list[dict]:
    """Validate and normalize a list of asset entries.

    - Rejects entries without 'symbol'.
    - Rejects entries with an unrecognised 'asset_class'.
    - Defaults missing 'asset_class' to 'equities'.
    - Returns the normalised list (copies of the input dicts, never mutates).
    """
    result = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("symbol"):
            raise ValueError("missing 'symbol'")
        ac = entry.get("asset_class", "equities")
        if ac not in _VALID_ASSET_CLASSES:
            raise ValueError(f"invalid asset_class: {ac!r}")
        result.append({**entry, "asset_class": ac})
    return result


def _derive_package_dir_name(repo_url: str, algo_name: str = "") -> str:
    """Derive the on-disk directory name from a GitHub repo URL (last path segment).

    For locally-installed algorithms (empty repo_url), falls back to the
    algorithm name which matches the package directory.
    """
    if not repo_url:
        if algo_name:
            return algo_name
        raise ValueError("Cannot derive package directory: both repo_url and algo_name are empty")
    m = re.match(r"^https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", repo_url)
    if not m:
        raise ValueError(f"Cannot derive package directory from repo_url: {repo_url!r}")
    return m.group(1).split("/", 1)[1]


def _build_algorithm_tarball(pkg_dir: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(
            pkg_dir,
            arcname=pkg_dir.name,
            filter=lambda ti: None if (
                "__pycache__" in ti.name
                or ti.name.endswith(".pyc")
                or ti.name.endswith(".pyo")
            ) else ti,
        )
    return buf.getvalue()


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
    parameter_set_id: Optional[str] = None


def _algo_to_response(algo: Algorithm) -> dict:
    return {
        "id": algo.id,
        "repo_url": algo.repo_url,
        "source_path": algo.source_path,
        "name": algo.name,
        "description": algo.description,
        "version": algo.version,
        "commit_hash": algo.commit_hash,
        "required_asset_types": algo.required_asset_types,
        "required_options_level": algo.required_options_level,
        "required_account_features": algo.required_account_features,
        "supported_brokers": algo.supported_brokers,
        "data_dependencies": algo.assets,
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
        assets=body.data_dependencies,
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


@router.get("/api/algorithms/{algorithm_id}/package.tar.gz")
async def algorithm_package(
    algorithm_id: str,
    sha: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Serve a gzipped tarball of the algorithm package directory to authenticated workers."""
    token = request.headers.get("X-Worker-Install-Token")
    if not token:
        raise HTTPException(status_code=401, detail="Missing X-Worker-Install-Token")
    worker = (await db.execute(
        select(Worker).where(Worker.install_token == token)
    )).scalar_one_or_none()
    if worker is None:
        raise HTTPException(status_code=401, detail="Invalid worker token")
    algo = (await db.execute(
        select(Algorithm).where(Algorithm.id == algorithm_id)
    )).scalar_one_or_none()
    if algo is None:
        raise HTTPException(status_code=404, detail="Algorithm not found")
    if algo.commit_hash != sha:
        raise HTTPException(
            status_code=404,
            detail=f"Algorithm SHA mismatch: have {algo.commit_hash}, requested {sha}",
        )
    try:
        pkg_dir_name = _derive_package_dir_name(algo.repo_url, algo.name)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    src_path = PACKAGE_ROOT / pkg_dir_name
    if not src_path.exists():
        raise HTTPException(status_code=404, detail="Algorithm package not on disk")
    data = _build_algorithm_tarball(src_path)
    return Response(
        content=data,
        media_type="application/gzip",
        headers={"Content-Disposition": f"attachment; filename={pkg_dir_name}.tar.gz"},
    )


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

    config_values = body.config_values
    parameter_set_id = body.parameter_set_id
    if parameter_set_id is not None:
        ps = (await db.execute(
            select(ParameterSet).where(
                ParameterSet.algorithm_id == algorithm_id,
                ParameterSet.id == parameter_set_id,
            )
        )).scalar_one_or_none()
        if ps is None:
            raise HTTPException(status_code=404, detail="Parameter set not found")
        config_values = ps.config_values

    instance = AlgorithmInstance(
        algorithm_id=algorithm_id,
        account_id=body.account_id,
        worker_id=body.worker_id,
        config_values=config_values,
        parameter_set_id=parameter_set_id,
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


def _hash_directory(path: Path) -> str:
    """Recursive content hash, excluding __pycache__ / .git directories."""
    h = hashlib.sha256()
    for entry in sorted(path.rglob("*")):
        if entry.is_dir():
            continue
        rel = entry.relative_to(path)
        if any(p.startswith(".") or p == "__pycache__" for p in rel.parts):
            continue
        h.update(str(rel).encode())
        h.update(entry.read_bytes())
    return h.hexdigest()[:12]


class InstallFromUrlRequest(BaseModel):
    repo_url: str


class InstallRequest(BaseModel):
    source: Optional[str] = None
    repo_url: Optional[str] = None  # backwards compat alias
    name_override: Optional[str] = None
    ref: Optional[str] = None  # branch or commit SHA for GitHub


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

    pkg_dir = Path(pm.package_path(name))
    val_errors = validate_algorithm_package(pkg_dir)
    if val_errors:
        shutil.rmtree(pkg_dir, ignore_errors=True)
        raise HTTPException(
            status_code=400,
            detail=f"Algorithm validation failed: {'; '.join(str(e) for e in val_errors)}",
        )

    # Populate `assets` from the new top-level manifest block if present.
    # Falls back to a best-effort conversion from the legacy
    # requirements.data_dependencies (deprecated) — the legacy entries lack
    # broker / asset_class so the conversion fills them from the manifest's
    # supported_brokers + asset_types defaults. Entries that can't be resolved
    # cleanly are dropped (the deploy-time _parse_assets filters strictly).
    assets_list = list(manifest.assets) if manifest.assets else []
    if not assets_list and manifest.requirements.data_dependencies:
        default_broker = (manifest.requirements.brokers or ["alpaca"])[0]
        default_class = (manifest.requirements.asset_types or ["equities"])[0]
        for dep in manifest.requirements.data_dependencies:
            if not isinstance(dep, dict):
                continue
            sym = dep.get("symbol")
            if not sym:
                continue
            assets_list.append({
                "broker": dep.get("broker") or default_broker,
                "symbol": sym,
                "asset_class": dep.get("asset_class") or default_class,
            })

    try:
        assets_list = _validate_assets(assets_list)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    algo = Algorithm(
        repo_url=public_url,
        name=manifest_disk.get("name", manifest.name),
        description=manifest_disk.get("description") or manifest.description,
        version=manifest_disk.get("version") or manifest.version,
        commit_hash=commit_hash,
        required_asset_types=manifest.requirements.asset_types or None,
        required_options_level=manifest.requirements.options_level,
        required_account_features=manifest.requirements.account_features or None,
        supported_brokers=manifest.requirements.brokers,
        assets=assets_list or None,
        config_schema={"parameters": manifest.config_parameters} if manifest.config_parameters else None,
        custom_events=manifest.custom_events or None,
        install_status="installed",
    )
    db.add(algo)
    await db.flush()
    return _algo_to_response(algo)


@router.post("/api/algorithms/install", status_code=201)
async def install_algorithm(body: InstallRequest, db: AsyncSession = Depends(get_db)):
    """Install an algorithm from a local directory path or a GitHub URL.

    Accepts either ``source`` (preferred) or ``repo_url`` (backwards compat).
    For local paths, ``source`` must be an absolute or relative path to a
    directory on disk that contains a valid ``quilt.yaml``.
    """
    source = body.source or body.repo_url
    if not source:
        raise HTTPException(status_code=400, detail="Either `source` or `repo_url` is required")

    is_url = source.startswith("http://") or source.startswith("https://")

    if is_url:
        # --- GitHub / URL path: delegate to existing install-from-url logic ---
        full_name = _full_name_from_url(source)
        if not full_name:
            raise HTTPException(status_code=400, detail=f"Unsupported repo URL: {source}")
        owner, repo = full_name.split("/", 1)

        yaml_text = await _fetch_manifest_yaml(owner, repo, db)
        try:
            manifest = QuiltManifest.from_string(yaml_text)
        except ManifestError as e:
            raise HTTPException(status_code=422, detail=f"Invalid manifest: {e}")
        if manifest.type != "algorithm":
            raise HTTPException(
                status_code=422, detail=f"That repo is a {manifest.type}, not an algorithm."
            )

        public_url = f"https://github.com/{owner}/{repo}.git"
        clone_url = public_url
        setting = (await db.execute(
            select(Setting).where(Setting.key == "github_pat")
        )).scalar_one_or_none()
        if setting is not None:
            container = get_container()
            pat = container.encryption.decrypt(setting.value)
            clone_url = f"https://{pat}@github.com/{owner}/{repo}.git"

        name = body.name_override or repo
        pm = PackageManager(packages_dir="data/packages")
        try:
            pm.clone_repo(clone_url, name)
            pm.create_venv(name)
            pm.install_requirements(name)
            manifest_disk = pm.validate_package(name)
            commit_hash = pm.get_commit_hash(name)
        except PackageError as e:
            raise HTTPException(status_code=422, detail=str(e))

        pkg_dir = Path(pm.package_path(name))
        val_errors = validate_algorithm_package(pkg_dir)
        if val_errors:
            shutil.rmtree(pkg_dir, ignore_errors=True)
            raise HTTPException(
                status_code=400,
                detail=f"Algorithm validation failed: {'; '.join(str(e) for e in val_errors)}",
            )

        algo = Algorithm(
            repo_url=public_url,
            source_path=None,
            name=manifest_disk.get("name", manifest.name),
            description=manifest_disk.get("description") or manifest.description,
            version=manifest_disk.get("version") or manifest.version,
            commit_hash=commit_hash,
            install_status="installed",
        )
        db.add(algo)
        await db.flush()
        return _algo_to_response(algo)

    else:
        # --- Local directory path ---
        src = Path(source).resolve()
        if not src.is_dir():
            raise HTTPException(
                status_code=400, detail=f"local source path is not a directory: {source}"
            )

        # Determine name: name_override > manifest name > directory name
        name = body.name_override
        if not name:
            try:
                mf_preview = QuiltManifest.from_file(src / "quilt.yaml")
                name = mf_preview.name
            except Exception:
                name = src.name

        dest = PACKAGE_ROOT / name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)

        commit_hash = "local:" + _hash_directory(dest)

        val_errors = validate_algorithm_package(dest)
        if val_errors:
            shutil.rmtree(dest, ignore_errors=True)
            raise HTTPException(
                status_code=400,
                detail=f"Validation failed: {'; '.join(str(e) for e in val_errors)}",
            )

        try:
            mf = QuiltManifest.from_file(dest / "quilt.yaml")
        except ManifestError as e:
            shutil.rmtree(dest, ignore_errors=True)
            raise HTTPException(status_code=422, detail=f"Invalid manifest: {e}")

        if mf.type != "algorithm":
            shutil.rmtree(dest, ignore_errors=True)
            raise HTTPException(
                status_code=422, detail=f"That package is a {mf.type}, not an algorithm."
            )

        assets_list = list(mf.assets) if mf.assets else []
        if not assets_list and mf.requirements.data_dependencies:
            default_broker = (mf.requirements.brokers or ["alpaca"])[0]
            default_class = (mf.requirements.asset_types or ["equities"])[0]
            for dep in mf.requirements.data_dependencies:
                if not isinstance(dep, dict):
                    continue
                sym = dep.get("symbol")
                if not sym:
                    continue
                assets_list.append({
                    "broker": dep.get("broker") or default_broker,
                    "symbol": sym,
                    "asset_class": dep.get("asset_class") or default_class,
                })

        try:
            assets_list = _validate_assets(assets_list)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        algo = Algorithm(
            repo_url="",
            source_path=str(src),
            name=name,
            description=mf.description or None,
            version=mf.version or None,
            commit_hash=commit_hash,
            required_asset_types=mf.requirements.asset_types or None,
            required_options_level=mf.requirements.options_level,
            required_account_features=mf.requirements.account_features or None,
            supported_brokers=mf.requirements.brokers,
            assets=assets_list or None,
            config_schema={"parameters": mf.config_parameters} if mf.config_parameters else None,
            custom_events=mf.custom_events or None,
            install_status="installed",
        )
        db.add(algo)
        await db.flush()
        return _algo_to_response(algo)
