import asyncio
import json as _json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_container, get_db
from coordinator.api.serialization import to_iso_utc
from coordinator.database.models import (
    Account, Position, AlgorithmInstance, Algorithm,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/positions", tags=["positions"])


def _expand(pos: Position, algo_name: Optional[str]) -> dict:
    legs = pos.legs or []
    first = legs[0] if legs else {}
    extra_legs = max(0, len(legs) - 1)
    return {
        "id": pos.id,
        "instance_id": pos.instance_id,
        "account_id": pos.account_id,
        "algorithm_name": algo_name,
        "status": pos.status,
        "symbol": first.get("symbol"),
        "side": first.get("side"),
        "quantity": first.get("quantity"),
        "avg_price": first.get("avg_price"),
        "current_price": first.get("current_price"),
        "asset_type": first.get("asset_type"),
        "unrealized_pnl": pos.unrealized_pnl,
        "net_pnl": pos.net_pnl,
        "net_cost": pos.net_cost,
        "extra_legs": extra_legs,
        "opened_at": to_iso_utc(pos.opened_at),
    }


async def _fetch_broker_positions(db: AsyncSession) -> list[dict]:
    """Fetch live positions from all visible broker accounts."""
    from worker.adapter_factory import make_broker_adapter
    container = get_container()

    accounts = (await db.execute(
        select(Account).where(Account.show_in_overview == True)  # noqa: E712
    )).scalars().all()

    items = []
    for acct in accounts:
        try:
            creds = _json.loads(container.encryption.decrypt(acct.credentials))
            adapter = make_broker_adapter(acct.broker_type, acct.environment, creds)
            positions = await asyncio.to_thread(adapter.get_positions)
            for sym, pos in positions.items():
                from coordinator.services.asset_services import get_default_registry
                registry = get_default_registry()
                items.append({
                    "id": f"{acct.id}:{sym}",
                    "instance_id": None,
                    "account_id": acct.id,
                    "algorithm_name": None,
                    "status": "open",
                    "symbol": sym,
                    "side": "long" if float(pos.get("quantity", 0)) > 0 else "short",
                    "quantity": float(pos.get("quantity", 0)),
                    "avg_price": float(pos.get("avg_price", 0)),
                    "current_price": float(pos.get("current_price", 0)),
                    "asset_type": registry.classify(sym).value,
                    "unrealized_pnl": float(pos.get("unrealized_pnl", 0)),
                    "net_pnl": None,
                    "net_cost": float(pos.get("avg_price", 0)) * float(pos.get("quantity", 0)),
                    "extra_legs": 0,
                    "opened_at": None,
                })
        except Exception:
            logger.warning("Failed to fetch positions for %s", acct.name, exc_info=True)
    return items


@router.get("")
async def list_positions(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    query = select(Position)
    if status:
        query = query.where(Position.status == status)
    query = query.order_by(Position.opened_at.desc()).limit(limit)
    db_positions = (await db.execute(query)).scalars().all()

    # Resolve algorithm names via instance_id for DB-tracked positions.
    instance_ids = {p.instance_id for p in db_positions if p.instance_id}
    algo_names: dict[str, str] = {}
    if instance_ids:
        joined = await db.execute(
            select(AlgorithmInstance.id, Algorithm.name)
            .join(Algorithm, AlgorithmInstance.algorithm_id == Algorithm.id)
            .where(AlgorithmInstance.id.in_(instance_ids))
        )
        algo_names = {row[0]: row[1] for row in joined.all()}

    db_items = [
        _expand(p, algo_names.get(p.instance_id) if p.instance_id else None)
        for p in db_positions
    ]

    # For open-position views, always merge in live broker positions so
    # things the user holds at the broker (but isn't tracked by an
    # algorithm) still surface. DB rows win on (account_id, symbol)
    # because they carry algorithm metadata.
    if status is None or status == "open":
        broker_items = await _fetch_broker_positions(db)
        seen_keys = {(it["account_id"], it["symbol"]) for it in db_items}
        merged = list(db_items)
        for bi in broker_items:
            if (bi["account_id"], bi["symbol"]) in seen_keys:
                continue
            merged.append(bi)
        return {"items": merged[:limit]}

    return {"items": db_items}
