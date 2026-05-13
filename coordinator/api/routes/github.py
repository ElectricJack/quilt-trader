from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db, get_container
from coordinator.database.models import Algorithm, Setting
from coordinator.services.github_service import GitHubService
from coordinator.services.package_manager import PackageManager, PackageError

router = APIRouter(prefix="/api/github", tags=["github"])


class InstallRequest(BaseModel):
    full_name: str


async def _get_pat(db: AsyncSession) -> str:
    result = await db.execute(select(Setting).where(Setting.key == "github_pat"))
    setting = result.scalar_one_or_none()
    if not setting:
        raise HTTPException(status_code=400, detail="GitHub PAT not configured")
    container = get_container()
    return container.encryption.decrypt(setting.value)


@router.get("/repos")
async def list_repos(db: AsyncSession = Depends(get_db)):
    pat = await _get_pat(db)
    service = GitHubService(pat=pat)
    repos = service.list_quilt_repos()
    return [
        {
            "name": r.name,
            "full_name": r.full_name,
            "description": r.description,
            "clone_url": r.clone_url,
            "html_url": r.html_url,
        }
        for r in repos
    ]


@router.post("/install", status_code=201)
async def install_algorithm(body: InstallRequest, db: AsyncSession = Depends(get_db)):
    pat = await _get_pat(db)
    gh = GitHubService(pat=pat)
    clone_url = gh.get_clone_url(body.full_name)
    pm = PackageManager(packages_dir="data/packages")
    name = body.full_name.split("/")[-1]
    try:
        pm.clone_repo(clone_url, name)
        pm.create_venv(name)
        pm.install_requirements(name)
        manifest = pm.validate_package(name)
        commit_hash = pm.get_commit_hash(name)
    except PackageError as e:
        raise HTTPException(status_code=422, detail=str(e))
    algo = Algorithm(
        repo_url=f"https://github.com/{body.full_name}",
        name=manifest.get("name", name),
        description=manifest.get("description"),
        version=manifest.get("version"),
        commit_hash=commit_hash,
        install_status="installed",
    )
    db.add(algo)
    await db.flush()
    return {
        "id": algo.id,
        "name": algo.name,
        "description": algo.description,
        "version": algo.version,
        "install_status": algo.install_status,
        "repo_url": algo.repo_url,
    }
