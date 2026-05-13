from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from coordinator.services.data_service import DataService

router = APIRouter(prefix="/api/data", tags=["data"])

_data_service: Optional[DataService] = None


def set_data_service(svc: DataService) -> None:
    global _data_service
    _data_service = svc


def get_data_service() -> DataService:
    if _data_service is None:
        return DataService(market_data_dir="data/market", custom_data_dir="data/custom")
    return _data_service


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
