from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_container, get_db
from coordinator.database.models import OptimizationSession, BacktestRun
from coordinator.database.session import get_session_factory
from coordinator.services.validation.optimization_session import (
    create_session,
    get_session_runs,
)
from coordinator.services.validation.walk_forward import (
    concatenate_oos_curves,
)
from coordinator.services.validation.regime import (
    regime_conditional_metrics,
    tag_regimes,
)
from coordinator.services.validation.bootstrap import bootstrap_metrics
from coordinator.services.validation.report import (
    ReportInputs,
    build_html_report,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/research", tags=["research"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    name: str
    hypothesis: str
    parameter_space: dict
    pre_registered_criteria: dict
    notes: str = ""


class SessionResponse(BaseModel):
    id: int
    name: str
    hypothesis: str
    status: str
    notes: str
    created_at: str
    completed_at: str | None = None
    parameter_space: dict
    pre_registered_criteria: dict
    n_runs: int


class SweepRequest(BaseModel):
    manifest_path: str
    base_config: dict
    parameter_space: dict | None = None  # None → use session's parameter_space
    search: str = "grid"  # grid | random | latin
    max_trials: int = 50
    parallelism: int = 1
    seed: int = 0


class WalkForwardRequest(BaseModel):
    manifest_path: str
    base_config: dict
    parameter_space: dict | None = None
    train_years: float = 4.0
    test_years: float = 1.0
    step_months: float = 6.0
    objective: str = "sharpe"  # sharpe | calmar | sortino
    parallelism: int = 1


class JobResponse(BaseModel):
    job_id: str
    session_id: int
    kind: str
    status: str
    progress_pct: float = 0.0
    progress_message: str | None = None
    run_ids: list[str] = []
    error_message: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str | None = None


class ReportResponse(BaseModel):
    session_id: int
    markdown_path: str
    html_path: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session_to_response(sess: OptimizationSession, n_runs: int) -> SessionResponse:
    return SessionResponse(
        id=sess.id,
        name=sess.name,
        hypothesis=sess.hypothesis,
        status=sess.status,
        notes=sess.notes,
        created_at=sess.created_at.isoformat() if sess.created_at else "",
        completed_at=sess.completed_at.isoformat() if sess.completed_at else None,
        parameter_space=json.loads(sess.parameter_space),
        pre_registered_criteria=json.loads(sess.pre_registered_criteria),
        n_runs=n_runs,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/sessions", response_model=SessionResponse)
async def create_session_endpoint(payload: CreateSessionRequest) -> SessionResponse:
    """Pre-register an OptimizationSession. Hypothesis and criteria are
    immutable after this call (enforced by uniqueness on `name`)."""
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        try:
            sess = create_session(
                db,
                name=payload.name,
                hypothesis=payload.hypothesis,
                parameter_space=payload.parameter_space,
                pre_registered_criteria=payload.pre_registered_criteria,
                notes=payload.notes,
            )
            db.commit()
            db.refresh(sess)
            return _session_to_response(sess, n_runs=0)
        except Exception as e:
            db.rollback()
            raise HTTPException(400, f"failed to create session: {e}") from e


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions_endpoint(
    db: AsyncSession = Depends(get_db),
) -> list[SessionResponse]:
    """List all OptimizationSessions, newest first."""
    result = await db.execute(
        select(OptimizationSession).order_by(OptimizationSession.created_at.desc())
    )
    sessions = result.scalars().all()
    out = []
    for s in sessions:
        cnt_result = await db.execute(
            select(BacktestRun).where(BacktestRun.optimization_session_id == s.id)
        )
        n_runs = len(cnt_result.scalars().all())
        out.append(_session_to_response(s, n_runs))
    return out


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session_endpoint(
    session_id: int,
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    sess = (
        await db.execute(
            select(OptimizationSession).where(OptimizationSession.id == session_id)
        )
    ).scalar_one_or_none()
    if sess is None:
        raise HTTPException(404, f"session {session_id} not found")
    cnt_result = await db.execute(
        select(BacktestRun).where(BacktestRun.optimization_session_id == session_id)
    )
    n_runs = len(cnt_result.scalars().all())
    return _session_to_response(sess, n_runs)


def _get_research_job_manager():
    container = get_container()
    mgr = getattr(container, "research_job_manager", None)
    if mgr is None:
        raise HTTPException(503, "research_job_manager not initialized")
    return mgr


@router.post(
    "/sessions/{session_id}/sweep",
    response_model=JobResponse,
    status_code=202,
)
async def sweep_endpoint(session_id: int, payload: SweepRequest) -> JobResponse:
    """Queue a sweep job and return immediately with the job_id (I18)."""
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        sess = (
            db.query(OptimizationSession)
            .filter(OptimizationSession.id == session_id)
            .one_or_none()
        )
        if sess is None:
            raise HTTPException(404, f"session {session_id} not found")
        # Resolve parameter_space: payload override, then session default.
        param_space = (
            payload.parameter_space
            if payload.parameter_space is not None
            else json.loads(sess.parameter_space)
        )

    mgr = _get_research_job_manager()
    request_payload = payload.model_dump(exclude_none=True)
    request_payload["parameter_space"] = param_space
    try:
        job_id = await mgr.create_sweep_job(
            session_id=session_id, request_payload=request_payload,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    job = await mgr.get_job(job_id)
    return JobResponse(**job)


@router.post(
    "/sessions/{session_id}/walk-forward",
    response_model=JobResponse,
    status_code=202,
)
async def walk_forward_endpoint(
    session_id: int, payload: WalkForwardRequest
) -> JobResponse:
    """Queue a walk-forward job and return immediately with the job_id (I18)."""
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        sess = (
            db.query(OptimizationSession)
            .filter(OptimizationSession.id == session_id)
            .one_or_none()
        )
        if sess is None:
            raise HTTPException(404, f"session {session_id} not found")
        param_space = (
            payload.parameter_space
            if payload.parameter_space is not None
            else json.loads(sess.parameter_space)
        )

    mgr = _get_research_job_manager()
    request_payload = payload.model_dump(exclude_none=True)
    request_payload["parameter_space"] = param_space
    try:
        job_id = await mgr.create_walk_forward_job(
            session_id=session_id, request_payload=request_payload,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    job = await mgr.get_job(job_id)
    return JobResponse(**job)


@router.get(
    "/sessions/{session_id}/jobs",
    response_model=list[JobResponse],
)
async def list_jobs_endpoint(session_id: int) -> list[JobResponse]:
    mgr = _get_research_job_manager()
    return [JobResponse(**j) for j in await mgr.list_jobs(session_id)]


@router.get(
    "/sessions/{session_id}/jobs/{job_id}",
    response_model=JobResponse,
)
async def get_job_endpoint(session_id: int, job_id: str) -> JobResponse:
    mgr = _get_research_job_manager()
    job = await mgr.get_job(job_id)
    if job is None or job["session_id"] != session_id:
        raise HTTPException(404, "job not found")
    return JobResponse(**job)


@router.delete("/sessions/{session_id}/jobs/{job_id}")
async def cancel_job_endpoint(session_id: int, job_id: str) -> dict:
    mgr = _get_research_job_manager()
    job = await mgr.get_job(job_id)
    if job is None or job["session_id"] != session_id:
        raise HTTPException(404, "job not found")
    await mgr.cancel_job(job_id)
    return {"ok": True}


@router.post("/sessions/{session_id}/report", response_model=ReportResponse)
async def build_report_endpoint(
    session_id: int, out_dir: str = "data/research_reports"
) -> ReportResponse:
    """Build the markdown + HTML report from a completed session's OOS runs."""
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        sess = (
            db.query(OptimizationSession)
            .filter(OptimizationSession.id == session_id)
            .one_or_none()
        )
        if sess is None:
            raise HTTPException(404, f"session {session_id} not found")

        runs = get_session_runs(db, session_id)
        oos_paths = []
        for r in runs:
            overrides = r.config_overrides or {}
            if isinstance(overrides, str):
                try:
                    overrides = json.loads(overrides)
                except Exception:
                    overrides = {}
            if overrides.get("_oos") is True:
                path = Path(f"data/backtests/{r.id}/equity_native.parquet")
                if path.exists():
                    oos_paths.append(path)

        if not oos_paths:
            raise HTTPException(404, "No OOS runs found for this session.")

        equity = concatenate_oos_curves(oos_paths)
        regimes = tag_regimes(equity)
        boot = bootstrap_metrics(equity, n_resamples=1000)
        regime_m = regime_conditional_metrics(equity, regimes)

        inputs = ReportInputs(
            session=sess,
            oos_equity_curve=equity,
            regimes=regimes,
            bootstrap_metrics={k: v.__dict__ for k, v in boot.items()},
            regime_metrics=regime_m,
            corrected_p_values=[],
        )
        target = Path(out_dir) / str(session_id)
        result = build_html_report(inputs, out_dir=target)
        return ReportResponse(
            session_id=session_id,
            markdown_path=str(result["md"]),
            html_path=str(result["html"]),
        )
