from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db, get_container
from coordinator.database.models import (
    Account,
    AccountCashFlow,
    AccountSnapshot,
    AlgorithmInstance,
    AlgorithmRun,
    BacktestComparison,
    DecisionLog,
    PDTTracking,
    Position,
    TradeLog,
)

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class AccountCreate(BaseModel):
    name: str
    broker_type: str
    credentials: dict
    supported_asset_types: list[str]
    options_level: Optional[int] = None
    account_features: Optional[list[str]] = None
    pdt_mode: str = "off"


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    credentials: Optional[dict] = None
    supported_asset_types: Optional[list[str]] = None
    options_level: Optional[int] = None
    account_features: Optional[list[str]] = None
    pdt_mode: Optional[str] = None


class AccountResponse(BaseModel):
    id: str
    name: str
    broker_type: str
    supported_asset_types: list[str]
    options_level: Optional[int]
    account_features: Optional[list[str]]
    pdt_mode: str
    locked_by: Optional[str]
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


def _to_response(account: Account) -> dict:
    return {
        "id": account.id,
        "name": account.name,
        "broker_type": account.broker_type,
        "supported_asset_types": account.supported_asset_types,
        "options_level": account.options_level,
        "account_features": account.account_features,
        "pdt_mode": account.pdt_mode,
        "locked_by": account.locked_by,
        "created_at": account.created_at.isoformat() if account.created_at else None,
        "updated_at": account.updated_at.isoformat() if account.updated_at else None,
    }


@router.post("", status_code=201)
async def create_account(body: AccountCreate, db: AsyncSession = Depends(get_db)):
    container = get_container()
    encrypted_creds = container.encryption.encrypt_json(body.credentials)
    account = Account(
        name=body.name,
        broker_type=body.broker_type,
        credentials=encrypted_creds,
        supported_asset_types=body.supported_asset_types,
        options_level=body.options_level,
        account_features=body.account_features,
        pdt_mode=body.pdt_mode,
    )
    db.add(account)
    await db.flush()
    return _to_response(account)


@router.get("")
async def list_accounts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account))
    accounts = result.scalars().all()
    return [_to_response(a) for a in accounts]


def _snap_to_dict(snap: "AccountSnapshot") -> dict:
    return {
        "timestamp": snap.timestamp.isoformat(),
        "total_value": snap.total_value,
        "cash": snap.cash,
        "positions_value": snap.positions_value,
    }


@router.get("/snapshots/latest")
async def accounts_snapshots_latest(db: AsyncSession = Depends(get_db)):
    accounts = (await db.execute(select(Account))).scalars().all()
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)

    items = []
    for acct in accounts:
        latest_q = (
            select(AccountSnapshot)
            .where(AccountSnapshot.account_id == acct.id)
            .order_by(AccountSnapshot.timestamp.desc())
            .limit(1)
        )
        latest = (await db.execute(latest_q)).scalar_one_or_none()
        if not latest:
            continue

        prior_q = (
            select(AccountSnapshot)
            .where(AccountSnapshot.account_id == acct.id)
            .where(AccountSnapshot.timestamp <= cutoff_24h)
            .order_by(AccountSnapshot.timestamp.desc())
            .limit(1)
        )
        prior = (await db.execute(prior_q)).scalar_one_or_none()

        day_pct = None
        if prior and prior.total_value:
            day_pct = (latest.total_value - prior.total_value) / prior.total_value * 100.0

        items.append({
            "account_id": acct.id,
            "account_name": acct.name,
            "broker_type": acct.broker_type,
            "latest": _snap_to_dict(latest),
            "prior": _snap_to_dict(prior) if prior else None,
            "day_pct": day_pct,
        })

    return {"items": items}


@router.get("/{account_id}")
async def get_account(account_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return _to_response(account)


@router.patch("/{account_id}")
async def update_account(
    account_id: str, body: AccountUpdate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    if body.name is not None:
        account.name = body.name
    if body.credentials is not None:
        container = get_container()
        account.credentials = container.encryption.encrypt_json(body.credentials)
    if body.supported_asset_types is not None:
        account.supported_asset_types = body.supported_asset_types
    if body.options_level is not None:
        account.options_level = body.options_level
    if body.account_features is not None:
        account.account_features = body.account_features
    if body.pdt_mode is not None:
        account.pdt_mode = body.pdt_mode

    await db.flush()
    return _to_response(account)


@router.delete("/{account_id}", status_code=204)
async def delete_account(account_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    # Collect instance IDs so we can cascade through runs/decisions/comparisons.
    instance_rows = await db.execute(
        select(AlgorithmInstance.id).where(AlgorithmInstance.account_id == account_id)
    )
    instance_ids = [row[0] for row in instance_rows.all()]

    if instance_ids:
        # Null out active_run_id on instances to break the circular FK before deleting runs.
        await db.execute(
            update(AlgorithmInstance)
            .where(AlgorithmInstance.id.in_(instance_ids))
            .values(active_run_id=None)
        )
        await db.execute(
            delete(AlgorithmRun).where(AlgorithmRun.instance_id.in_(instance_ids))
        )
        await db.execute(
            delete(DecisionLog).where(DecisionLog.instance_id.in_(instance_ids))
        )
        await db.execute(
            delete(BacktestComparison).where(BacktestComparison.instance_id.in_(instance_ids))
        )

    # Clear the self-referential locked_by FK before deleting instances.
    account.locked_by = None
    await db.flush()

    if instance_ids:
        await db.execute(
            delete(AlgorithmInstance).where(AlgorithmInstance.account_id == account_id)
        )

    # Delete all other dependent rows.
    await db.execute(
        delete(Position).where(Position.account_id == account_id)
    )
    # pdt_tracking references trade_log.id, so delete it before trade_log.
    await db.execute(
        delete(PDTTracking).where(PDTTracking.account_id == account_id)
    )
    await db.execute(
        delete(TradeLog).where(TradeLog.account_id == account_id)
    )
    await db.execute(
        delete(AccountCashFlow).where(AccountCashFlow.account_id == account_id)
    )
    await db.execute(
        delete(AccountSnapshot).where(AccountSnapshot.account_id == account_id)
    )

    await db.delete(account)
