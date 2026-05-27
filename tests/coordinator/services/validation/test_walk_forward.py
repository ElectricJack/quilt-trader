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
    from coordinator.database.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as s:
        yield s


@pytest.mark.asyncio
async def test_run_walk_forward_runs_sweep_per_fold(db_session):
    sess = create_session(
        db_session,
        name="wf-test-001",
        hypothesis="H",
        parameter_space={"vol_target": [0.10, 0.15]},
        pre_registered_criteria={},
    )
    db_session.commit()

    # Fake sweep that picks the first config as best
    async def fake_sweep(*, db, session_id, manifest_path, base_config, parameter_space, **kwargs):
        from coordinator.services.validation.sweep import SweepResult, expand_grid
        configs = expand_grid(parameter_space)
        return SweepResult(session_id=session_id, n_configs=len(configs), run_ids=[1, 2])

    # Fake "best config from train" picker
    async def fake_pick_best(db, run_ids, objective):
        return {"vol_target": 0.15}

    # Fake "run single OOS config" call
    fake_oos = AsyncMock(return_value=99)  # returns OOS run_id

    from datetime import date
    with patch("coordinator.services.validation.walk_forward.run_sweep", side_effect=fake_sweep), \
         patch("coordinator.services.validation.walk_forward._pick_best_train_config", side_effect=fake_pick_best), \
         patch("coordinator.services.validation.walk_forward._run_oos_backtest", fake_oos):
        result = await run_walk_forward(
            db=db_session,
            session_id=sess.id,
            manifest_path="/dummy/manifest.yaml",
            base_config={"start": "2015-01-01", "end": "2026-05-01"},
            parameter_space={"vol_target": [0.10, 0.15]},
            train_years=4.0,
            test_years=1.0,
            step_months=6.0,
            objective="sharpe",
            parallelism=1,
        )

    assert result.n_folds >= 10
    assert len(result.oos_run_ids) == result.n_folds
