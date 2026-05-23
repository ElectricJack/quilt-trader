import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from coordinator.api.dependencies import get_db, get_container
from coordinator.api.serialization import to_iso_utc
from coordinator.database.models import (
    Account,
    AccountCashFlow,
    AccountEquityDaily,
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
    environment: str = "paper"
    credentials: dict
    supported_asset_types: list[str]
    options_level: Optional[int] = None
    account_features: Optional[list[str]] = None
    pdt_mode: str = "off"
    show_in_overview: bool = True


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    environment: Optional[str] = None
    credentials: Optional[dict] = None
    supported_asset_types: Optional[list[str]] = None
    options_level: Optional[int] = None
    account_features: Optional[list[str]] = None
    pdt_mode: Optional[str] = None
    show_in_overview: Optional[bool] = None


class TestConnectionRequest(BaseModel):
    broker_type: str
    environment: str = "paper"
    credentials: dict


class AccountResponse(BaseModel):
    id: str
    name: str
    broker_type: str
    environment: str
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
        "environment": account.environment,
        "supported_asset_types": account.supported_asset_types,
        "options_level": account.options_level,
        "account_features": account.account_features,
        "pdt_mode": account.pdt_mode,
        "show_in_overview": account.show_in_overview,
        "locked_by": account.locked_by,
        "created_at": to_iso_utc(account.created_at),
        "updated_at": to_iso_utc(account.updated_at),
    }


def _validate_environment(env: str) -> None:
    if env not in ("paper", "live"):
        raise HTTPException(status_code=400, detail=f"environment must be 'paper' or 'live', got {env!r}")


@router.post("", status_code=201)
async def create_account(body: AccountCreate, db: AsyncSession = Depends(get_db)):
    _validate_environment(body.environment)
    container = get_container()
    encrypted_creds = container.encryption.encrypt_json(body.credentials)
    account = Account(
        name=body.name,
        broker_type=body.broker_type,
        environment=body.environment,
        credentials=encrypted_creds,
        supported_asset_types=body.supported_asset_types,
        options_level=body.options_level,
        account_features=body.account_features,
        pdt_mode=body.pdt_mode,
        show_in_overview=body.show_in_overview,
    )
    db.add(account)
    await db.flush()
    lifecycle = getattr(container, "account_lifecycle", None)
    if lifecycle:
        asyncio.create_task(lifecycle.initial_backfill(str(account.id)))
    return _to_response(account)


@router.post("/test-connection")
async def test_connection(body: TestConnectionRequest):
    """Validate credentials against the broker without saving the account."""
    _validate_environment(body.environment)
    # Import here so the API module loads cleanly even if worker deps are unavailable.
    from worker.adapter_factory import CredentialError, make_broker_adapter

    try:
        adapter = make_broker_adapter(body.broker_type, body.environment, body.credentials)
    except CredentialError as e:
        return {"ok": False, "error": str(e)}
    except (ValueError, NotImplementedError) as e:
        return {"ok": False, "error": str(e)}

    def _probe() -> dict:
        return adapter.get_account_info()

    try:
        info = await asyncio.to_thread(_probe)
    except Exception as e:  # noqa: BLE001 — surface broker error to the user
        logger.warning("test-connection failed for %s/%s: %s", body.broker_type, body.environment, e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        close = getattr(adapter, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    return {
        "ok": True,
        "info": {
            "cash": info.get("cash"),
            "portfolio_value": info.get("portfolio_value"),
            "buying_power": info.get("buying_power"),
            "currency": info.get("currency"),
        },
    }


@router.get("")
async def list_accounts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account))
    accounts = result.scalars().all()
    return [_to_response(a) for a in accounts]


def _snap_to_dict(snap: "AccountSnapshot") -> dict:
    return {
        "timestamp": to_iso_utc(snap.timestamp),
        "total_value": snap.total_value,
        "cash": snap.cash,
        "positions_value": snap.positions_value,
    }


@router.get("/snapshots/latest")
async def accounts_snapshots_latest(db: AsyncSession = Depends(get_db)):
    import asyncio
    import json as _json
    from coordinator.database.models import AccountEquityDaily
    from datetime import date as date_type
    from worker.adapter_factory import make_broker_adapter

    accounts = (await db.execute(select(Account))).scalars().all()
    container = get_container()
    yesterday = date_type.today() - timedelta(days=1)

    items = []
    for acct in accounts:
        # Get live broker values
        try:
            creds = _json.loads(container.encryption.decrypt(acct.credentials))
            adapter = make_broker_adapter(acct.broker_type, acct.environment, creds)
            info = await asyncio.to_thread(adapter.get_account_info)
            live_total = float(info.get("portfolio_value", 0))
            live_cash = float(info.get("cash", 0))
            live_positions = live_total - live_cash
        except Exception:
            live_total = 0.0
            live_cash = 0.0
            live_positions = 0.0

        if live_total == 0:
            continue

        # Get most recent materialized close for day % change
        eq_q = (
            select(AccountEquityDaily)
            .where(AccountEquityDaily.account_id == acct.id)
            .order_by(AccountEquityDaily.date.desc())
            .limit(1)
        )
        yesterday_row = (await db.execute(eq_q)).scalar_one_or_none()
        day_pct = None
        prior_dict = None
        if yesterday_row and yesterday_row.total_value > 0:
            day_pct = (live_total - yesterday_row.total_value) / yesterday_row.total_value * 100.0
            prior_dict = {
                "timestamp": yesterday_row.date.isoformat() + "T00:00:00Z",
                "total_value": yesterday_row.total_value,
                "cash": yesterday_row.cash,
                "positions_value": yesterday_row.positions_value,
            }

        now_iso = datetime.now(timezone.utc).isoformat()
        items.append({
            "account_id": acct.id,
            "account_name": acct.name,
            "broker_type": acct.broker_type,
            "latest": {
                "timestamp": now_iso,
                "total_value": live_total,
                "cash": live_cash,
                "positions_value": live_positions,
            },
            "prior": prior_dict,
            "day_pct": day_pct,
        })

    return {"items": items}


async def _adapter_for_account(account: Account):
    """Construct a broker adapter from an Account's decrypted credentials."""
    from worker.adapter_factory import CredentialError, make_broker_adapter

    container = get_container()
    try:
        creds = container.encryption.decrypt_json(account.credentials)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to decrypt credentials: {e}")
    try:
        return make_broker_adapter(account.broker_type, account.environment, creds)
    except CredentialError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (ValueError, NotImplementedError) as e:
        raise HTTPException(status_code=400, detail=str(e))


def _close_adapter(adapter) -> None:
    close = getattr(adapter, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


@router.get("/{account_id}/equity-curve")
async def equity_curve(
    account_id: str,
    since: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Portfolio-value curve over time.

    Strategy:
      1. Try the materialized account_equity_daily table first (daily granularity, accurate).
      2. Fall back to the snapshot + cash-flow interpolation approach for accounts that
         haven't been backfilled yet.
    """
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    since_dt: Optional[datetime] = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid 'since': {since}")

    # --- Primary path: materialized daily equity table ---
    mat_stmt = select(AccountEquityDaily).where(
        AccountEquityDaily.account_id == account_id,
    ).order_by(AccountEquityDaily.date)

    if since_dt is not None:
        mat_stmt = mat_stmt.where(AccountEquityDaily.date >= since_dt.date())

    mat_rows = (await db.execute(mat_stmt)).scalars().all()

    if mat_rows:
        return {
            "items": [
                {
                    "timestamp": r.date.isoformat() + "T00:00:00Z",
                    "value": r.total_value,
                    "positions_value": r.positions_value,
                    "cash": r.cash,
                    "estimated": r.estimated,
                    "source": "estimated" if r.estimated else "materialized",
                }
                for r in mat_rows
            ]
        }

    # --- Fallback: snapshot + cash-flow interpolation ---
    if since_dt is None:
        since_dt = datetime.now(timezone.utc) - timedelta(days=90)

    # Snapshots in range (asc).
    snap_q = (
        select(AccountSnapshot)
        .where(AccountSnapshot.account_id == account_id)
        .where(AccountSnapshot.timestamp >= since_dt)
        .order_by(AccountSnapshot.timestamp.asc())
    )
    snapshots = (await db.execute(snap_q)).scalars().all()

    # Cash flows in range (asc).
    cf_q = (
        select(AccountCashFlow)
        .where(AccountCashFlow.account_id == account_id)
        .where(AccountCashFlow.timestamp >= since_dt)
        .order_by(AccountCashFlow.timestamp.asc())
    )
    cash_flows = (await db.execute(cf_q)).scalars().all()

    INFLOW = {"deposit", "dividend", "interest"}
    OUTFLOW = {"withdrawal", "fee"}

    def cf_delta(cf) -> float:
        """How much the total portfolio value changed at this cash event."""
        amt = float(cf.amount)
        t = (cf.type or "").lower()
        if t in INFLOW:
            return abs(amt)
        if t in OUTFLOW:
            return -abs(amt)
        # Fallback: trust the sign of the amount.
        return amt

    points: list[dict] = []
    # Forward-walk from the earliest anchor: each snapshot is a known truth.
    if snapshots:
        # Find cash flows strictly before the first snapshot — these are pre-anchor.
        first_snap = snapshots[0]
        pre = [cf for cf in cash_flows if cf.timestamp < first_snap.timestamp]
        # Back-calc points before the first snapshot.
        value = float(first_snap.total_value)
        # Walk pre-flows in reverse so we step backward from the snapshot anchor.
        for cf in reversed(pre):
            value -= cf_delta(cf)
            points.append({
                "timestamp": to_iso_utc(cf.timestamp),
                "value": round(value, 2),
                "source": "estimated",
            })
        # Anchor.
        points.append({
            "timestamp": to_iso_utc(first_snap.timestamp),
            "value": float(first_snap.total_value),
            "source": "snapshot",
        })
        # Walk forward through remaining snapshots and cash flows.
        prev_snap_ts = first_snap.timestamp
        prev_value = float(first_snap.total_value)
        for snap in snapshots[1:]:
            # Cash flows between prev_snap_ts and snap.timestamp.
            between = [
                cf for cf in cash_flows
                if prev_snap_ts < cf.timestamp < snap.timestamp
            ]
            # Forward-walk events: each event shifts value by cf_delta(cf).
            for cf in between:
                prev_value += cf_delta(cf)
                points.append({
                    "timestamp": to_iso_utc(cf.timestamp),
                    "value": round(prev_value, 2),
                    "source": "estimated",
                })
            # Snapshot anchor wipes out drift error from missing market movements.
            points.append({
                "timestamp": to_iso_utc(snap.timestamp),
                "value": float(snap.total_value),
                "source": "snapshot",
            })
            prev_snap_ts = snap.timestamp
            prev_value = float(snap.total_value)
        # Cash flows after the last snapshot.
        after = [cf for cf in cash_flows if cf.timestamp > prev_snap_ts]
        for cf in after:
            prev_value += cf_delta(cf)
            points.append({
                "timestamp": to_iso_utc(cf.timestamp),
                "value": round(prev_value, 2),
                "source": "estimated",
            })
    else:
        # No snapshots — back-calc everything from broker live value.
        adapter = await _adapter_for_account(account)

        def _live():
            return adapter.get_account_info()

        try:
            info = await asyncio.to_thread(_live)
        except Exception as e:  # noqa: BLE001
            logger.warning("equity-curve live fetch failed for %s: %s", account_id, e)
            raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
        finally:
            _close_adapter(adapter)

        now = datetime.now(timezone.utc)
        current_value = float(info.get("portfolio_value", 0.0))
        points.append({
            "timestamp": to_iso_utc(now),
            "value": round(current_value, 2),
            "source": "live",
        })
        value = current_value
        for cf in reversed(cash_flows):
            value -= cf_delta(cf)
            points.append({
                "timestamp": to_iso_utc(cf.timestamp),
                "value": round(value, 2),
                "source": "estimated",
            })
    # Always sort ascending so the consumer doesn't have to.
    points.sort(key=lambda p: p["timestamp"])

    return {"items": points}


@router.get("/{account_id}/broker-info")
async def broker_info(account_id: str, db: AsyncSession = Depends(get_db)):
    """Live snapshot of account_info + positions fetched from the broker."""
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    adapter = await _adapter_for_account(account)

    def _fetch():
        return adapter.get_account_info(), adapter.get_positions()

    try:
        info, positions = await asyncio.to_thread(_fetch)
    except Exception as e:  # noqa: BLE001
        logger.warning("broker-info failed for %s: %s", account_id, e)
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
    finally:
        _close_adapter(adapter)

    return {
        "account_info": info,
        "positions": list(positions.values()),
    }


class SyncRequest(BaseModel):
    since: Optional[str] = None  # ISO8601; defaults to last sync or 30d ago


@router.post("/{account_id}/sync")
async def sync_account(
    account_id: str,
    body: Optional[SyncRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """Pull broker transactions since `since` and import fills + cash flows with dedup."""
    from coordinator.database.models import AccountCashFlow, AccountSnapshot, TradeLog

    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    # Determine since.
    since_dt: Optional[datetime] = None
    if body and body.since:
        try:
            since_dt = datetime.fromisoformat(body.since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid 'since' timestamp: {body.since}")
    if since_dt is None:
        # Pick the latest broker-sourced timestamp from either table; fall back to 30 days.
        latest_trade = (await db.execute(
            select(TradeLog.timestamp)
            .where(TradeLog.account_id == account_id)
            .where(TradeLog.broker_txn_id.is_not(None))
            .order_by(TradeLog.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()
        latest_cf = (await db.execute(
            select(AccountCashFlow.timestamp)
            .where(AccountCashFlow.account_id == account_id)
            .where(AccountCashFlow.broker_txn_id.is_not(None))
            .order_by(AccountCashFlow.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()
        candidates = [t for t in (latest_trade, latest_cf) if t is not None]
        since_dt = max(candidates) if candidates else datetime.now(timezone.utc) - timedelta(days=30)

    adapter = await _adapter_for_account(account)

    def _fetch_all():
        txns = adapter.get_transactions(since_dt)
        info = adapter.get_account_info()
        positions = adapter.get_positions()
        return txns, info, positions

    try:
        txns, info, positions = await asyncio.to_thread(_fetch_all)
    except Exception as e:  # noqa: BLE001
        logger.warning("sync failed for %s: %s", account_id, e)
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
    finally:
        _close_adapter(adapter)

    # Dedup against existing broker_txn_ids for this account.
    existing_trade_ids = set(
        (await db.execute(
            select(TradeLog.broker_txn_id)
            .where(TradeLog.account_id == account_id)
            .where(TradeLog.broker_txn_id.is_not(None))
        )).scalars().all()
    )
    existing_cf_ids = set(
        (await db.execute(
            select(AccountCashFlow.broker_txn_id)
            .where(AccountCashFlow.account_id == account_id)
            .where(AccountCashFlow.broker_txn_id.is_not(None))
        )).scalars().all()
    )

    trades_inserted = 0
    cash_flows_inserted = 0
    for txn in txns:
        if txn.type == "fill":
            if txn.broker_id in existing_trade_ids:
                continue
            db.add(TradeLog(
                account_id=account_id,
                source="broker_sync",
                timestamp=txn.timestamp,
                symbol=txn.symbol or "",
                asset_type="equities",
                side=(txn.side or "buy"),
                quantity=float(txn.quantity or 0.0),
                order_type="market",
                filled_price=float(txn.price or 0.0),
                fees=float(txn.fees or 0.0),
                broker_txn_id=txn.broker_id,
                metadata_={"description": txn.description} if txn.description else None,
            ))
            existing_trade_ids.add(txn.broker_id)
            trades_inserted += 1
        elif txn.type in ("deposit", "withdrawal", "dividend", "interest", "fee"):
            if txn.broker_id in existing_cf_ids:
                continue
            db.add(AccountCashFlow(
                account_id=account_id,
                type=txn.type,
                amount=float(txn.amount),
                timestamp=txn.timestamp,
                notes=txn.description,
                broker_txn_id=txn.broker_id,
            ))
            existing_cf_ids.add(txn.broker_id)
            cash_flows_inserted += 1

    # Snapshot the current state so portfolio history is fresh.
    positions_value = float(info.get("portfolio_value", 0.0)) - float(info.get("cash", 0.0))
    db.add(AccountSnapshot(
        account_id=account_id,
        total_value=float(info.get("portfolio_value", 0.0)),
        cash=float(info.get("cash", 0.0)),
        positions_value=positions_value,
        source="broker_sync",
    ))

    await db.flush()

    # Trigger full backfill to rebuild equity curve with latest data
    container = get_container()
    lifecycle = getattr(container, "account_lifecycle", None)
    if lifecycle:
        asyncio.create_task(lifecycle.initial_backfill(account_id))

    return {
        "ok": True,
        "since": to_iso_utc(since_dt),
        "trades_inserted": trades_inserted,
        "cash_flows_inserted": cash_flows_inserted,
        "total_fetched": len(txns),
        "snapshot": {
            "total_value": float(info.get("portfolio_value", 0.0)),
            "cash": float(info.get("cash", 0.0)),
            "positions_value": positions_value,
        },
        "positions_count": len(positions),
    }


@router.post("/{account_id}/backfill")
async def trigger_backfill(account_id: str, db: AsyncSession = Depends(get_db)):
    account = (await db.execute(
        select(Account).where(Account.id == account_id)
    )).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    container = get_container()
    lifecycle = getattr(container, "account_lifecycle", None)
    if not lifecycle:
        raise HTTPException(status_code=503, detail="Lifecycle service not available")
    asyncio.create_task(lifecycle.initial_backfill(account_id))
    return {"ok": True, "message": "Backfill started"}


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
    if body.environment is not None:
        _validate_environment(body.environment)
        account.environment = body.environment
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
    if body.show_in_overview is not None:
        account.show_in_overview = body.show_in_overview

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


class _LegSpecIn(BaseModel):
    symbol: str
    asset_type: str
    side: str
    quantity: float
    expiry: Optional[str] = None
    strike: Optional[float] = None
    right: Optional[str] = None


class OpenPositionRequest(BaseModel):
    legs: list[_LegSpecIn]
    strategy_type: str = "single"
    order_type: str = "market"
    limit_price: Optional[float] = None


class ClosePositionRequest(BaseModel):
    symbol: str
    asset_type: str
    side: str  # "long" or "short" — the *position* side, not the order side
    quantity: float


class ClosePositionByIdRequest(BaseModel):
    order_type: str = "market"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    quantity: Optional[float] = None  # partial close; None = full close


@router.post("/{account_id}/positions/open")
async def open_position(
    account_id: str,
    body: OpenPositionRequest,
    db: AsyncSession = Depends(get_db),
):
    from worker.broker_adapter import MultilegLegSpec
    from coordinator.database.models import Position, TradeLog

    account = (
        await db.execute(select(Account).where(Account.id == account_id))
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.locked_by:
        return Response(
            content=json.dumps({"detail": {"locked_by": account.locked_by}}),
            status_code=423,
            media_type="application/json",
        )

    # Validate asset types vs account
    allowed = set(account.supported_asset_types or [])
    bad = [l.asset_type for l in body.legs if l.asset_type not in allowed]
    if bad:
        raise HTTPException(
            status_code=422,
            detail=f"Asset types not enabled on this account: {sorted(set(bad))}. "
                   f"Allowed: {sorted(allowed)}.",
        )

    # Options legs must have expiry/strike/right
    missing = [
        i for i, l in enumerate(body.legs)
        if l.asset_type == "options" and not (l.expiry and l.strike is not None and l.right)
    ]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Options legs missing expiry/strike/right at indices: {missing}",
        )

    adapter = await _adapter_for_account(account)
    legs_spec = [
        MultilegLegSpec(
            symbol=l.symbol,
            asset_type=l.asset_type,
            side=l.side,
            quantity=l.quantity,
            expiry=l.expiry,
            strike=l.strike,
            right=l.right,
        )
        for l in body.legs
    ]

    try:
        if len(legs_spec) > 1 and adapter.supports_multileg_orders(legs_spec):
            # Atomic path
            def _submit():
                return adapter.submit_multileg_order(
                    legs_spec,
                    order_type=body.order_type,
                    limit_price=body.limit_price,
                )

            try:
                result = await asyncio.to_thread(_submit)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(status_code=422, detail=f"Broker rejected: {e}")

            # Persist
            position = Position(
                account_id=account_id,
                instance_id=None,
                strategy_type=body.strategy_type,
                legs=[
                    {
                        "symbol": l.symbol,
                        "asset_type": l.asset_type,
                        "side": l.side,
                        "quantity": l.quantity,
                        "expiry": l.expiry,
                        "strike": l.strike,
                        "right": l.right,
                        "avg_price": leg_res.filled_price,
                    }
                    for l, leg_res in zip(body.legs, result.legs)
                ],
                status="open",
                net_cost=sum(
                    (leg_res.filled_price or 0.0)
                    * l.quantity
                    * (1 if l.side == "buy" else -1)
                    for l, leg_res in zip(body.legs, result.legs)
                ),
                metadata_={"broker_order_id": result.broker_order_id},
            )
            db.add(position)
            await db.flush()
            for leg, leg_res in zip(body.legs, result.legs):
                db.add(
                    TradeLog(
                        account_id=account_id,
                        source="manual",
                        timestamp=datetime.now(timezone.utc),
                        symbol=leg.symbol,
                        asset_type=leg.asset_type,
                        side=leg.side,
                        quantity=leg.quantity,
                        order_type=body.order_type,
                        filled_price=leg_res.filled_price or 0.0,
                        fees=leg_res.fees or 0.0,
                        broker_txn_id=leg_res.broker_order_id,
                        position_id=position.id,
                    )
                )
            await db.flush()
            return {
                "position_id": position.id,
                "broker_order_id": result.broker_order_id,
                "legs": [
                    {
                        "index": r.index,
                        "status": r.status,
                        "filled_price": r.filled_price,
                        "fees": r.fees,
                        "error": r.error,
                        "broker_order_id": r.broker_order_id,
                    }
                    for r in result.legs
                ],
                "atomic": True,
                "partial_fill": False,
            }
        else:
            # Fallback: sequential per-leg submit_order
            leg_outcomes = []
            filled_legs = []
            for i, leg in enumerate(legs_spec):
                def _sub(leg=leg):
                    return adapter.submit_order(
                        symbol=adapter.compose_symbol(leg),
                        side=leg.side,
                        quantity=leg.quantity,
                        order_type=body.order_type,
                        limit_price=body.limit_price,
                    )

                try:
                    res = await asyncio.to_thread(_sub)
                    leg_outcomes.append(
                        {
                            "index": i,
                            "status": "filled",
                            "filled_price": res.filled_price,
                            "fees": res.fees,
                            "broker_order_id": res.broker_order_id,
                            "error": None,
                        }
                    )
                    filled_legs.append((i, leg, res))
                except Exception as e:  # noqa: BLE001
                    leg_outcomes.append(
                        {
                            "index": i,
                            "status": "rejected",
                            "filled_price": None,
                            "fees": None,
                            "broker_order_id": None,
                            "error": str(e),
                        }
                    )
            partial = (
                any(lo["status"] == "rejected" for lo in leg_outcomes)
                and len(filled_legs) > 0
            )
            position_id = None
            if filled_legs:
                pos = Position(
                    account_id=account_id,
                    instance_id=None,
                    strategy_type=body.strategy_type,
                    legs=[
                        {
                            "symbol": l.symbol,
                            "asset_type": l.asset_type,
                            "side": l.side,
                            "quantity": l.quantity,
                            "expiry": l.expiry,
                            "strike": l.strike,
                            "right": l.right,
                            "avg_price": r.filled_price,
                        }
                        for _, l, r in filled_legs
                    ],
                    status="open",
                    net_cost=sum(
                        r.filled_price * l.quantity * (1 if l.side == "buy" else -1)
                        for _, l, r in filled_legs
                    ),
                    metadata_={"partial_fill": True} if partial else None,
                )
                db.add(pos)
                await db.flush()
                position_id = pos.id
                for _, leg, res in filled_legs:
                    db.add(
                        TradeLog(
                            account_id=account_id,
                            source="manual",
                            timestamp=datetime.now(timezone.utc),
                            symbol=leg.symbol,
                            asset_type=leg.asset_type,
                            side=leg.side,
                            quantity=leg.quantity,
                            order_type=body.order_type,
                            filled_price=res.filled_price,
                            fees=res.fees or 0.0,
                            broker_txn_id=res.broker_order_id,
                            position_id=pos.id,
                        )
                    )
                await db.flush()
            if not filled_legs:
                return Response(
                    content=json.dumps(
                        {
                            "position_id": None,
                            "broker_order_id": None,
                            "legs": leg_outcomes,
                            "atomic": False,
                            "partial_fill": False,
                        }
                    ),
                    media_type="application/json",
                    status_code=422,
                )
            return Response(
                content=json.dumps(
                    {
                        "position_id": position_id,
                        "broker_order_id": None,
                        "legs": leg_outcomes,
                        "atomic": False,
                        "partial_fill": partial,
                    }
                ),
                media_type="application/json",
                status_code=207 if partial else 200,
            )
    finally:
        _close_adapter(adapter)


@router.post("/{account_id}/positions/close")
async def close_position(
    account_id: str,
    body: ClosePositionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Close an open broker position by submitting an opposite-side market order.

    Identifies the position by broker-visible (symbol, side, quantity).
    Does NOT honor the account `locked_by` check — closes must work as a
    safety valve even when an algorithm holds the account lock.
    """
    account = (await db.execute(
        select(Account).where(Account.id == account_id)
    )).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")

    pos_side = body.side.lower()
    if pos_side not in ("long", "short"):
        raise HTTPException(
            status_code=422,
            detail=f"side must be 'long' or 'short', got {body.side!r}",
        )
    order_side = "sell" if pos_side == "long" else "buy"

    adapter = await _adapter_for_account(account)
    try:
        def _sub():
            return adapter.submit_order(
                symbol=body.symbol,
                side=order_side,
                quantity=body.quantity,
                order_type="market",
                asset_type=body.asset_type,
            )
        result = await asyncio.to_thread(_sub)
    except Exception as e:
        _close_adapter(adapter)
        raise HTTPException(status_code=500, detail=str(e))
    else:
        _close_adapter(adapter)

    # If Quilt has an internal Position record for this symbol, mark it closed
    # and write a closing TradeLog row. Multiple matches are allowed (e.g. an
    # algo + a manual position on the same symbol); we close all of them here
    # because the broker treats this as a single net flat.
    matches = (await db.execute(
        select(Position).where(
            Position.account_id == account_id,
            Position.status == "open",
        )
    )).scalars().all()
    matching = [
        p for p in matches
        if any(leg.get("symbol") == body.symbol for leg in (p.legs or []))
    ]
    now = datetime.now(timezone.utc)
    for p in matching:
        p.status = "closed"
        p.closed_at = now
        db.add(TradeLog(
            account_id=account_id,
            position_id=p.id,
            source="manual",
            timestamp=now,
            symbol=body.symbol,
            asset_type=body.asset_type,
            side=order_side,
            quantity=body.quantity,
            order_type="market",
            filled_price=result.filled_price,
            fees=result.fees or 0.0,
            broker_txn_id=result.broker_order_id,
        ))
    await db.flush()
    await db.commit()

    return {
        "broker_order_id": result.broker_order_id,
        "filled_price": result.filled_price,
        "status": "filled" if result.filled_price else "pending",
    }


@router.post("/{account_id}/positions/{position_id}/close")
async def close_position_by_id(
    account_id: str,
    position_id: str,
    body: ClosePositionByIdRequest,
    db: AsyncSession = Depends(get_db),
):
    """Close an existing position by its ID.

    Reads the position's legs, inverts each side (buy->sell, sell->buy),
    and submits via multileg (atomic) or sequential single-leg fallback.
    Does NOT honor the account `locked_by` check — closes must work as a
    safety valve even when an algorithm holds the account lock.
    """
    from worker.broker_adapter import MultilegLegSpec

    account = (
        await db.execute(select(Account).where(Account.id == account_id))
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    position = (
        await db.execute(
            select(Position).where(
                Position.id == position_id,
                Position.account_id == account_id,
            )
        )
    ).scalar_one_or_none()
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")

    if position.status != "open":
        raise HTTPException(
            status_code=409,
            detail=f"Position is already {position.status!r}; cannot close",
        )

    # Validate order-type-specific price fields
    if body.order_type == "limit" and body.limit_price is None:
        raise HTTPException(status_code=422, detail="limit_price is required when order_type is 'limit'")
    if body.order_type == "stop" and body.stop_price is None:
        raise HTTPException(status_code=422, detail="stop_price is required when order_type is 'stop'")

    # Validate partial close quantity against remaining_quantity
    close_quantity = body.quantity  # None means full close
    if close_quantity is not None and position.remaining_quantity is not None:
        if close_quantity > position.remaining_quantity:
            raise HTTPException(
                status_code=422,
                detail=f"Requested quantity {close_quantity} exceeds remaining quantity {position.remaining_quantity}",
            )

    # Build closing legs: invert each side, mark position_intent="close"
    legs_spec: list[MultilegLegSpec] = []
    for leg in (position.legs or []):
        orig_side = leg.get("side", "buy")
        close_side = "sell" if orig_side == "buy" else "buy"
        qty = body.quantity if body.quantity is not None else leg.get("quantity", 0)
        legs_spec.append(
            MultilegLegSpec(
                symbol=leg.get("symbol", ""),
                asset_type=leg.get("asset_type", "equities"),
                side=close_side,
                quantity=qty,
                expiry=leg.get("expiry"),
                strike=leg.get("strike"),
                right=leg.get("right"),
                position_intent="close",
            )
        )

    if not legs_spec:
        raise HTTPException(status_code=422, detail="Position has no legs")

    adapter = await _adapter_for_account(account)
    now = datetime.now(timezone.utc)
    try:
        if len(legs_spec) > 1 and adapter.supports_multileg_orders(legs_spec):
            # Atomic multileg path
            def _submit():
                return adapter.submit_multileg_order(
                    legs_spec,
                    order_type=body.order_type,
                    limit_price=body.limit_price,
                )

            try:
                result = await asyncio.to_thread(_submit)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(status_code=422, detail=f"Broker rejected: {e}")

            # Update position
            total_proceeds = 0.0
            total_fees = 0.0
            for leg, leg_res in zip(legs_spec, result.legs):
                filled = leg_res.filled_price or 0.0
                fees = leg_res.fees or 0.0
                total_proceeds += filled * leg.quantity * (1 if leg.side == "sell" else -1)
                total_fees += fees
                db.add(
                    TradeLog(
                        account_id=account_id,
                        position_id=position.id,
                        source="manual",
                        timestamp=now,
                        symbol=leg.symbol,
                        asset_type=leg.asset_type,
                        side=leg.side,
                        quantity=leg.quantity,
                        order_type=body.order_type,
                        filled_price=filled,
                        fees=fees,
                        broker_txn_id=leg_res.broker_order_id,
                    )
                )

            # Update remaining_quantity and determine close status
            close_qty = close_quantity if close_quantity is not None else (position.remaining_quantity or 0)
            if close_quantity is not None and position.remaining_quantity is not None:
                position.remaining_quantity -= close_quantity
                if position.remaining_quantity <= 0:
                    position.status = "closed"
                    position.closed_at = now
                # else: keep status as "open" for partial close
            else:
                # Full close
                position.remaining_quantity = 0
                position.status = "closed"
                position.closed_at = now

            position.net_proceeds = total_proceeds
            position.total_fees = (position.total_fees or 0.0) + total_fees
            position.net_pnl = total_proceeds - (position.net_cost or 0.0)

            # Record cost basis lot for the close
            filled_prices = [lr.filled_price or 0.0 for lr in result.legs]
            fill_price = filled_prices[0] if len(filled_prices) == 1 else (
                sum(fp * ls.quantity for fp, ls in zip(filled_prices, legs_spec))
                / sum(ls.quantity for ls in legs_spec)
                if legs_spec else 0.0
            )
            lots = list(position.cost_basis_lots or [])
            lots.append({
                "quantity": close_qty,
                "fill_price": fill_price,
                "timestamp": now.isoformat(),
                "type": "close",
            })
            position.cost_basis_lots = lots

            await db.flush()

            return {
                "position_id": position.id,
                "broker_order_id": result.broker_order_id,
                "legs": [
                    {
                        "index": r.index,
                        "status": r.status,
                        "filled_price": r.filled_price,
                        "fees": r.fees,
                        "error": r.error,
                        "broker_order_id": r.broker_order_id,
                    }
                    for r in result.legs
                ],
                "atomic": True,
                "partial_fill": False,
            }
        else:
            # Sequential single-leg fallback
            leg_outcomes = []
            filled_legs = []
            for i, leg in enumerate(legs_spec):
                def _sub(leg=leg):
                    return adapter.submit_order(
                        symbol=adapter.compose_symbol(leg),
                        side=leg.side,
                        quantity=leg.quantity,
                        order_type=body.order_type,
                        limit_price=body.limit_price,
                        stop_price=body.stop_price,
                        asset_type=leg.asset_type,
                    )

                try:
                    res = await asyncio.to_thread(_sub)
                    leg_outcomes.append(
                        {
                            "index": i,
                            "status": "filled",
                            "filled_price": res.filled_price,
                            "fees": res.fees,
                            "broker_order_id": res.broker_order_id,
                            "error": None,
                        }
                    )
                    filled_legs.append((i, leg, res))
                except Exception as e:  # noqa: BLE001
                    leg_outcomes.append(
                        {
                            "index": i,
                            "status": "rejected",
                            "filled_price": None,
                            "fees": None,
                            "broker_order_id": None,
                            "error": str(e),
                        }
                    )

            partial = (
                any(lo["status"] == "rejected" for lo in leg_outcomes)
                and len(filled_legs) > 0
            )

            if filled_legs:
                total_proceeds = 0.0
                total_fees = 0.0
                for _, leg, res in filled_legs:
                    filled = res.filled_price or 0.0
                    fees = res.fees or 0.0
                    total_proceeds += filled * leg.quantity * (1 if leg.side == "sell" else -1)
                    total_fees += fees
                    db.add(
                        TradeLog(
                            account_id=account_id,
                            position_id=position.id,
                            source="manual",
                            timestamp=now,
                            symbol=leg.symbol,
                            asset_type=leg.asset_type,
                            side=leg.side,
                            quantity=leg.quantity,
                            order_type=body.order_type,
                            filled_price=filled,
                            fees=fees,
                            broker_txn_id=res.broker_order_id,
                        )
                    )

                # Update remaining_quantity and determine close status
                close_qty = close_quantity if close_quantity is not None else (position.remaining_quantity or 0)

                # If any leg returned filled_price=None, order is pending (limit/stop not yet filled)
                if any(lo.get("filled_price") is None for lo in leg_outcomes if lo.get("status") != "rejected"):
                    position.status = "closing"
                    # Don't decrement remaining_quantity until fill confirmed
                elif not partial:
                    if close_quantity is not None and position.remaining_quantity is not None:
                        position.remaining_quantity -= close_quantity
                        if position.remaining_quantity <= 0:
                            position.status = "closed"
                            position.closed_at = now
                        # else: keep status as "open" for partial close
                    else:
                        # Full close
                        position.remaining_quantity = 0
                        position.status = "closed"
                        position.closed_at = now
                position.net_proceeds = total_proceeds
                position.total_fees = (position.total_fees or 0.0) + total_fees
                position.net_pnl = total_proceeds - (position.net_cost or 0.0)

                # Record cost basis lot for the close
                filled_prices = [res.filled_price or 0.0 for _, _, res in filled_legs]
                fill_price = filled_prices[0] if len(filled_prices) == 1 else (
                    sum(fp * leg.quantity for fp, (_, leg, _) in zip(filled_prices, filled_legs))
                    / sum(leg.quantity for _, leg, _ in filled_legs)
                    if filled_legs else 0.0
                )
                lots = list(position.cost_basis_lots or [])
                lots.append({
                    "quantity": close_qty,
                    "fill_price": fill_price,
                    "timestamp": now.isoformat(),
                    "type": "close",
                })
                position.cost_basis_lots = lots

                await db.flush()

            if not filled_legs:
                return Response(
                    content=json.dumps(
                        {
                            "position_id": position.id,
                            "broker_order_id": None,
                            "legs": leg_outcomes,
                            "atomic": False,
                            "partial_fill": False,
                        }
                    ),
                    media_type="application/json",
                    status_code=422,
                )
            return Response(
                content=json.dumps(
                    {
                        "position_id": position.id,
                        "broker_order_id": None,
                        "legs": leg_outcomes,
                        "atomic": False,
                        "partial_fill": partial,
                    }
                ),
                media_type="application/json",
                status_code=207 if partial else 200,
            )
    finally:
        _close_adapter(adapter)


@router.get("/{account_id}/positions/reconcile")
async def reconcile_positions(
    account_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Compare broker positions against Quilt's internal position records."""
    from coordinator.services.position_reconciler import PositionReconciler

    account = (await db.execute(
        select(Account).where(Account.id == account_id)
    )).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    adapter = await _adapter_for_account(account)
    try:
        broker_positions = await asyncio.to_thread(adapter.get_positions)
    finally:
        _close_adapter(adapter)

    db_positions_rows = (await db.execute(
        select(Position).where(
            Position.account_id == account_id,
            Position.status == "open",
        )
    )).scalars().all()
    db_positions = [
        {"id": p.id, "legs": p.legs, "status": p.status, "account_id": p.account_id}
        for p in db_positions_rows
    ]

    result = PositionReconciler.reconcile(broker_positions, db_positions)
    return {
        "matched": result.matched,
        "untracked": result.untracked,
        "stale": result.stale,
        "mismatched": result.mismatched,
    }
