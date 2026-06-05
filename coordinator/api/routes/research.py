from __future__ import annotations

import asyncio
import json
import logging
from datetime import date as _date
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_container, get_db
from coordinator.database.models import Algorithm, OptimizationSession, BacktestRun
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
    algorithm_id: str                                  # required
    base_config: dict = Field(default_factory=dict)    # defaults to {}
    parameter_space: dict
    pre_registered_criteria: dict
    notes: str = ""

    # NEW (this spec)
    date_range_start: _date                                # required
    date_range_end: _date                                  # required
    initial_cash: float = 10_000.0                         # required, with default
    cost_profile: str = "default"                          # required, with default
    benchmark_symbol: str | None = None                    # optional pair
    benchmark_source: str | None = None
    mtm_realism: float = 0.0

    @model_validator(mode="after")
    def _benchmark_pair(self):
        if (self.benchmark_symbol is None) != (self.benchmark_source is None):
            raise ValueError(
                "benchmark_symbol and benchmark_source must both be set or both be null"
            )
        return self

    @model_validator(mode="after")
    def _date_range_valid(self):
        if self.date_range_end <= self.date_range_start:
            raise ValueError("date_range_end must be after date_range_start")
        return self

    @model_validator(mode="after")
    def _mtm_realism_in_range(self):
        if not (0.0 <= self.mtm_realism <= 1.0):
            raise ValueError(
                f"mtm_realism must be in [0.0, 1.0]; got {self.mtm_realism!r}"
            )
        return self


class SessionResponse(BaseModel):
    id: int
    name: str
    hypothesis: str
    status: str
    notes: str
    created_at: str
    completed_at: str | None = None
    algorithm_id: str
    base_config: dict
    parameter_space: dict
    pre_registered_criteria: dict
    n_runs: int
    # NEW
    date_range_start: str           # ISO date YYYY-MM-DD
    date_range_end: str
    initial_cash: float
    cost_profile: str
    benchmark_symbol: str | None = None
    benchmark_source: str | None = None
    mtm_realism: float = 0.0


class SweepRequest(BaseModel):
    # algorithm + base_config + parameter_space come from the session.
    search: Literal["grid", "random", "latin", "tpe"] = "grid"
    max_trials: int = 50
    parallelism: int = 1
    seed: int = 0

    model_config = {"extra": "forbid"}  # reject legacy fields explicitly


class WalkForwardRequest(BaseModel):
    train_years: float = 4.0
    test_years: float = 1.0
    step_months: float = 6.0
    objective: Literal["sharpe", "calmar", "sortino"] = "sharpe"
    parallelism: int = 1

    model_config = {"extra": "forbid"}


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
        algorithm_id=sess.algorithm_id,
        base_config=sess.base_config if sess.base_config is not None else {},
        status=sess.status,
        notes=sess.notes,
        created_at=sess.created_at.isoformat() if sess.created_at else "",
        completed_at=sess.completed_at.isoformat() if sess.completed_at else None,
        parameter_space=json.loads(sess.parameter_space),
        pre_registered_criteria=json.loads(sess.pre_registered_criteria),
        n_runs=n_runs,
        # NEW
        date_range_start=sess.date_range_start.isoformat(),
        date_range_end=sess.date_range_end.isoformat(),
        initial_cash=sess.initial_cash,
        cost_profile=sess.cost_profile,
        benchmark_symbol=sess.benchmark_symbol,
        benchmark_source=sess.benchmark_source,
        mtm_realism=sess.mtm_realism,
    )


async def _resolve_manifest_path_from_algorithm(
    db: AsyncSession,
    *,
    algorithm_id: str,
) -> str:
    """Resolve an algorithm id to its on-disk manifest path."""
    algo = (
        await db.execute(select(Algorithm).where(Algorithm.id == algorithm_id))
    ).scalar_one_or_none()
    if algo is None:
        raise HTTPException(404, f"unknown algorithm: {algorithm_id}")
    if not algo.source_path:
        raise HTTPException(
            400,
            f"algorithm {algorithm_id} has no source_path; "
            "cannot resolve manifest",
        )
    return f"{algo.source_path}/quilt.yaml"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/sessions", response_model=SessionResponse)
async def create_session_endpoint(payload: CreateSessionRequest) -> SessionResponse:
    """Pre-register an OptimizationSession. Hypothesis, algorithm, base_config,
    and criteria are immutable after this call."""
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        # Validate the algorithm exists AND has a source_path.
        algo = (
            db.query(Algorithm)
            .filter(Algorithm.id == payload.algorithm_id)
            .one_or_none()
        )
        if algo is None:
            raise HTTPException(404, f"unknown algorithm: {payload.algorithm_id}")
        if not algo.source_path:
            raise HTTPException(
                400,
                f"algorithm {payload.algorithm_id} has no source_path; "
                "cannot bind to a session",
            )
        try:
            sess = create_session(
                db,
                name=payload.name,
                hypothesis=payload.hypothesis,
                algorithm_id=payload.algorithm_id,
                base_config=payload.base_config,
                parameter_space=payload.parameter_space,
                pre_registered_criteria=payload.pre_registered_criteria,
                notes=payload.notes,
                date_range_start=payload.date_range_start,
                date_range_end=payload.date_range_end,
                initial_cash=payload.initial_cash,
                cost_profile=payload.cost_profile,
                benchmark_symbol=payload.benchmark_symbol,
                benchmark_source=payload.benchmark_source,
                mtm_realism=payload.mtm_realism,
            )

            db.commit()
            db.refresh(sess)
            return _session_to_response(sess, n_runs=0)
        except HTTPException:
            raise
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
async def sweep_endpoint(
    session_id: int,
    payload: SweepRequest,
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Queue a sweep job. Algorithm, base_config, and parameter_space all
    come from the session; the request body carries only execution params."""
    sess = (
        await db.execute(
            select(OptimizationSession).where(OptimizationSession.id == session_id)
        )
    ).scalar_one_or_none()
    if sess is None:
        raise HTTPException(404, f"session {session_id} not found")

    manifest_path = await _resolve_manifest_path_from_algorithm(
        db, algorithm_id=sess.algorithm_id,
    )

    request_payload = {
        "manifest_path": manifest_path,
        "algorithm_id": sess.algorithm_id,
        "date_range_start": sess.date_range_start.isoformat(),
        "date_range_end": sess.date_range_end.isoformat(),
        "initial_cash": sess.initial_cash,
        "cost_profile": sess.cost_profile,
        "benchmark_symbol": sess.benchmark_symbol,
        "benchmark_source": sess.benchmark_source,
        "base_config": sess.base_config,    # algorithm config only
        "parameter_space": json.loads(sess.parameter_space),
        "search": payload.search,
        "max_trials": payload.max_trials,
        "parallelism": payload.parallelism,
        "seed": payload.seed,
    }

    mgr = _get_research_job_manager()
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
    session_id: int,
    payload: WalkForwardRequest,
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Queue a walk-forward job. Algorithm + base_config + parameter_space
    from session; request body carries only train/test/step/objective."""
    sess = (
        await db.execute(
            select(OptimizationSession).where(OptimizationSession.id == session_id)
        )
    ).scalar_one_or_none()
    if sess is None:
        raise HTTPException(404, f"session {session_id} not found")

    manifest_path = await _resolve_manifest_path_from_algorithm(
        db, algorithm_id=sess.algorithm_id,
    )

    request_payload = {
        "manifest_path": manifest_path,
        "algorithm_id": sess.algorithm_id,
        "date_range_start": sess.date_range_start.isoformat(),
        "date_range_end": sess.date_range_end.isoformat(),
        "initial_cash": sess.initial_cash,
        "cost_profile": sess.cost_profile,
        "benchmark_symbol": sess.benchmark_symbol,
        "benchmark_source": sess.benchmark_source,
        "base_config": sess.base_config,    # algorithm config only
        "parameter_space": json.loads(sess.parameter_space),
        "train_years": payload.train_years,
        "test_years": payload.test_years,
        "step_months": payload.step_months,
        "objective": payload.objective,
        "parallelism": payload.parallelism,
    }

    mgr = _get_research_job_manager()
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
