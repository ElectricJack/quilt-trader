import pytest

from coordinator.services.validation.sweep import expand_grid, config_hash


def test_expand_grid_simple():
    space = {"vol_target": [0.10, 0.15], "lookbacks": [[7, 14], [7, 14, 28]]}
    configs = expand_grid(space)
    assert len(configs) == 4
    assert {"vol_target": 0.10, "lookbacks": [7, 14]} in configs
    assert {"vol_target": 0.15, "lookbacks": [7, 14, 28]} in configs


def test_expand_grid_empty_returns_single_empty_config():
    assert expand_grid({}) == [{}]


def test_config_hash_stable():
    a = {"x": 1, "y": [2, 3]}
    b = {"y": [2, 3], "x": 1}  # different key order
    assert config_hash(a) == config_hash(b)


def test_config_hash_changes_with_value():
    assert config_hash({"x": 1}) != config_hash({"x": 2})


from coordinator.services.validation.sweep import sample_random, sample_latin_hypercube


def test_random_sample_count_and_seed():
    space = {"vol_target": [0.05, 0.40], "lookback": [3, 90]}  # bounds, not discrete
    configs = sample_random(space, n=20, seed=42, distributions={"vol_target": "uniform", "lookback": "int_uniform"})
    assert len(configs) == 20
    for cfg in configs:
        assert 0.05 <= cfg["vol_target"] <= 0.40
        assert 3 <= cfg["lookback"] <= 90 and isinstance(cfg["lookback"], int)

    # Determinism
    configs2 = sample_random(space, n=20, seed=42, distributions={"vol_target": "uniform", "lookback": "int_uniform"})
    assert configs == configs2


def test_latin_hypercube_covers_range():
    import numpy as np
    space = {"vol_target": [0.05, 0.40]}
    configs = sample_latin_hypercube(space, n=10, seed=7, distributions={"vol_target": "uniform"})
    values = np.array([c["vol_target"] for c in configs])
    # Latin hypercube: each of 10 strata should be hit exactly once
    strata = ((values - 0.05) / (0.40 - 0.05) * 10).astype(int)
    assert len(set(strata)) == 10


import pytest
from unittest.mock import AsyncMock, patch

from coordinator.services.validation.sweep import run_sweep, SweepResult
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
async def test_run_sweep_persists_runs(db_session):
    sess = create_session(
        db_session,
        name="sweep-test-001",
        hypothesis="H",
        parameter_space={"vol_target": [0.10, 0.15]},
        pre_registered_criteria={},
    )
    db_session.commit()

    fake_run_backtest = AsyncMock(return_value={"sharpe": 0.8, "max_dd": 0.20})

    async def fake_factory(run_id: int) -> None:
        return None

    with patch("coordinator.services.validation.sweep._run_one_backtest", fake_run_backtest):
        result = await run_sweep(
            db_session,
            fake_factory,
            session_id=sess.id,
            manifest_path="/dummy/manifest.yaml",
            base_config={"start": "2024-01-01", "end": "2024-02-01"},
            parameter_space={"vol_target": [0.10, 0.15]},
            search="grid",
            max_trials=2,
            parallelism=1,
            seed=42,
        )

    assert isinstance(result, SweepResult)
    assert result.n_configs == 2
    assert fake_run_backtest.await_count == 2


# ---- Optuna / TPE search ----

@pytest.mark.asyncio
async def test_tpe_search_dispatches_max_trials(db_session):
    """TPE search runs ``max_trials`` sequentially. Each trial reads the
    objective metric off the BacktestRun row Optuna just produced."""
    from unittest.mock import AsyncMock, patch
    from coordinator.services.validation.optimization_session import create_session
    from coordinator.database.models import BacktestRun

    sess = create_session(
        db_session,
        name="tpe-test-001",
        hypothesis="H",
        parameter_space={},
        pre_registered_criteria={},
    )
    db_session.commit()

    # _run_one_backtest is mocked. We need it to return a run_id AND set the
    # sharpe_ratio column on the just-created BacktestRun so optuna gets a
    # signal. The simplest path: instead of mocking _run_one_backtest, mock
    # the runner_factory and let the real _run_one_backtest do its thing.
    sharpe_per_trial = []

    async def runner_factory(run_id):
        # After _run_one_backtest creates the BacktestRun row with the trial
        # config, set sharpe_ratio so optuna gets a signal. Use vol_target
        # as a proxy (higher vol_target → higher fake sharpe to learn a trend).
        row = db_session.query(BacktestRun).filter(BacktestRun.id == run_id).one()
        cfg = row.config_overrides or {}
        fake_sharpe = float(cfg.get("vol_target", 0.0)) + 0.5  # 0.5 base + vol_target lift
        row.sharpe_ratio = fake_sharpe
        row.status = "completed"
        db_session.commit()
        sharpe_per_trial.append(fake_sharpe)

    from coordinator.services.validation.sweep import run_sweep
    result = await run_sweep(
        db=db_session,
        runner_factory=runner_factory,
        session_id=sess.id,
        manifest_path="/dummy/manifest.yaml",
        base_config={"start": "2024-01-01", "end": "2024-02-01"},
        parameter_space={"vol_target": [0.05, 0.30]},
        search="tpe",
        max_trials=8,
        seed=42,
        distributions={"vol_target": "uniform"},
        objective="sharpe_ratio",
        objective_direction="maximize",
    )
    assert result.n_configs == 8
    assert len(result.run_ids) == 8
    # 8 trials is below Optuna's TPE warmup (10 random trials by default), so
    # convergence isn't guaranteed yet. Verify the basics: every trial ran
    # and produced a recorded objective, and the range of explored sharpes
    # spans a meaningful chunk of the [0.55, 0.80] possible range.
    assert max(sharpe_per_trial) - min(sharpe_per_trial) > 0.05


@pytest.mark.asyncio
async def test_tpe_skips_runs_with_missing_objective(db_session):
    """When a backtest fails to populate the objective metric, TPE should
    skip it (study.tell with FAIL state) rather than crashing."""
    from coordinator.services.validation.optimization_session import create_session

    sess = create_session(
        db_session,
        name="tpe-test-002",
        hypothesis="H",
        parameter_space={},
        pre_registered_criteria={},
    )
    db_session.commit()

    async def runner_factory(run_id):
        # Do nothing — sharpe_ratio stays None
        pass

    from coordinator.services.validation.sweep import run_sweep
    # Should not crash even though every trial fails
    result = await run_sweep(
        db=db_session,
        runner_factory=runner_factory,
        session_id=sess.id,
        manifest_path="/dummy/manifest.yaml",
        base_config={"start": "2024-01-01", "end": "2024-02-01"},
        parameter_space={"vol_target": [0.05, 0.30]},
        search="tpe",
        max_trials=3,
        seed=1,
        distributions={"vol_target": "uniform"},
        objective="sharpe_ratio",
    )
    # All 3 trials ran (we still get run_ids) but optuna's record shows them as failed
    assert result.n_configs == 3
