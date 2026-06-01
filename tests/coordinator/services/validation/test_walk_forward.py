from datetime import date

from coordinator.services.validation.walk_forward import compute_folds, Fold


def test_compute_folds_basic():
    folds = compute_folds(
        start=date(2015, 1, 1),
        end=date(2026, 5, 1),
        train_years=4.0,
        test_years=1.0,
        step_months=6.0,
    )
    assert len(folds) >= 10
    assert all(isinstance(f, Fold) for f in folds)
    assert folds[0].train_start == date(2015, 1, 1)
    assert (folds[0].train_end - folds[0].train_start).days >= 4 * 365 - 1
    assert folds[0].test_start == folds[0].train_end
    assert (folds[0].test_end - folds[0].test_start).days >= 365 - 1
    # Step of 6 months ~ 182 days between successive train_starts
    delta_days = (folds[1].train_start - folds[0].train_start).days
    assert 175 <= delta_days <= 190


def test_compute_folds_drops_incomplete_last_fold():
    folds = compute_folds(
        start=date(2020, 1, 1),
        end=date(2021, 1, 1),
        train_years=2.0,
        test_years=1.0,
        step_months=6.0,
    )
    # train_years=2 from start=2020-01-01 → train_end = 2022-01-01 > end → no folds
    assert len(folds) == 0


import pytest
from unittest.mock import AsyncMock, patch

from coordinator.services.validation.walk_forward import run_walk_forward
from coordinator.services.validation.optimization_session import create_session


@pytest.fixture
def db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from coordinator.database.models import Base, Algorithm

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as s:
        algo = Algorithm(id="test-algo", name="test-algo", repo_url="https://example.com/algo")
        s.add(algo)
        s.flush()
        yield s


@pytest.mark.asyncio
async def test_run_walk_forward_runs_sweep_per_fold(db_session):
    sess = create_session(
        db_session,
        name="wf-test-001",
        hypothesis="H",
        algorithm_id="test-algo",
        base_config={},
        parameter_space={"vol_target": [0.10, 0.15]},
        pre_registered_criteria={},
        date_range_start=date(2023, 1, 1),
        date_range_end=date(2024, 12, 31),
    )
    db_session.commit()

    # Fake sweep that picks the first config as best
    async def fake_sweep(db, runner_factory, *, session_id, manifest_path, base_config, parameter_space, **kwargs):
        from coordinator.services.validation.sweep import SweepResult, expand_grid
        configs = expand_grid(parameter_space)
        return SweepResult(session_id=session_id, n_configs=len(configs), run_ids=[1, 2])

    # Fake "best config from train" picker
    async def fake_pick_best(db, run_ids, objective):
        return {"vol_target": 0.15}

    # Fake "run single OOS config" call
    fake_oos = AsyncMock(return_value=99)  # returns OOS run_id

    async def fake_factory(run_id: int) -> None:
        return None

    with patch("coordinator.services.validation.walk_forward.run_sweep", side_effect=fake_sweep), \
         patch("coordinator.services.validation.walk_forward._pick_best_train_config", side_effect=fake_pick_best), \
         patch("coordinator.services.validation.walk_forward._run_oos_backtest", fake_oos):
        result = await run_walk_forward(
            db_session,
            fake_factory,
            session_id=sess.id,
            manifest_path="/dummy/manifest.yaml",
            algorithm_id="test-algo",
            date_range_start=date(2015, 1, 1),
            date_range_end=date(2026, 5, 1),
            initial_cash=10000,
            cost_profile="default",
            benchmark_symbol=None,
            benchmark_source=None,
            base_config={},
            parameter_space={"vol_target": [0.10, 0.15]},
            train_years=4.0,
            test_years=1.0,
            step_months=6.0,
            objective="sharpe",
            parallelism=1,
        )

    assert result.n_folds >= 10
    assert len(result.oos_run_ids) == result.n_folds


@pytest.mark.asyncio
async def test_pick_best_train_config_reads_real_schema(db_session):
    """Regression: previously _pick_best_train_config read a non-existent
    `config_json` attribute on BacktestRun. Verify it reads the real column."""
    from coordinator.database.models import Algorithm, BacktestRun
    from coordinator.services.validation.walk_forward import _pick_best_train_config

    # Insert a stub Algorithm row (algorithm_id is NOT NULL on BacktestRun)
    algo = Algorithm(
        name="test-algo",
        repo_url="https://example.com/algo",
    )
    db_session.add(algo)
    db_session.flush()

    # Insert two BacktestRun rows with different sharpe and config_overrides
    from datetime import datetime, timezone
    _now = datetime.now(timezone.utc)
    r1 = BacktestRun(
        algorithm_id=algo.id,
        sharpe_ratio=0.5,
        config_overrides={"x": 1},
        status="completed",
        date_range_start=_now,
        date_range_end=_now,
    )
    r2 = BacktestRun(
        algorithm_id=algo.id,
        sharpe_ratio=1.2,
        config_overrides={"x": 2},
        status="completed",
        date_range_start=_now,
        date_range_end=_now,
    )
    db_session.add_all([r1, r2])
    db_session.commit()

    winner = await _pick_best_train_config(db_session, [r1.id, r2.id], "sharpe")
    assert winner == {"x": 2}  # r2 has the higher sharpe


def test_concatenate_oos_curves(tmp_path):
    """Concatenate OOS equity curves from N folds into one continuous series.

    Each fold's parquet starts with its own initial_cash; concatenation
    must scale each successive fold to chain off the prior fold's terminal value.
    """
    import pandas as pd
    from coordinator.services.validation.walk_forward import concatenate_oos_curves

    # Fold 1: 1000 → 1100 over 3 days
    f1 = tmp_path / "f1.parquet"
    pd.DataFrame(
        {"timestamp": pd.date_range("2024-01-01", periods=3, freq="D"), "equity": [1000.0, 1050.0, 1100.0]}
    ).to_parquet(f1)

    # Fold 2: 1000 → 990 over 3 days
    f2 = tmp_path / "f2.parquet"
    pd.DataFrame(
        {"timestamp": pd.date_range("2024-01-04", periods=3, freq="D"), "equity": [1000.0, 995.0, 990.0]}
    ).to_parquet(f2)

    curve = concatenate_oos_curves([f1, f2])
    assert len(curve) == 6
    assert curve.iloc[0] == 1000.0
    assert curve.iloc[2] == 1100.0   # end of fold 1
    # Fold 2: scaled to start at 1100 (fold 1 terminal)
    assert abs(curve.iloc[3] - 1100.0) < 1e-6
    # Fold 2 lost 1% over its window → terminal should be 1100 * (990/1000) = 1089
    assert abs(curve.iloc[5] - 1089.0) < 1e-6
