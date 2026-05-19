from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.api.serialization import to_iso_utc
from coordinator.database.models import (
    Algorithm,
    BacktestRun,
    ParameterSet,
    compute_parameter_set_id,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["parameter-sets"])


class ParameterSetCreate(BaseModel):
    name: str
    config_values: dict


class ParameterSetUpdate(BaseModel):
    name: str


class ParameterSetImportBody(BaseModel):
    sets: list[ParameterSetCreate]


def _ps_to_response(ps: ParameterSet, best_backtest: dict | None = None) -> dict:
    return {
        "id": ps.id,
        "algorithm_id": ps.algorithm_id,
        "name": ps.name,
        "config_values": ps.config_values,
        "created_at": to_iso_utc(ps.created_at),
        "updated_at": to_iso_utc(ps.updated_at),
        "best_backtest": best_backtest,
    }


@router.post("/api/algorithms/{algorithm_id}/parameter-sets", status_code=201)
async def create_parameter_set(
    algorithm_id: str, body: ParameterSetCreate, db: AsyncSession = Depends(get_db)
):
    algo = (await db.execute(
        select(Algorithm).where(Algorithm.id == algorithm_id)
    )).scalar_one_or_none()
    if algo is None:
        raise HTTPException(status_code=404, detail="Algorithm not found")

    set_id = compute_parameter_set_id(body.config_values)

    existing = (await db.execute(
        select(ParameterSet).where(
            ParameterSet.algorithm_id == algorithm_id,
            ParameterSet.id == set_id,
        )
    )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="Parameter set with identical values already exists",
        )

    ps = ParameterSet(
        id=set_id,
        algorithm_id=algorithm_id,
        name=body.name,
        config_values=body.config_values,
    )
    db.add(ps)
    await db.flush()
    return _ps_to_response(ps)


@router.get("/api/algorithms/{algorithm_id}/parameter-sets")
async def list_parameter_sets(
    algorithm_id: str, db: AsyncSession = Depends(get_db)
):
    ps_result = await db.execute(
        select(ParameterSet).where(ParameterSet.algorithm_id == algorithm_id)
    )
    sets = ps_result.scalars().all()

    bt_result = await db.execute(
        select(BacktestRun).where(
            BacktestRun.algorithm_id == algorithm_id,
            BacktestRun.status == "completed",
            BacktestRun.parameter_set_id.isnot(None),
        )
    )
    backtests = bt_result.scalars().all()

    bt_by_set: dict[str, list[BacktestRun]] = defaultdict(list)
    for bt in backtests:
        bt_by_set[bt.parameter_set_id].append(bt)

    def _best_backtest(ps_id: str) -> dict | None:
        runs = bt_by_set.get(ps_id)
        if not runs:
            return None
        best = max(
            runs,
            key=lambda r: (r.sharpe_ratio is not None, r.sharpe_ratio or 0.0),
        )
        return {
            "sharpe_ratio": best.sharpe_ratio,
            "total_return": best.total_return,
            "max_drawdown": best.max_drawdown,
            "run_count": len(runs),
        }

    def _sort_key(ps: ParameterSet):
        bb = _best_backtest(ps.id)
        if bb is None:
            return (0, 0.0)
        return (1, bb["sharpe_ratio"] or 0.0)

    sorted_sets = sorted(sets, key=_sort_key, reverse=True)
    return [_ps_to_response(ps, _best_backtest(ps.id)) for ps in sorted_sets]


@router.get("/api/algorithms/{algorithm_id}/parameter-sets/export")
async def export_parameter_sets(
    algorithm_id: str, db: AsyncSession = Depends(get_db)
):
    ps_result = await db.execute(
        select(ParameterSet).where(ParameterSet.algorithm_id == algorithm_id)
    )
    sets = ps_result.scalars().all()
    payload = json.dumps(
        [{"name": ps.name, "config_values": ps.config_values} for ps in sets],
        indent=2,
    )
    return Response(
        content=payload,
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename=parameter-sets-{algorithm_id}.json"
        },
    )


@router.post("/api/algorithms/{algorithm_id}/parameter-sets/import")
async def import_parameter_sets(
    algorithm_id: str,
    body: ParameterSetImportBody,
    db: AsyncSession = Depends(get_db),
):
    algo = (await db.execute(
        select(Algorithm).where(Algorithm.id == algorithm_id)
    )).scalar_one_or_none()
    if algo is None:
        raise HTTPException(status_code=404, detail="Algorithm not found")

    imported = 0
    skipped = 0
    for item in body.sets:
        set_id = compute_parameter_set_id(item.config_values)
        existing = (await db.execute(
            select(ParameterSet).where(
                ParameterSet.algorithm_id == algorithm_id,
                ParameterSet.id == set_id,
            )
        )).scalar_one_or_none()
        if existing is not None:
            skipped += 1
            continue
        ps = ParameterSet(
            id=set_id,
            algorithm_id=algorithm_id,
            name=item.name,
            config_values=item.config_values,
        )
        db.add(ps)
        imported += 1

    await db.flush()
    return {"imported": imported, "skipped": skipped}


@router.get("/api/algorithms/{algorithm_id}/parameter-sets/{set_id}")
async def get_parameter_set(
    algorithm_id: str, set_id: str, db: AsyncSession = Depends(get_db)
):
    ps = (await db.execute(
        select(ParameterSet).where(
            ParameterSet.algorithm_id == algorithm_id,
            ParameterSet.id == set_id,
        )
    )).scalar_one_or_none()
    if ps is None:
        raise HTTPException(status_code=404, detail="Parameter set not found")
    return _ps_to_response(ps)


@router.patch("/api/algorithms/{algorithm_id}/parameter-sets/{set_id}")
async def update_parameter_set(
    algorithm_id: str,
    set_id: str,
    body: ParameterSetUpdate,
    db: AsyncSession = Depends(get_db),
):
    ps = (await db.execute(
        select(ParameterSet).where(
            ParameterSet.algorithm_id == algorithm_id,
            ParameterSet.id == set_id,
        )
    )).scalar_one_or_none()
    if ps is None:
        raise HTTPException(status_code=404, detail="Parameter set not found")
    ps.name = body.name
    return _ps_to_response(ps)


@router.delete("/api/algorithms/{algorithm_id}/parameter-sets/{set_id}", status_code=204)
async def delete_parameter_set(
    algorithm_id: str, set_id: str, db: AsyncSession = Depends(get_db)
):
    ps = (await db.execute(
        select(ParameterSet).where(
            ParameterSet.algorithm_id == algorithm_id,
            ParameterSet.id == set_id,
        )
    )).scalar_one_or_none()
    if ps is None:
        raise HTTPException(status_code=404, detail="Parameter set not found")
    await db.delete(ps)
