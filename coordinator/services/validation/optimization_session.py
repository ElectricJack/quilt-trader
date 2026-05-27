from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from coordinator.database.models import OptimizationSession


def create_session(
    db: Session,
    *,
    name: str,
    hypothesis: str,
    parameter_space: dict[str, Any],
    pre_registered_criteria: dict[str, Any],
    notes: str = "",
) -> OptimizationSession:
    """Create a new OptimizationSession.

    The session must be created *before* any backtest runs are attached to it;
    this enforces pre-registration of hypothesis and criteria.
    """
    sess = OptimizationSession(
        name=name,
        hypothesis=hypothesis,
        parameter_space=json.dumps(parameter_space),
        pre_registered_criteria=json.dumps(pre_registered_criteria),
        notes=notes,
        status="open",
    )
    db.add(sess)
    db.flush()  # populate sess.id without committing
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
