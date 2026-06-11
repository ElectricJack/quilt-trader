"""Integration tests for run_cpcv (modes A and B)."""
from datetime import date
import json
import pytest
import pytest_asyncio
import uuid
from unittest.mock import AsyncMock

from coordinator.database.models import Algorithm, BacktestRun, OptimizationSession
from coordinator.services.validation.cpcv import run_cpcv


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


@pytest_asyncio.fixture
async def seeded_session(db_session):
    """Seed an Algorithm + Session row used by all tests."""
    algo_id = "test-algo"
    sess = OptimizationSession(
        name=f"sess-{uuid.uuid4().hex[:6]}",
        hypothesis="h",
        algorithm_id=algo_id,
        base_config={},
        parameter_space=json.dumps({}),
        pre_registered_criteria=json.dumps({}),
        status="open",
        date_range_start=date(2024, 1, 1),
        date_range_end=date(2024, 12, 31),
        initial_cash=100_000.0,
        cost_profile="default",
    )
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    return sess


def _complete_run(db, run_id: str, *, sharpe: float, n_bars: int = 100):
    """Update the existing BacktestRun row (created by the orchestrator) to completed."""
    row = db.query(BacktestRun).filter_by(id=run_id).one()
    row.status = "completed"
    row.sharpe_ratio = sharpe
    row.equity_curve = [{"timestamp": str(i), "equity": 1.0 + i * 0.001} for i in range(n_bars)]


@pytest.mark.asyncio
async def test_run_cpcv_mode_fixed_n4_yields_4_segments(seeded_session, db_session):
    """Mode A with N=4 dispatches 4 backtests and reports 4 segment Sharpes."""
    completed_run_ids: list[str] = []

    async def fake_runner(run_id, bars_cache=None):
        completed_run_ids.append(run_id)
        sharpe = 0.5 + len(completed_run_ids) * 0.1
        _complete_run(db_session, run_id, sharpe=sharpe)
        db_session.commit()

    result = await run_cpcv(
        db=db_session,
        runner_factory=fake_runner,
        session_id=seeded_session.id,
        mode="fixed",
        n_groups=4,
        test_groups_per_split=1,  # ignored in mode A
        embargo=0,
        purge_horizon=0,
        parallelism=1,
        bar_count_estimate=400,   # 100 per group
    )
    assert result.mode == "fixed"
    assert result.n_groups == 4
    assert len(result.segment_run_ids) == 4
    assert len(result.summary["segment_sharpes"]) == 4
    assert "bootstrap_ci_lower" in result.summary
    assert "bootstrap_ci_upper" in result.summary
    # paths empty in mode A
    assert result.paths == []


@pytest.mark.asyncio
async def test_run_cpcv_mode_fixed_median_correct_for_even_n(seeded_session, db_session):
    """Median of [0.6, 0.8, 1.0, 1.2] is 0.9, not 1.0."""
    runner_factory = AsyncMock()
    completed: list[str] = []
    sharpe_seq = [0.6, 0.8, 1.0, 1.2]

    async def fake_runner(run_id, bars_cache=None):
        completed.append(run_id)
        _complete_run(db_session, run_id, sharpe=sharpe_seq[len(completed) - 1])
        db_session.commit()

    runner_factory.side_effect = fake_runner

    result = await run_cpcv(
        db=db_session, runner_factory=runner_factory,
        session_id=seeded_session.id,
        mode="fixed", n_groups=4, test_groups_per_split=1,
        embargo=0, purge_horizon=0, parallelism=1, bar_count_estimate=400,
    )
    assert result.summary["median_segment_sharpe"] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_run_cpcv_rejects_select_without_required_params_early(seeded_session, db_session):
    """mode='select' without parameter_space raises ValueError immediately."""
    runner_factory = AsyncMock()  # should never be called
    with pytest.raises(ValueError, match="parameter_space"):
        await run_cpcv(
            db=db_session, runner_factory=runner_factory,
            session_id=seeded_session.id,
            mode="select",
            n_groups=4, test_groups_per_split=2,
            embargo=0, purge_horizon=0, parallelism=1,
        )
    runner_factory.assert_not_called()


@pytest.mark.asyncio
async def test_run_cpcv_mode_select_n4_k2_yields_paths(seeded_session, db_session, monkeypatch):
    """Mode B with N=4, k=2 → 6 splits, 3 reconstructed paths."""
    from coordinator.services.validation import cpcv as cpcv_mod
    from coordinator.services.validation.sweep import InnerSweepResult

    # Stub the inner sweep so we don't recursively dispatch dozens of backtests.
    async def fake_inner_sweep(**kwargs):
        # Return 3 trials worth of run IDs and a synthetic winner.
        ids = [f"inner-{uuid.uuid4().hex[:6]}" for _ in range(3)]
        return InnerSweepResult(
            all_run_ids=ids,
            winning_run_id=ids[0],
            winning_config={"lookback": 20},
            winning_objective=1.2,
        )

    monkeypatch.setattr(cpcv_mod, "_run_inner_sweep", fake_inner_sweep)

    runner_factory = AsyncMock()
    test_seg_runs: list[str] = []

    async def fake_runner(run_id, bars_cache=None):
        test_seg_runs.append(run_id)
        _complete_run(db_session, run_id, sharpe=0.5 + len(test_seg_runs) * 0.1)
        db_session.commit()

    runner_factory.side_effect = fake_runner

    result = await run_cpcv(
        db=db_session,
        runner_factory=runner_factory,
        session_id=seeded_session.id,
        mode="select",
        n_groups=4,
        test_groups_per_split=2,
        embargo=0,
        purge_horizon=0,
        parallelism=1,
        bar_count_estimate=400,
        parameter_space={"lookback": [10, 20, 30]},
        search="grid",
        max_trials_per_split=3,
        objective="sharpe_ratio",
        objective_direction="maximize",
        seed=0,
    )
    assert result.mode == "select"
    # C(4, 2) = 6 splits
    assert len(result.splits) == 6
    for split in result.splits:
        assert split.selected_config == {"lookback": 20}
        assert len(split.oos_segment_run_ids) == 2
    # C(3, 1) = 3 paths
    assert len(result.paths) == 3
    for path in result.paths:
        groups_in_path = [seg.group for seg in path]
        assert sorted(groups_in_path) == [0, 1, 2, 3]
    assert "path_sharpes" in result.summary
    assert len(result.summary["path_sharpes"]) == 3
    assert "deflated_sharpe_ratio" in result.summary
    assert "probabilistic_sharpe_zero" in result.summary
