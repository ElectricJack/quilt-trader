from fastapi import APIRouter, HTTPException

from coordinator.services.asset_catalog import (
    BROKER_ASSET_TYPES, asset_types_for_broker,
)

router = APIRouter(prefix="/api/brokers", tags=["brokers"])


@router.get("/{broker_type}/asset-types")
async def get_asset_types(broker_type: str):
    if broker_type not in BROKER_ASSET_TYPES:
        raise HTTPException(status_code=404, detail=f"Unknown broker: {broker_type}")
    return {"asset_types": asset_types_for_broker(broker_type)}
