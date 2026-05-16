from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.api.serialization import to_iso_utc
from coordinator.database.models import AccountCashFlow

router = APIRouter(tags=["cash_flows"])


class CashFlowCreate(BaseModel):
    type: str
    amount: float
    notes: Optional[str] = None


def _to_response(cf: AccountCashFlow) -> dict:
    return {
        "id": cf.id,
        "account_id": cf.account_id,
        "type": cf.type,
        "amount": cf.amount,
        "timestamp": to_iso_utc(cf.timestamp),
        "notes": cf.notes,
    }


@router.get("/api/accounts/{account_id}/cash-flows")
async def list_cash_flows(account_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AccountCashFlow)
        .where(AccountCashFlow.account_id == account_id)
        .order_by(AccountCashFlow.timestamp.desc())
    )
    return [_to_response(cf) for cf in result.scalars().all()]


@router.post("/api/accounts/{account_id}/cash-flows", status_code=201)
async def create_cash_flow(account_id: str, body: CashFlowCreate, db: AsyncSession = Depends(get_db)):
    cf = AccountCashFlow(
        account_id=account_id,
        type=body.type,
        amount=body.amount,
        notes=body.notes,
    )
    db.add(cf)
    await db.flush()
    return _to_response(cf)
