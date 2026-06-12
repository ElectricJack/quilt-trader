"""Failing tests (TDD red phase) for correctness-audit findings F11 and F12
in coordinator/services/validation/cpcv.py.

Source: docs/superpowers/research/2026-06-11-correctness-audit-findings.md
Each test asserts the CORRECT behavior; it fails today because the bug exists
and must pass once the production code is fixed. No production code modified.
"""
from datetime import date, timedelta
import json
import uuid

import pytest
import pytest_asyncio

from coordinator.database.models import Algorithm, BacktestRun, OptimizationSession
from coordinator.services.validation.cpcv import compute_groups, run_cpcv


@pytest.fixture
def db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from coordinator.database.models import Base

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
    sess = OptimizationSession(
        name=f"sess-{uuid.uuid4().hex[:6]}",
        hypothesis="h",
        algorithm_id="test-algo",
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


def _complete_run(db, run_id: str, *, sharpe: float, with_equity: bool, n_bars: int = 100):
    """Mark the orchestrator-created BacktestRun row completed, optionally
    leaving equity_curve missing (None)."""
    row = db.query(BacktestRun).filter_by(id=run_id).one()
    row.status = "completed"
    row.sharpe_ratio = sharpe
    if with_equity:
        row.equity_curve = [
            {"timestamp": str(i), "equity": 1.0 + i * 0.001} for i in range(n_bars)
        ]


def _patch_inner_sweep(monkeypatch, captured_windows=None):
    """Stub _run_inner_sweep; optionally capture (train_start, train_end)."""
    from coordinator.services.validation import cpcv as cpcv_mod
    from coordinator.services.validation.sweep import InnerSweepResult

    async def fake_inner_sweep(**kwargs):
        if captured_windows is not None:
            captured_windows.append(
                (kwargs["date_range_start"], kwargs["date_range_end"])
            )
        ids = [f"inner-{uuid.uuid4().hex[:6]}"]
        return InnerSweepResult(
            all_run_ids=ids, winning_run_id=ids[0],
            winning_config={"x": 1}, winning_objective=1.0,
        )

    monkeypatch.setattr(cpcv_mod, "_run_inner_sweep", fake_inner_sweep)


# ---------------------------------------------------------------------------
# F11 — CPCV silently drops segments lacking equity_curve
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_f11_mode_fixed_missing_equity_curve_raises(seeded_session, db_session):
    """F11: cpcv.py:339-342 (mode A aggregation) skips a completed segment
    whose BacktestRun row has no equity_curve via `if row.equity_curve:` —
    the run completes silently with fewer segments (survivorship bias
    inside the validation tool). Correct: raise ValueError naming the
    segment. Buggy: returns a result built from 3 of 4 segments.
    """
    completed: list[str] = []

    async def fake_runner(run_id, bars_cache=None):
        completed.append(run_id)
        # The 3rd segment completes WITHOUT an equity curve.
        _complete_run(
            db_session, run_id, sharpe=0.5 + len(completed) * 0.1,
            with_equity=(len(completed) != 3),
        )
        db_session.commit()

    with pytest.raises(ValueError):
        await run_cpcv(
            db=db_session, runner_factory=fake_runner,
            session_id=seeded_session.id,
            mode="fixed", n_groups=4, test_groups_per_split=1,
            embargo=0, purge_horizon=0, parallelism=1, bar_count_estimate=400,
        )


@pytest.mark.asyncio
async def test_f11_mode_select_path_segment_missing_equity_curve_raises(
    seeded_session, db_session, monkeypatch,
):
    """F11: cpcv.py:487-497 (mode B per-path Sharpe) silently drops a path
    segment whose run row has no equity_curve — the path Sharpe is built
    from fewer segments without any signal. Correct: raise ValueError
    naming the segment. Buggy: completes with a quietly degraded path.
    """
    _patch_inner_sweep(monkeypatch)
    completed_oos: list[str] = []

    async def fake_runner(run_id, bars_cache=None):
        is_oos = run_id.startswith("cpcv-oos-")
        if is_oos:
            completed_oos.append(run_id)
        # The 5th OOS segment completes WITHOUT an equity curve.
        _complete_run(
            db_session, run_id, sharpe=0.8,
            with_equity=not (is_oos and len(completed_oos) == 5),
        )
        db_session.commit()

    with pytest.raises(ValueError):
        await run_cpcv(
            db=db_session, runner_factory=fake_runner,
            session_id=seeded_session.id,
            mode="select", n_groups=4, test_groups_per_split=2,
            embargo=0, purge_horizon=0, parallelism=1, bar_count_estimate=400,
            parameter_space={"x": [1]}, search="grid", max_trials_per_split=1,
        )


# ---------------------------------------------------------------------------
# F12 — CPCV purge/embargo applied on the wrong side
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_f12_train_window_following_test_group_gets_embargo_gap(
    seeded_session, db_session, monkeypatch,
):
    """F12: cpcv.py:399/428 — per Lopez de Prado, embargo must exclude
    TRAIN data immediately AFTER the test window; cpcv.py instead shifts
    the TEST start (line ~428) and only ever trims the train END (line
    ~399), never the train start. For the split with test_groups=(0, 1)
    and train run (2, 3), the train window START must be >=
    test_end + embargo days. Buggy: train start abuts the test end
    (groups[2].start = groups[1].end + 1 day).
    """
    EMBARGO = 7
    captured_windows: list[tuple] = []
    _patch_inner_sweep(monkeypatch, captured_windows)

    async def fake_runner(run_id, bars_cache=None):
        _complete_run(db_session, run_id, sharpe=1.0, with_equity=True)
        db_session.commit()

    await run_cpcv(
        db=db_session, runner_factory=fake_runner,
        session_id=seeded_session.id,
        mode="select", n_groups=4, test_groups_per_split=2,
        embargo=EMBARGO, purge_horizon=0, parallelism=1, bar_count_estimate=400,
        parameter_space={"x": [1]}, search="grid", max_trials_per_split=1,
    )

    groups = compute_groups(
        timeline_start=seeded_session.date_range_start,
        timeline_end=seeded_session.date_range_end,
        bar_count=400, n_groups=4,
    )
    # The train window for split test=(0,1) is the contiguous run (2,3) —
    # uniquely identifiable as the latest-ending captured train window.
    assert captured_windows, "inner sweep was never invoked"
    train_start, train_end = max(captured_windows, key=lambda w: w[1])
    test_end = groups[1].end
    min_allowed_start = test_end + timedelta(days=EMBARGO)
    assert train_start >= min_allowed_start, (
        f"train window following test group must start >= test_end + embargo "
        f"({min_allowed_start}), got {train_start} (abuts test end {test_end})"
    )


@pytest.mark.asyncio
async def test_f12_test_windows_not_shifted_by_embargo(
    seeded_session, db_session, monkeypatch,
):
    """F12: cpcv.py:428 shifts the OOS TEST window start forward by the
    embargo. Per Lopez de Prado, test windows stay full-size — embargo
    excludes TRAIN samples after the test window instead. Every mode-B
    OOS segment must start exactly at its group's start. Buggy: starts at
    group.start + embargo days.
    """
    EMBARGO = 7
    _patch_inner_sweep(monkeypatch)
    captured_starts: list = []

    async def fake_runner(run_id, bars_cache=None):
        row = db_session.query(BacktestRun).filter_by(id=run_id).one()
        if run_id.startswith("cpcv-oos-"):
            start = row.date_range_start
            captured_starts.append(start.date() if hasattr(start, "date") else start)
        _complete_run(db_session, run_id, sharpe=1.0, with_equity=True)
        db_session.commit()

    await run_cpcv(
        db=db_session, runner_factory=fake_runner,
        session_id=seeded_session.id,
        mode="select", n_groups=4, test_groups_per_split=2,
        embargo=EMBARGO, purge_horizon=0, parallelism=1, bar_count_estimate=400,
        parameter_space={"x": [1]}, search="grid", max_trials_per_split=1,
    )

    groups = compute_groups(
        timeline_start=seeded_session.date_range_start,
        timeline_end=seeded_session.date_range_end,
        bar_count=400, n_groups=4,
    )
    expected_starts = {g.start for g in groups}
    assert set(captured_starts) == expected_starts, (
        f"test windows must start at group starts {sorted(expected_starts)}, "
        f"got {sorted(set(captured_starts))} (shifted by embargo)"
    )
