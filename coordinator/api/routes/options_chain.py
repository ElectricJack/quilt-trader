import asyncio
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.api.serialization import to_iso_utc
from coordinator.database.models import Account
from coordinator.api.routes.accounts import _adapter_for_account, _close_adapter

router = APIRouter(prefix="/api/accounts/{account_id}/options-chain", tags=["options-chain"])


async def _check_lock_and_get_account(account_id: str, db: AsyncSession) -> Account:
    a = (await db.execute(select(Account).where(Account.id == account_id))).scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if a.locked_by:
        raise HTTPException(status_code=423,
                            detail={"locked_by": a.locked_by})
    from coordinator.services.asset_services import AssetType
    if AssetType.OPTIONS.value not in (a.supported_asset_types or []):
        raise HTTPException(status_code=422,
                            detail="Account does not support options")
    return a


@router.get("/expiries")
async def list_expiries(
    account_id: str,
    underlying: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    account = await _check_lock_and_get_account(account_id, db)
    adapter = await _adapter_for_account(account)
    try:
        expiries = await asyncio.to_thread(adapter.list_option_expiries, underlying)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Broker error: {e}")
    finally:
        _close_adapter(adapter)
    return {"expiries": [d.isoformat() for d in expiries]}


@router.get("")
async def get_chain_matrix(
    account_id: str,
    underlying: str = Query(...),
    max_expiries: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Return the full strike × expiry matrix in one call.

    Fans out per-expiry chain fetches in parallel (capped at ``max_expiries``).
    Used by the strategy builder's matrix view.
    """
    account = await _check_lock_and_get_account(account_id, db)
    adapter = await _adapter_for_account(account)
    try:
        all_expiries = await asyncio.to_thread(adapter.list_option_expiries, underlying)
        all_expiries = sorted(all_expiries)[:max_expiries]

        async def _fetch(exp_date):
            try:
                return await asyncio.to_thread(adapter.get_option_chain, underlying, exp_date)
            except Exception:
                return None

        snaps = await asyncio.gather(*[_fetch(d) for d in all_expiries])
    except Exception as e:  # noqa: BLE001
        _close_adapter(adapter)
        raise HTTPException(status_code=502, detail=f"Broker error: {e}")
    finally:
        _close_adapter(adapter)

    expiries_out = []
    spot = None
    for snap in snaps:
        if snap is None:
            continue
        if spot is None:
            spot = snap.spot
        expiries_out.append({
            "expiry": snap.expiry.isoformat(),
            "contracts": [{
                "strike": c.strike, "right": c.right, "occ_symbol": c.occ_symbol,
                "bid": c.bid, "ask": c.ask, "last": c.last, "iv": c.iv,
                "delta": c.delta, "gamma": c.gamma, "theta": c.theta, "vega": c.vega,
                "open_interest": c.open_interest, "volume": c.volume,
            } for c in snap.contracts],
        })

    return {
        "underlying": underlying,
        "spot": spot,
        "expiries": expiries_out,
    }


@router.get("/{expiry}")
async def get_chain(
    account_id: str, expiry: str,
    underlying: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        exp_date = date.fromisoformat(expiry)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid expiry: {expiry}")
    account = await _check_lock_and_get_account(account_id, db)
    adapter = await _adapter_for_account(account)
    try:
        snap = await asyncio.to_thread(adapter.get_option_chain, underlying, exp_date)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Broker error: {e}")
    finally:
        _close_adapter(adapter)
    return {
        "underlying": snap.underlying,
        "spot": snap.spot,
        "expiry": snap.expiry.isoformat(),
        "as_of": to_iso_utc(snap.as_of),
        "contracts": [{
            "strike": c.strike, "right": c.right, "occ_symbol": c.occ_symbol,
            "bid": c.bid, "ask": c.ask, "last": c.last, "iv": c.iv,
            "delta": c.delta, "gamma": c.gamma, "theta": c.theta, "vega": c.vega,
            "open_interest": c.open_interest, "volume": c.volume,
        } for c in snap.contracts],
    }
