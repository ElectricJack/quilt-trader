from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from sqlalchemy.orm import Session

from coordinator.services.validation.sweep import run_sweep


@dataclass
class Fold:
    index: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date


def compute_folds(
    *,
    start: date,
    end: date,
    train_years: float,
    test_years: float,
    step_months: float,
) -> list[Fold]:
    train_days = int(train_years * 365.25)
    test_days = int(test_years * 365.25)
    step_days = int(step_months * 30.4375)

    folds: list[Fold] = []
    i = 0
    cursor = start
    while True:
        train_start = cursor
        train_end = train_start + timedelta(days=train_days)
        test_start = train_end
        test_end = test_start + timedelta(days=test_days)
        if test_end > end:
            break
        folds.append(
            Fold(
                index=i,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
        i += 1
        cursor = cursor + timedelta(days=step_days)
    return folds


@dataclass
class WalkForwardResult:
    session_id: int
    n_folds: int
    oos_run_ids: list[int]
    train_run_ids: list[list[int]] = field(default_factory=list)


async def _pick_best_train_config(
    db: Session, run_ids: list[int], objective: str
) -> dict[str, Any]:
    """Pick the in-sample winner based on the objective metric."""
    from coordinator.database.models import BacktestRun

    rows = db.query(BacktestRun).filter(BacktestRun.id.in_(run_ids)).all()
    metric_col = {
        "sharpe": "sharpe_ratio",
        "calmar": "calmar_ratio",
        "sortino": "sortino_ratio",
    }[objective]
    rows.sort(key=lambda r: getattr(r, metric_col, 0.0) or 0.0, reverse=True)
    best = rows[0]
    import json
    return json.loads(best.config_json)


async def _run_oos_backtest(
    db: Session,
    *,
    session_id: int,
    manifest_path: str,
    base_config: dict[str, Any],
    config: dict[str, Any],
    fold_index: int,
) -> int:
    """Run a single OOS backtest with the winning config on the test window.
    Returns the BacktestRun.id."""
    from coordinator.database.models import BacktestRun
    import json
    from coordinator.services.validation.sweep import config_hash

    merged = {**base_config, **config, "_fold_index": fold_index, "_oos": True}

    # Extract runner infrastructure from merged config (stripped from DB row).
    session_factory = merged.pop("_session_factory", None)
    download_manager = merged.pop("_download_manager", None)
    data_service = merged.pop("_data_service", None)

    run_row = BacktestRun(
        algorithm_id=merged.get("algorithm_id", ""),
        date_range_start=merged.get("start"),
        date_range_end=merged.get("end"),
        config_overrides=merged,
        config_hash=config_hash(config),
        optimization_session_id=session_id,
        status="pending",
    )
    db.add(run_row)
    db.flush()

    if session_factory is not None and download_manager is not None and data_service is not None:
        from coordinator.services.backtest_runner import BacktestRunner

        runner = BacktestRunner(
            session_factory=session_factory,
            download_manager=download_manager,
            data_service=data_service,
        )
        await runner.run(run_row.id)
        db.refresh(run_row)

    return run_row.id


async def run_walk_forward(
    db: Session,
    *,
    session_id: int,
    manifest_path: str,
    base_config: dict[str, Any],
    parameter_space: dict[str, Any],
    train_years: float,
    test_years: float,
    step_months: float,
    objective: Literal["sharpe", "calmar", "sortino"],
    parallelism: int = 1,
) -> WalkForwardResult:
    """Rolling train/test walk-forward.

    For each fold:
      1. Run a sweep over ``parameter_space`` on the train window.
      2. Pick the in-sample winner by ``objective``.
      3. Run a single OOS backtest with that winner on the test window.

    Returns a result object with OOS run IDs (one per fold) for downstream
    concatenation and metric computation.
    """
    start = date.fromisoformat(base_config["start"])
    end = date.fromisoformat(base_config["end"])
    folds = compute_folds(
        start=start, end=end,
        train_years=train_years, test_years=test_years, step_months=step_months,
    )

    oos_run_ids: list[int] = []
    train_run_ids: list[list[int]] = []

    for fold in folds:
        train_cfg = {
            **base_config,
            "start": fold.train_start.isoformat(),
            "end": fold.train_end.isoformat(),
        }
        sweep_result = await run_sweep(
            db=db,
            session_id=session_id,
            manifest_path=manifest_path,
            base_config=train_cfg,
            parameter_space=parameter_space,
            search="grid",
            max_trials=10_000,
            parallelism=parallelism,
        )
        train_run_ids.append(sweep_result.run_ids)

        winner = await _pick_best_train_config(db, sweep_result.run_ids, objective)
        oos_cfg = {
            **base_config,
            "start": fold.test_start.isoformat(),
            "end": fold.test_end.isoformat(),
        }
        oos_id = await _run_oos_backtest(
            db,
            session_id=session_id,
            manifest_path=manifest_path,
            base_config=oos_cfg,
            config=winner,
            fold_index=fold.index,
        )
        oos_run_ids.append(oos_id)

    return WalkForwardResult(
        session_id=session_id,
        n_folds=len(folds),
        oos_run_ids=oos_run_ids,
        train_run_ids=train_run_ids,
    )
