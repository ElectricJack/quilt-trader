from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from coordinator.services.scraper_registry import ScraperRegistry

router = APIRouter(prefix="/api/scrapers", tags=["scrapers"])

_registry: Optional[ScraperRegistry] = None


def set_registry(registry: ScraperRegistry) -> None:
    global _registry
    _registry = registry


def _require_registry() -> ScraperRegistry:
    if _registry is None:
        raise HTTPException(status_code=503, detail="scraper registry not initialized")
    return _registry


def _next_run_for(reg: ScraperRegistry, name: str) -> Optional[str]:
    for job in reg._scheduler.list_jobs():  # noqa: SLF001
        if job["id"] == f"scraper:{name}":
            return job.get("next_run")
    return None


def _record_to_dict(record, reg: ScraperRegistry) -> dict:
    return {
        "name": record.name,
        "schedule": record.schedule,
        "jitter_seconds": record.jitter_seconds,
        "next_run_at": _next_run_for(reg, record.name),
        "version": record.manifest.get("version"),
        "description": record.manifest.get("description"),
        "config_overrides": sorted(record.config.keys()),
        "last_status": record.last_status,
        "last_run_at": record.last_run_at,
        "data_url": f"/api/data/custom/{record.name}",
        "last_error": record.last_error,
    }


@router.get("")
async def list_scrapers():
    reg = _require_registry()
    return [_record_to_dict(r, reg) for r in reg.list_records()]


@router.get("/{name}")
async def get_scraper(name: str):
    reg = _require_registry()
    record = reg.get(name)
    if record is None:
        raise HTTPException(status_code=404, detail=f"scraper {name!r} not found")
    return _record_to_dict(record, reg)


@router.post("/{name}/run")
async def run_scraper_now(name: str):
    reg = _require_registry()
    record = reg.get(name)
    if record is None:
        raise HTTPException(status_code=404, detail=f"scraper {name!r} not found")
    result = await reg.run(name)
    return {
        "success": result.success,
        "error": result.error,
        "record": _record_to_dict(record, reg),
    }
