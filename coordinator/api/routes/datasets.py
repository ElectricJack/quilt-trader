from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import desc, select

from coordinator.api.dependencies import get_container
from coordinator.database.models import DatasetDownload, QuotaUsage
from coordinator.services.datasets.registry import get as _registry_get, list_all as _list_specs
from coordinator.services.datasets.storage import _get_service, load_dataset

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


def _spec_to_dict(spec) -> dict:
    return {
        "name": spec.name,
        "provider": spec.provider,
        "endpoint_path": spec.endpoint_path,
        "event_date_column": spec.event_date_column,
        "knowledge_date_column": spec.knowledge_date_column,
        "symbol_keyed": spec.symbol_keyed,
        "id_columns": list(spec.id_columns),
        "columns": spec.columns,
        "pagination": spec.pagination,
        "page_size": spec.page_size,
        "date_chunk_days": spec.date_chunk_days,
        "free_tier": spec.free_tier,
    }


@router.get("")
async def list_datasets():
    return [_spec_to_dict(s) for s in _list_specs()]


@router.get("/providers")
async def list_dataset_providers(request: Request):
    adapters: dict[str, Any] = getattr(request.app.state, "dataset_adapters", {})
    seen = {s.provider for s in _list_specs()}
    result = []
    for prov in sorted(seen):
        available = prov in adapters
        result.append({
            "name": prov,
            "available": available,
            "reason": None if available else f"{prov}_api_key setting missing",
        })
    return result


@router.get("/coverage")
async def coverage_index():
    return [
        {
            "name": s.name,
            "provider": s.provider,
            "symbol_keyed": s.symbol_keyed,
            "detail_url": f"/api/datasets/{s.name}/coverage",
        }
        for s in _list_specs()
    ]


@router.get("/downloads")
async def list_downloads(
    status: str | None = None,
    provider: str | None = None,
):
    sf = get_container().session_factory
    async with sf() as s:
        q = select(DatasetDownload).order_by(desc(DatasetDownload.queued_at))
        if status:
            q = q.where(DatasetDownload.status == status)
        if provider:
            q = q.where(DatasetDownload.provider == provider)
        rows = (await s.execute(q.limit(500))).scalars().all()
        return [_row_to_dict(r) for r in rows]


@router.get("/downloads/{download_id}")
async def get_download(download_id: int):
    sf = get_container().session_factory
    async with sf() as s:
        row = await s.get(DatasetDownload, download_id)
        if row is None:
            raise HTTPException(404)
        return _row_to_dict(row)


@router.delete("/downloads/{download_id}")
async def cancel_download(download_id: int):
    sf = get_container().session_factory
    async with sf() as s:
        row = await s.get(DatasetDownload, download_id)
        if row is None:
            raise HTTPException(404)
        if row.status == "queued":
            row.status = "cancelled"
            await s.commit()
        return _row_to_dict(row)


@router.get("/quota")
async def list_quota():
    today = datetime.now(timezone.utc).date()
    sf = get_container().session_factory
    async with sf() as s:
        rows = (
            await s.execute(select(QuotaUsage).where(QuotaUsage.reset_window == today))
        ).scalars().all()
        return [
            {
                "provider": r.provider,
                "reset_window": r.reset_window.isoformat(),
                "calls_used": r.calls_used,
                "daily_limit": r.daily_limit,
                "exhausted": r.exhausted,
            }
            for r in rows
        ]


@router.get("/quota/{provider}")
async def get_quota(provider: str):
    today = datetime.now(timezone.utc).date()
    sf = get_container().session_factory
    async with sf() as s:
        row = (
            await s.execute(
                select(QuotaUsage).where(
                    QuotaUsage.provider == provider,
                    QuotaUsage.reset_window == today,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return {
                "provider": provider,
                "calls_used": 0,
                "daily_limit": None,
                "exhausted": False,
            }
        return {
            "provider": row.provider,
            "reset_window": row.reset_window.isoformat(),
            "calls_used": row.calls_used,
            "daily_limit": row.daily_limit,
            "exhausted": row.exhausted,
        }


@router.get("/{name}/coverage")
async def get_dataset_coverage(name: str):
    try:
        spec = _registry_get(name)
    except KeyError:
        raise HTTPException(404, f"unknown dataset: {name}")
    svc = _get_service()
    out: list[dict] = []
    if spec.symbol_keyed:
        short = spec.name.split(".", 1)[1]
        base = svc._data_root / "datasets" / spec.provider / short
        if base.exists():
            for p in sorted(base.glob("*.parquet")):
                out.append(_coverage_entry(p, symbol=p.stem))
    else:
        p = svc._path_for(spec, None)
        if p.exists():
            out.append(_coverage_entry(p, symbol=None))
    return {"name": name, "symbols": out}


def _coverage_entry(path, symbol):
    df = pd.read_parquet(path, columns=["event_date", "knowledge_date"])
    return {
        "symbol": symbol,
        "row_count": int(len(df)),
        "event_date_min": str(df["event_date"].min()) if len(df) else None,
        "event_date_max": str(df["event_date"].max()) if len(df) else None,
        "knowledge_date_max": str(df["knowledge_date"].max()) if len(df) else None,
        "file_size_bytes": int(path.stat().st_size),
        "last_modified": int(path.stat().st_mtime),
    }


@router.get("/{name}/rows")
async def get_dataset_rows(
    name: str,
    symbol: str | None = Query(None),
    as_of: str | None = Query(None),
    start: date | None = Query(None),
    end: date | None = Query(None),
    columns: str | None = Query(None),
    q: str | None = Query(None, description="case-insensitive substring filter across all columns"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    try:
        spec = _registry_get(name)
    except KeyError:
        raise HTTPException(404, f"unknown dataset: {name}")
    # as_of is a forward-bias guard for algorithm execution, not a browsing
    # constraint. Browsing the UI defaults it to "now" so users see every
    # row that's been disclosed (i.e., everything on disk).
    as_of_dt = (
        pd.Timestamp(as_of, tz="UTC").to_pydatetime()
        if as_of
        else datetime.now(timezone.utc)
    )
    cols = columns.split(",") if columns else None
    df = load_dataset(name, as_of=as_of_dt, symbol=symbol, start=start, end=end, columns=cols)
    if q:
        needle = q.lower()
        # Build a per-row haystack lazily via DataFrame.astype(str).agg
        haystack = df.astype(str).apply(lambda r: " ".join(r.values).lower(), axis=1)
        df = df[haystack.str.contains(needle, regex=False, na=False)]
    total = len(df)
    page = df.iloc[offset : offset + limit]
    return {
        "total": int(total),
        "rows": page.to_dict(orient="records"),
        "spec_summary": {
            "columns": spec.columns,
            "event_date_column": spec.event_date_column,
            "knowledge_date_column": spec.knowledge_date_column,
        },
    }


@router.get("/{name}")
async def get_dataset(name: str):
    try:
        spec = _registry_get(name)
    except KeyError:
        raise HTTPException(404, f"unknown dataset: {name}")
    return _spec_to_dict(spec)


class DownloadRequest(BaseModel):
    name: str
    params: dict = {}


@router.post("/downloads")
async def queue_download(req: DownloadRequest, request: Request):
    try:
        spec = _registry_get(req.name)
    except KeyError:
        raise HTTPException(404, f"unknown dataset: {req.name}")
    adapters = getattr(request.app.state, "dataset_adapters", {})
    if spec.provider not in adapters:
        raise HTTPException(
            400,
            f"{spec.provider} adapter not configured (missing {spec.provider}_api_key)",
        )
    sf = get_container().session_factory
    async with sf() as s:
        row = DatasetDownload(
            dataset_name=req.name,
            provider=spec.provider,
            request_payload=req.params,
            status="queued",
            created_by="api",
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
        result = _row_to_dict(row)

    # Kick off the dispatcher so the job actually runs. The task is fire-and-
    # forget; the dispatcher writes status transitions back to the DB row.
    dispatcher = getattr(request.app.state, "dataset_dispatcher", None)
    manager = getattr(request.app.state, "download_manager", None)
    if dispatcher is not None:
        # Re-load the row inside the task's own session to avoid leaking the
        # response-scoped one across the task boundary.
        async def _run(job_id: int) -> None:
            async with sf() as s2:
                job = await s2.get(DatasetDownload, job_id)
                if job is None:
                    return
            await dispatcher.execute(job, manager=manager)

        import asyncio as _asyncio
        _asyncio.create_task(_run(row.id))

    return result


def _row_to_dict(r: DatasetDownload) -> dict:
    return {
        "id": r.id,
        "dataset_name": r.dataset_name,
        "provider": r.provider,
        "request_payload": r.request_payload,
        "status": r.status,
        "queued_at": r.queued_at.isoformat() if r.queued_at else None,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        "rows_fetched": r.rows_fetched,
        "calls_consumed": r.calls_consumed,
        "progress_pct": r.progress_pct,
        "progress_message": r.progress_message,
        "error_message": r.error_message,
    }
