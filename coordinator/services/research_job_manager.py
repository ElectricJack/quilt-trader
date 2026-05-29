"""ResearchJobManager — fire-and-poll orchestration for sweep / walk-forward.

Mirrors DownloadManager's pattern: a request to start a job inserts a DB row,
returns the id immediately, then runs the work in an asyncio.create_task that
streams progress updates into the row. Polling endpoints read the row.

Invariant I18: Research orchestration endpoints are fire-and-poll.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from coordinator.database.models import OptimizationSession, ResearchJob

logger = logging.getLogger(__name__)


SweepFn = Callable[..., Awaitable[Any]]          # signature of run_sweep
WalkForwardFn = Callable[..., Awaitable[Any]]    # signature of run_walk_forward
RunnerFactory = Callable[[str], Awaitable[None]] # (run_id) -> None


class ResearchJobManager:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        sweep_fn: SweepFn,
        walk_forward_fn: WalkForwardFn,
        runner_factory: RunnerFactory,
        sync_session_factory: Optional[Callable[[], Any]] = None,
    ):
        self._sf = session_factory
        self._sweep_fn = sweep_fn
        self._wf_fn = walk_forward_fn
        self._runner_factory = runner_factory
        self._sync_sf = sync_session_factory
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._cancel_flags: dict[str, asyncio.Event] = {}

    # ---- Public API ----------------------------------------------------

    async def create_sweep_job(self, *, session_id: int, request_payload: dict) -> str:
        return await self._create_job("sweep", session_id, request_payload)

    async def create_walk_forward_job(self, *, session_id: int, request_payload: dict) -> str:
        return await self._create_job("walk-forward", session_id, request_payload)

    async def get_job(self, job_id: str) -> Optional[dict]:
        async with self._sf() as s:
            row = (await s.execute(select(ResearchJob).where(ResearchJob.id == job_id))).scalar_one_or_none()
            return _row_to_dict(row) if row else None

    async def list_jobs(self, session_id: int) -> list[dict]:
        async with self._sf() as s:
            rows = (await s.execute(
                select(ResearchJob).where(ResearchJob.session_id == session_id)
                .order_by(ResearchJob.created_at.desc())
            )).scalars().all()
            return [_row_to_dict(r) for r in rows]

    async def cancel_job(self, job_id: str) -> bool:
        flag = self._cancel_flags.get(job_id)
        if flag is not None:
            flag.set()
        task = self._active_tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()
        async with self._sf() as s:
            row = (await s.execute(select(ResearchJob).where(ResearchJob.id == job_id))).scalar_one_or_none()
            if row is None:
                return False
            if row.status in ("completed", "failed", "cancelled"):
                return True
            row.status = "cancelled"
            row.completed_at = datetime.now(timezone.utc)
            await s.commit()
        return True

    async def recover_orphaned_jobs(self) -> int:
        async with self._sf() as s:
            rows = (await s.execute(
                select(ResearchJob).where(ResearchJob.status.in_(["queued", "running"]))
            )).scalars().all()
            for r in rows:
                r.status = "failed"
                r.completed_at = datetime.now(timezone.utc)
                r.error_message = "Orphaned by coordinator restart"
            count = len(rows)
            await s.commit()
            return count

    async def shutdown(self) -> None:
        for t in list(self._active_tasks.values()):
            if not t.done():
                t.cancel()
        for t in list(self._active_tasks.values()):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._active_tasks.clear()
        self._cancel_flags.clear()

    # ---- Internals -----------------------------------------------------

    async def _create_job(self, kind: str, session_id: int, request_payload: dict) -> str:
        job_id = str(uuid.uuid4())
        async with self._sf() as s:
            sess = (await s.execute(
                select(OptimizationSession).where(OptimizationSession.id == session_id)
            )).scalar_one_or_none()
            if sess is None:
                raise ValueError(f"session {session_id} not found")
            row = ResearchJob(
                id=job_id, session_id=session_id, kind=kind,
                status="queued", progress_pct=0.0,
                request_payload=request_payload, run_ids=[],
            )
            s.add(row)
            await s.commit()
        cancel_flag = asyncio.Event()
        self._cancel_flags[job_id] = cancel_flag
        task = asyncio.create_task(self._run_job(job_id, kind, session_id, request_payload, cancel_flag))
        self._active_tasks[job_id] = task
        return job_id

    async def _run_job(self, job_id: str, kind: str, session_id: int,
                       payload: dict, cancel_flag: asyncio.Event) -> None:
        try:
            await self._mark_running(job_id)
            progress_cb = _make_progress_callback(self._sf, job_id, cancel_flag)
            if kind == "sweep":
                await self._dispatch_sweep(session_id, payload, progress_cb)
            else:
                await self._dispatch_walk_forward(session_id, payload, progress_cb)
            await self._mark_terminal(job_id, "completed")
        except asyncio.CancelledError:
            await self._mark_terminal(job_id, "cancelled")
            raise
        except Exception as exc:
            logger.exception("ResearchJob %s failed", job_id)
            await self._mark_terminal(job_id, "failed", error=str(exc))
        finally:
            self._active_tasks.pop(job_id, None)
            self._cancel_flags.pop(job_id, None)

    async def _dispatch_sweep(self, session_id: int, payload: dict, progress_cb) -> None:
        if self._sync_sf is None:
            raise RuntimeError("sync_session_factory required for sweep dispatch")
        with self._sync_sf() as db:
            await self._sweep_fn(
                db, self._runner_factory,
                session_id=session_id,
                manifest_path=payload["manifest_path"],
                base_config=payload["base_config"],
                parameter_space=payload.get("parameter_space"),
                search=payload.get("search", "grid"),
                max_trials=payload.get("max_trials", 50),
                parallelism=payload.get("parallelism", 1),
                seed=payload.get("seed", 0),
                progress_callback=progress_cb,
            )
            db.commit()

    async def _dispatch_walk_forward(self, session_id: int, payload: dict, progress_cb) -> None:
        if self._sync_sf is None:
            raise RuntimeError("sync_session_factory required for walk-forward dispatch")
        with self._sync_sf() as db:
            await self._wf_fn(
                db, self._runner_factory,
                session_id=session_id,
                manifest_path=payload["manifest_path"],
                base_config=payload["base_config"],
                parameter_space=payload.get("parameter_space"),
                train_years=payload.get("train_years", 4.0),
                test_years=payload.get("test_years", 1.0),
                step_months=payload.get("step_months", 6.0),
                objective=payload.get("objective", "sharpe"),
                parallelism=payload.get("parallelism", 1),
                progress_callback=progress_cb,
            )
            db.commit()

    async def _mark_running(self, job_id: str) -> None:
        async with self._sf() as s:
            row = (await s.execute(select(ResearchJob).where(ResearchJob.id == job_id))).scalar_one()
            if row.status == "cancelled":
                raise asyncio.CancelledError()
            row.status = "running"
            row.started_at = datetime.now(timezone.utc)
            await s.commit()

    async def _mark_terminal(self, job_id: str, status: str, *, error: str | None = None) -> None:
        async with self._sf() as s:
            row = (await s.execute(select(ResearchJob).where(ResearchJob.id == job_id))).scalar_one_or_none()
            if row is None:
                return
            if row.status in ("completed", "failed", "cancelled"):
                return
            row.status = status
            row.completed_at = datetime.now(timezone.utc)
            if status == "completed":
                row.progress_pct = 1.0
            if error is not None:
                row.error_message = error
            await s.commit()


def _row_to_dict(row: ResearchJob) -> dict:
    return {
        "job_id": row.id,
        "session_id": row.session_id,
        "kind": row.kind,
        "status": row.status,
        "progress_pct": row.progress_pct,
        "progress_message": row.progress_message,
        "run_ids": row.run_ids or [],
        "error_message": row.error_message,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _make_progress_callback(session_factory, job_id: str, cancel_flag: asyncio.Event):
    """Return a callable the sweep/walk-forward orchestrator invokes after each
    completed trial / fold. Signature: (pct: float, message: str, run_ids: list[str]).

    The callback raises asyncio.CancelledError if the cancel flag has been
    set, providing a cooperative cancellation point between trials.
    """
    async def cb(pct: float, message: str, run_ids: list[str]) -> None:
        if cancel_flag.is_set():
            raise asyncio.CancelledError()
        async with session_factory() as s:
            row = (await s.execute(select(ResearchJob).where(ResearchJob.id == job_id))).scalar_one_or_none()
            if row is None:
                return
            row.progress_pct = float(pct)
            row.progress_message = message
            row.run_ids = list(run_ids)
            await s.commit()
    return cb
