from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.database.models import (
    TradeLog, AlgorithmInstance, Algorithm,
)

router = APIRouter(prefix="/api/trades", tags=["trades"])


def _to_response(trade: TradeLog, algo_name: Optional[str]) -> dict:
    notional = (trade.filled_price or 0.0) * (trade.quantity or 0.0)
    return {
        "id": trade.id,
        "instance_id": trade.instance_id,
        "account_id": trade.account_id,
        "algorithm_name": algo_name,
        "timestamp": trade.timestamp.isoformat() if trade.timestamp else None,
        "symbol": trade.symbol,
        "asset_type": trade.asset_type,
        "side": trade.side,
        "quantity": trade.quantity,
        "filled_price": trade.filled_price,
        "notional": notional,
        "fees": trade.fees,
    }


@router.get("")
async def list_trades(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(TradeLog)
        .order_by(desc(TradeLog.timestamp))
        .limit(limit)
    )
    trades = (await db.execute(query)).scalars().all()

    instance_ids = {t.instance_id for t in trades if t.instance_id}
    algo_names: dict[str, str] = {}
    if instance_ids:
        joined = await db.execute(
            select(AlgorithmInstance.id, Algorithm.name)
            .join(Algorithm, AlgorithmInstance.algorithm_id == Algorithm.id)
            .where(AlgorithmInstance.id.in_(instance_ids))
        )
        algo_names = {row[0]: row[1] for row in joined.all()}

    return {
        "items": [_to_response(t, algo_names.get(t.instance_id) if t.instance_id else None) for t in trades]
    }
