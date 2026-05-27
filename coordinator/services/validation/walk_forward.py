from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from sqlalchemy.orm import Session

from coordinator.services.validation.sweep import RunnerFactory, run_sweep


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
    return best.config_overrides or {}


async def _run_oos_backtest(
    db: Session,
    runner_factory: RunnerFactory,
    *,
    session_id: int,
    base_config: dict[str, Any],
    config: dict[str, Any],
    fold_index: int,
) -> int:
    """Run a single OOS backtest with the winning config on the test window.
    Returns the BacktestRun.id."""
    from coordinator.database.models import BacktestRun
    from coordinator.services.validation.sweep import config_hash

    merged = {**base_config, **config, "_fold_index": fold_index, "_oos": True}

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
    db.commit()  # make row visible to async runner via its own separate connection

    run_id = run_row.id
    await runner_factory(run_id)

    db.refresh(run_row)  # pull updated status/metrics written by the runner

    return run_row.id


async def run_walk_forward(
    db: Session,
    runner_factory: RunnerFactory,
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
            db,
            runner_factory,
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
            runner_factory,
            session_id=session_id,
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


import pandas as pd
from pathlib import Path


def concatenate_oos_curves(parquet_paths: list[Path]) -> pd.Series:
    """Chain OOS equity curves into one continuous series.

    Each fold's equity series is rescaled so its first value equals the prior
    fold's terminal value, producing a continuous compounding curve.

    Returns a Series indexed by timestamp.
    """
    segments: list[pd.Series] = []
    running_anchor: float | None = None

    for path in parquet_paths:
        df = pd.read_parquet(path)
        if "timestamp" not in df.columns or "equity" not in df.columns:
            raise ValueError(f"Expected timestamp + equity columns in {path}")
        s = df.set_index("timestamp")["equity"].astype(float)

        if running_anchor is None:
            segments.append(s)
        else:
            scale = running_anchor / s.iloc[0]
            segments.append(s * scale)

        running_anchor = float(segments[-1].iloc[-1])

    return pd.concat(segments)
