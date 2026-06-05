from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from coordinator.database.models import OptimizationSession


def create_session(
    db: Session,
    *,
    name: str,
    hypothesis: str,
    algorithm_id: str,
    base_config: dict[str, Any],
    parameter_space: dict[str, Any],
    pre_registered_criteria: dict[str, Any],
    notes: str = "",
    # NEW (this spec) — required
    date_range_start: date,
    date_range_end: date,
    # NEW — required-with-default
    initial_cash: float = 10_000.0,
    cost_profile: str = "default",
    # NEW — optional pair
    benchmark_symbol: str | None = None,
    benchmark_source: str | None = None,
    mtm_realism: float = 0.0,
) -> OptimizationSession:
    """Create a new OptimizationSession.

    The session is the complete pre-registered experiment definition:
    algorithm, base_config, parameter_space, criteria, date range, capital,
    cost model, and optional benchmark are all immutable post-create.
    """
    sess = OptimizationSession(
        name=name,
        hypothesis=hypothesis,
        algorithm_id=algorithm_id,
        base_config=base_config,
        parameter_space=json.dumps(parameter_space),
        pre_registered_criteria=json.dumps(pre_registered_criteria),
        notes=notes,
        status="open",
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        initial_cash=initial_cash,
        cost_profile=cost_profile,
        benchmark_symbol=benchmark_symbol,
        benchmark_source=benchmark_source,
        mtm_realism=mtm_realism,
    )
    db.add(sess)
    db.flush()
    return sess


def get_session_runs(db: Session, session_id: int) -> list[Any]:
    """Return all BacktestRun rows attached to this session."""
    from coordinator.database.models import BacktestRun

    return db.query(BacktestRun).filter(BacktestRun.optimization_session_id == session_id).all()


def count_hypotheses_tested(db: Session, session_id: int) -> int:
    """Distinct parameter configs tested in this session (for multi-test correction).

    Counts distinct `config_hash` values across BacktestRun rows in this session.
    """
    from coordinator.database.models import BacktestRun

    rows = (
        db.query(BacktestRun.config_hash)
        .filter(BacktestRun.optimization_session_id == session_id)
        .distinct()
        .all()
    )
    return len(rows)
