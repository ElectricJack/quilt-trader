from datetime import date
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.api.serialization import to_iso_utc
from coordinator.database.models import DataSource
from coordinator.services.data_service import DataService
from coordinator.services.download_manager import DownloadManager

router = APIRouter(prefix="/api/data", tags=["data"])

_data_service: Optional[DataService] = None
_download_manager: Optional[DownloadManager] = None


def set_data_service(svc: DataService) -> None:
    global _data_service
    _data_service = svc


def get_data_service() -> DataService:
    if _data_service is None:
        return DataService(market_data_dir="data/market", custom_data_dir="data/custom")
    return _data_service


def set_download_manager(mgr: DownloadManager) -> None:
    global _download_manager
    _download_manager = mgr


def get_download_manager() -> DownloadManager:
    if _download_manager is None:
        raise HTTPException(status_code=503, detail="Download manager not initialized")
    return _download_manager


class DownloadRequest(BaseModel):
    symbols: list[str]
    date_range_start: date
    date_range_end: date
    provider: str = "polygon"
    data_type: str = "bars"
    timeframe: str = "1min"


@router.get("/market/{symbol}/meta")
async def get_market_data_meta(
    symbol: str,
    timeframe: str = Query("1day"),
    provider: str = Query("polygon"),
    source: Optional[str] = Query(None),
):
    resolved_provider = source or provider
    svc = get_data_service()
    df = svc.load_market_data(resolved_provider, symbol, timeframe)
    if df is None:
        raise HTTPException(status_code=404, detail=f"No data for {resolved_provider}/{symbol}/{timeframe}")
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return {
            "total_bars": len(df),
            "first_timestamp": df["timestamp"].min().isoformat() if len(df) > 0 else None,
            "last_timestamp": df["timestamp"].max().isoformat() if len(df) > 0 else None,
        }
    return {"total_bars": len(df), "first_timestamp": None, "last_timestamp": None}


@router.get("/market/{symbol}")
async def get_market_data(
    symbol: str,
    timeframe: str = Query("1day"),
    provider: str = Query("polygon"),
    source: Optional[str] = Query(None),
    start: Optional[str] = Query(None, description="ISO timestamp — include bars at or after this time"),
    end: Optional[str] = Query(None, description="ISO timestamp — include bars at or before this time"),
    limit: int = Query(5000, description="Maximum number of rows to return (most-recent N after filtering)"),
):
    resolved_provider = source or provider
    svc = get_data_service()
    df = svc.load_market_data(resolved_provider, symbol, timeframe)
    if df is None:
        raise HTTPException(status_code=404, detail=f"No data for {resolved_provider}/{symbol}/{timeframe}")

    # Filter by time window when a timestamp column is present.
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        if start:
            df = df[df["timestamp"] >= pd.Timestamp(start, tz="UTC")]
        if end:
            df = df[df["timestamp"] <= pd.Timestamp(end, tz="UTC")]

    total = len(df)
    # Return the most-recent `limit` rows so the browser sees recent data first.
    df = df.tail(limit)
    return {
        "data": df.to_dict(orient="records"),
        "total": total,
        "truncated": total > limit,
    }


@router.get("/custom/{source_name}")
async def get_custom_data(source_name: str, fmt: str = Query("csv")):
    svc = get_data_service()
    df = svc.load_custom_data(source_name, fmt)
    if df is None:
        raise HTTPException(status_code=404, detail=f"No data for {source_name}")
    return {"data": df.to_dict(orient="records")}


@router.get("/available")
async def list_available():
    svc = get_data_service()
    return svc.list_available_market_data()


@router.get("/sources")
async def list_data_sources(
    type: Optional[str] = Query(None, description="Filter by source type, e.g. 'scraper'"),
    db: AsyncSession = Depends(get_db),
):
    """List DataSource rows (scraper outputs, custom datasets registered via the API)."""
    q = select(DataSource).order_by(DataSource.last_updated.desc().nullslast())
    if type:
        q = q.where(DataSource.type == type)
    rows = (await db.execute(q)).scalars().all()
    return [{
        "id": r.id,
        "type": r.type,
        "source": r.source,
        "name": r.name,
        "description": r.description,
        "file_path": r.file_path,
        "last_updated": to_iso_utc(r.last_updated),
        "metadata": r.metadata_,
    } for r in rows]


@router.post("/downloads", status_code=201)
async def create_download(body: DownloadRequest):
    mgr = get_download_manager()
    try:
        result = await mgr.create_download(
            symbols=body.symbols,
            date_range_start=body.date_range_start,
            date_range_end=body.date_range_end,
            provider=body.provider,
            data_type=body.data_type,
            timeframe=body.timeframe,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/downloads")
async def list_downloads():
    mgr = get_download_manager()
    return await mgr.list_downloads()


@router.get("/downloads/{download_id}")
async def get_download(download_id: str):
    mgr = get_download_manager()
    dl = await mgr.get_download(download_id)
    if dl is None:
        raise HTTPException(status_code=404, detail="Download not found")
    return dl


ACTIVE_STATUSES = {"queued", "running"}


@router.delete("/downloads/{download_id}", status_code=204)
async def delete_download(download_id: str):
    mgr = get_download_manager()
    dl = await mgr.get_download(download_id)
    if dl is None:
        raise HTTPException(status_code=404, detail="Download not found")
    if dl["status"] in ACTIVE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete an active download (status={dl['status']}). Cancel it first."
        )
    deleted = await mgr.delete_download(download_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Download not found")


@router.delete("/downloads")
async def clear_downloads(status: Optional[str] = Query(None)):
    mgr = get_download_manager()
    statuses = None
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
    n = await mgr.clear_downloads(statuses=statuses)
    return {"deleted": n}


@router.post("/downloads/{download_id}/cancel")
async def cancel_download(download_id: str):
    mgr = get_download_manager()
    cancelled = await mgr.cancel_download(download_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Download not found or already completed")
    return {"status": "cancelled"}
