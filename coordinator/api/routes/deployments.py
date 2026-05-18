"""Public 'deployments' API — the user-facing name for AlgorithmInstance.

Wraps the existing instance model and joins in algorithm/account/worker names
so the frontend never has to display GUIDs. The original /api/instances/*
routes still exist for one release for backwards compatibility.
"""
from __future__ import annotations

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db, get_container
from coordinator.api.websocket import manager as ws_manager
from coordinator.api.serialization import to_iso_utc
from coordinator.database.models import (
    Account, Algorithm, AlgorithmDeploymentReport, AlgorithmInstance, AlgorithmRun, Worker,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/deployments", tags=["deployments"])


def _load_manifest_dict(algo) -> dict:
    """Read the algorithm's manifest YAML from disk as a plain dict.

    Workers parse this on bring-up. We send the raw YAML structure rather
    than the QuiltManifest dataclass so worker code doesn't have to depend
    on sdk.manifest internals.
    """
    import re as _re
    from pathlib import Path
    import yaml
    m = _re.match(r"^https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", algo.repo_url or "")
    if not m:
        raise HTTPException(status_code=500, detail=f"Cannot derive package dir from {algo.repo_url!r}")
    pkg_dir = m.group(1).split("/", 1)[1]
    path = Path("data/packages") / pkg_dir / "quilt.yaml"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Manifest not on disk for algorithm {algo.id}")
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _deployment_to_response(
    inst: AlgorithmInstance,
    algo_name: str,
    account_name: str,
    worker_name: str,
) -> dict:
    return {
        "id": inst.id,
        "algorithm_id": inst.algorithm_id,
        "account_id": inst.account_id,
        "worker_id": inst.worker_id,
        "algorithm_name": algo_name,
        "account_name": account_name,
        "worker_name": worker_name,
        "status": inst.status,
        "active_run_id": inst.active_run_id,
        "config_values": inst.config_values,
        "lifetime_metrics": inst.lifetime_metrics,
        "created_at": to_iso_utc(inst.created_at),
        "updated_at": to_iso_utc(inst.updated_at),
    }


class DeploymentUpdate(BaseModel):
    config_values: Optional[dict] = None


@router.get("")
async def list_deployments(
    algorithm_id: Optional[str] = None,
    worker_id: Optional[str] = None,
    account_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(AlgorithmInstance, Algorithm.name, Account.name, Worker.name)
        .join(Algorithm, AlgorithmInstance.algorithm_id == Algorithm.id)
        .join(Account, AlgorithmInstance.account_id == Account.id)
        .join(Worker, AlgorithmInstance.worker_id == Worker.id)
    )
    if algorithm_id:
        stmt = stmt.where(AlgorithmInstance.algorithm_id == algorithm_id)
    if worker_id:
        stmt = stmt.where(AlgorithmInstance.worker_id == worker_id)
    if account_id:
        stmt = stmt.where(AlgorithmInstance.account_id == account_id)
    rows = (await db.execute(stmt)).all()
    return [_deployment_to_response(inst, a, ac, w) for inst, a, ac, w in rows]


async def _broadcast_status_changed(deployment_id: str, status: str, active_run_id: Optional[str]) -> None:
    await ws_manager.broadcast_to_dashboards({
        "type": "deployment_status_changed",
        "deployment_id": deployment_id,
        "status": status,
        "active_run_id": active_run_id,
    })


@router.post("/{deployment_id}/start")
async def start_deployment(deployment_id: str, db: AsyncSession = Depends(get_db)):
    inst = (await db.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.id == deployment_id)
    )).scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    if inst.status not in ("stopped", "error"):
        raise HTTPException(status_code=409, detail=f"Cannot start deployment in status {inst.status!r}")

    worker_ws = ws_manager.worker_connections.get(inst.worker_id)
    if worker_ws is None:
        raise HTTPException(status_code=502, detail="Worker offline")

    next_n = (await db.execute(
        select(func.coalesce(func.max(AlgorithmRun.run_number), 0))
        .where(AlgorithmRun.instance_id == inst.id)
    )).scalar_one() + 1
    run = AlgorithmRun(instance_id=inst.id, run_number=next_n, status="running")
    db.add(run)
    await db.flush()

    inst.status = "starting"
    inst.active_run_id = run.id
    await db.commit()

    await _broadcast_status_changed(inst.id, "starting", run.id)

    # Load Algorithm + Account for the enriched payload.
    algo = (await db.execute(
        select(Algorithm).where(Algorithm.id == inst.algorithm_id)
    )).scalar_one()
    account = (await db.execute(
        select(Account).where(Account.id == inst.account_id)
    )).scalar_one()

    # Decrypt credentials.
    import json as _json
    encryption = get_container().encryption
    try:
        creds = _json.loads(encryption.decrypt(account.credentials))
    except Exception:
        inst.status = "error"
        run.status = "error"
        await db.commit()
        await _broadcast_status_changed(inst.id, "error", run.id)
        raise HTTPException(status_code=500, detail="Failed to decrypt account credentials")

    # Load manifest YAML for the worker.
    manifest_dict = _load_manifest_dict(algo)

    payload = {
        "type": "start_instance",
        "instance_id": inst.id,
        "run_id": run.id,
        "algorithm_id": algo.id,
        "algorithm_commit_sha": algo.commit_hash,
        "manifest": manifest_dict,
        "broker_type": account.broker_type,
        "environment": account.environment,
        "credentials": creds,
        "config": inst.config_values or {},
        "persisted_state": inst.persisted_state,
    }
    try:
        await worker_ws.send_json(payload)
    except Exception:
        inst.status = "error"
        run.status = "error"
        await db.commit()
        await _broadcast_status_changed(inst.id, "error", run.id)
        raise HTTPException(status_code=502, detail="Failed to reach worker")

    container = get_container()
    if getattr(container, "tick_scheduler", None) is not None:
        try:
            await container.tick_scheduler.start_instance({
                "instance_id": inst.id,
                "run_id": run.id,
                "worker_id": inst.worker_id,
                "broker_type": account.broker_type,
                "asset_type": (account.supported_asset_types or ["equities"])[0],
                "trigger": manifest_dict.get("trigger", "bar:1min"),
                "symbols": (manifest_dict.get("requirements") or {}).get("data_dependencies") or [],
            })
        except Exception:
            logger.exception("Failed to register instance with TickScheduler")

    return {"ok": True, "active_run_id": run.id}


@router.post("/{deployment_id}/stop")
async def stop_deployment(deployment_id: str, db: AsyncSession = Depends(get_db)):
    inst = (await db.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.id == deployment_id)
    )).scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    if inst.status not in ("running", "starting"):
        raise HTTPException(status_code=409, detail=f"Cannot stop deployment in status {inst.status!r}")

    worker_ws = ws_manager.worker_connections.get(inst.worker_id)
    inst.status = "stopping"
    await db.commit()
    await _broadcast_status_changed(inst.id, "stopping", inst.active_run_id)
    if worker_ws is not None:
        try:
            await worker_ws.send_json({"type": "stop_instance", "instance_id": inst.id})
        except Exception:
            pass

    container = get_container()
    if getattr(container, "tick_scheduler", None) is not None:
        try:
            await container.tick_scheduler.stop_instance(deployment_id)
        except Exception:
            logger.exception("Failed to unregister instance from TickScheduler")

    return {"ok": True}


@router.get("/{deployment_id}/runs")
async def list_runs(deployment_id: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(AlgorithmRun)
        .where(AlgorithmRun.instance_id == deployment_id)
        .order_by(AlgorithmRun.run_number.desc())
    )).scalars().all()
    return [
        {
            "id": r.id,
            "run_number": r.run_number,
            "status": r.status,
            "started_at": to_iso_utc(r.started_at),
            "stopped_at": to_iso_utc(r.stopped_at),
            "starting_equity": r.starting_equity,
            "ending_equity": r.ending_equity,
            "net_pnl": r.net_pnl,
            "unrealized_pnl": r.unrealized_pnl,
            "total_fees": r.total_fees,
            "total_slippage": r.total_slippage,
            "trade_count": r.trade_count,
            "metrics": r.metrics,
        }
        for r in rows
    ]


SEVERITY_ORDER = {"debug": 0, "info": 1, "warn": 2, "error": 3}


@router.get("/{deployment_id}/activity")
async def list_deployment_activity(
    deployment_id: str,
    limit: int = 100,
    before: Optional[str] = None,
    severity: str = "info",
    event_types: Optional[str] = None,
    kind: str = "all",
    db: AsyncSession = Depends(get_db),
):
    from coordinator.database.models import WorkerActivity
    from datetime import datetime

    limit = max(1, min(500, limit))
    min_sev = SEVERITY_ORDER.get(severity, 1)
    allowed_sev = [s for s, n in SEVERITY_ORDER.items() if n >= min_sev]

    stmt = (
        select(WorkerActivity)
        .where(WorkerActivity.instance_id == deployment_id)
        .where(WorkerActivity.severity.in_(allowed_sev))
    )
    if kind in ("event", "log"):
        stmt = stmt.where(WorkerActivity.kind == kind)
    if event_types:
        stmt = stmt.where(WorkerActivity.event_type.in_(event_types.split(",")))
    if before:
        try:
            before_dt = datetime.fromisoformat(before.replace("Z", "+00:00"))
            stmt = stmt.where(WorkerActivity.timestamp < before_dt)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid `before`")
    stmt = stmt.order_by(WorkerActivity.timestamp.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "id": r.id,
                "worker_id": r.worker_id,
                "instance_id": r.instance_id,
                "timestamp": to_iso_utc(r.timestamp),
                "kind": r.kind,
                "event_type": r.event_type,
                "severity": r.severity,
                "logger_name": r.logger_name,
                "message": r.message,
                "payload": r.payload,
            }
            for r in rows
        ]
    }


@router.get("/{deployment_id}/report")
async def get_report(deployment_id: str, db: AsyncSession = Depends(get_db)):
    rep = (await db.execute(
        select(AlgorithmDeploymentReport).where(
            AlgorithmDeploymentReport.deployment_id == deployment_id
        )
    )).scalar_one_or_none()
    if rep is None:
        raise HTTPException(
            status_code=404,
            detail="No report yet — deployment has not produced samples",
        )
    return {
        "deployment_id": rep.deployment_id,
        "generated_at": to_iso_utc(rep.generated_at),
        "total_return": rep.total_return,
        "cagr": rep.cagr,
        "volatility": rep.volatility,
        "sharpe_ratio": rep.sharpe_ratio,
        "sortino_ratio": rep.sortino_ratio,
        "calmar_ratio": rep.calmar_ratio,
        "max_drawdown": rep.max_drawdown,
        "romad": rep.romad,
        "trade_count": rep.trade_count,
        "win_rate": rep.win_rate,
        "profit_factor": rep.profit_factor,
        "avg_win": rep.avg_win,
        "avg_loss": rep.avg_loss,
        "expectancy": rep.expectancy,
        "longest_drawdown_days": rep.longest_drawdown_days,
        "equity_curve": rep.equity_curve,
        "drawdown_curve": rep.drawdown_curve,
        "drawdown_periods": rep.drawdown_periods,
        "key_metrics": rep.key_metrics,
        "rolling_metrics": rep.rolling_metrics,
        "monthly_returns_matrix": rep.monthly_returns_matrix,
        "eoy_returns": rep.eoy_returns,
        "runs_index": rep.runs_index,
    }


@router.get("/{deployment_id}/trades")
async def list_deployment_trades(
    deployment_id: str,
    limit: int = 500,
    offset: int = 0,
    run_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    from coordinator.database.models import TradeLog
    limit = max(1, min(1000, limit))
    stmt = (
        select(TradeLog)
        .where(TradeLog.instance_id == deployment_id)
        .order_by(TradeLog.timestamp.desc())
        .limit(limit)
        .offset(offset)
    )
    # run_id filter: TradeLog has no run_id column, so we filter by timestamp
    # boundaries of the run (started_at / stopped_at).
    if run_id:
        from coordinator.database.models import AlgorithmRun
        run = (await db.execute(
            select(AlgorithmRun).where(AlgorithmRun.id == run_id)
        )).scalar_one_or_none()
        if run is not None and run.started_at is not None:
            stmt = stmt.where(TradeLog.timestamp >= run.started_at)
            if run.stopped_at is not None:
                stmt = stmt.where(TradeLog.timestamp < run.stopped_at)

    rows = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "id": t.id,
                "timestamp": to_iso_utc(t.timestamp),
                "symbol": t.symbol,
                "asset_type": t.asset_type,
                "side": t.side,
                "quantity": t.quantity,
                "requested_price": t.requested_price,
                "fill_price": t.filled_price,
                "fees": t.fees,
                "slippage_dollars": t.slippage,
                "realized_pnl": None,  # TradeLog doesn't track realized_pnl
            }
            for t in rows
        ]
    }


@router.get("/{deployment_id}")
async def get_deployment(deployment_id: str, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(AlgorithmInstance, Algorithm.name, Account.name, Worker.name)
        .join(Algorithm, AlgorithmInstance.algorithm_id == Algorithm.id)
        .join(Account, AlgorithmInstance.account_id == Account.id)
        .join(Worker, AlgorithmInstance.worker_id == Worker.id)
        .where(AlgorithmInstance.id == deployment_id)
    )
    row = (await db.execute(stmt)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    inst, a, ac, w = row
    return _deployment_to_response(inst, a, ac, w)


@router.patch("/{deployment_id}")
async def update_deployment(
    deployment_id: str, body: DeploymentUpdate, db: AsyncSession = Depends(get_db),
):
    inst = (await db.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.id == deployment_id)
    )).scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    if body.config_values is not None:
        inst.config_values = body.config_values
    await db.flush()
    return {"ok": True}


@router.delete("/{deployment_id}", status_code=204)
async def delete_deployment(deployment_id: str, db: AsyncSession = Depends(get_db)):
    inst = (await db.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.id == deployment_id)
    )).scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    # active_run_id FKs to algorithm_runs.id; null it before cascading the runs
    # so SQLite doesn't see a transient self-reference to a deleted row.
    inst.active_run_id = None
    await db.flush()
    await db.delete(inst)
    # Flush here so any FK/integrity errors surface as a 500 BEFORE FastAPI
    # sends the 204 response — otherwise the commit in get_db happens after
    # the response and the error is silently swallowed.
    await db.flush()
