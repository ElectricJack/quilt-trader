from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

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
    timeframe: str = "1day"


@router.get("/market/{symbol}")
async def get_market_data(symbol: str, timeframe: str = Query("1day"), provider: str = Query("polygon")):
    svc = get_data_service()
    df = svc.load_market_data(provider, symbol, timeframe)
    if df is None:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}/{timeframe}")
    return {"data": df.to_dict(orient="records")}


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


@router.post("/downloads/{download_id}/cancel")
async def cancel_download(download_id: str):
    mgr = get_download_manager()
    cancelled = await mgr.cancel_download(download_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Download not found or already completed")
    return {"status": "cancelled"}
