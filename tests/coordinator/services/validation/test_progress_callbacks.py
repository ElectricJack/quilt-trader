"""Verify run_sweep + run_walk_forward invoke progress_callback per trial/fold."""
from datetime import date
from unittest.mock import AsyncMock, MagicMock
import pytest


@pytest.mark.asyncio
async def test_sweep_invokes_progress_callback_per_trial(monkeypatch):
    """run_sweep should call progress_callback(pct, message, run_ids) after each
    trial's BacktestRun row is committed."""
    from coordinator.services.validation import sweep as sweep_mod

    # Stub out _run_one_backtest so we don't actually drive the engine.
    counter = {"i": 0}
    async def fake_run_one(db, runner_factory, *, base_config, config, config_hash_str, **_kw):
        counter["i"] += 1
        return {"run_id": f"r-{counter['i']}", "objective": 1.0,
                "config": {**base_config, **config}}
    monkeypatch.setattr(sweep_mod, "_run_one_backtest", fake_run_one)

    progress_log: list = []
    async def cb(pct, message, run_ids):
        progress_log.append((pct, message, list(run_ids)))

    result = await sweep_mod.run_sweep(
        MagicMock(),
        AsyncMock(),
        session_id=1,
        manifest_path="x.yaml",
        algorithm_id="a",
        date_range_start=date(2024, 1, 1),
        date_range_end=date(2024, 2, 1),
        initial_cash=10000.0,
        cost_profile="default",
        benchmark_symbol=None,
        benchmark_source=None,
        base_config={},
        parameter_space={"vol_target": [0.1, 0.15, 0.2]},
        search="grid", max_trials=3, parallelism=1, seed=0,
        progress_callback=cb,
    )

    # Three trials -> three progress updates ending at 1.0
    assert len(progress_log) == 3
    assert progress_log[-1][0] == 1.0
    # final tick carries all 3 run_ids
    assert len(progress_log[-1][2]) == 3
    assert result.n_configs == 3


@pytest.mark.asyncio
async def test_sweep_no_callback_works(monkeypatch):
    """Passing no progress_callback is a valid no-op."""
    from coordinator.services.validation import sweep as sweep_mod

    counter = {"i": 0}
    async def fake_run_one(db, runner_factory, *, base_config, config, config_hash_str, **_kw):
        counter["i"] += 1
        return {"run_id": f"r-{counter['i']}", "objective": 1.0}
    monkeypatch.setattr(sweep_mod, "_run_one_backtest", fake_run_one)

    result = await sweep_mod.run_sweep(
        MagicMock(), AsyncMock(),
        session_id=1, manifest_path="x.yaml",
        algorithm_id="a",
        date_range_start=date(2024, 1, 1),
        date_range_end=date(2024, 2, 1),
        initial_cash=10000.0,
        cost_profile="default",
        benchmark_symbol=None,
        benchmark_source=None,
        base_config={},
        parameter_space={"vol_target": [0.1]},
        search="grid", max_trials=1, parallelism=1, seed=0,
        # progress_callback intentionally omitted
    )
    assert result.n_configs == 1


@pytest.mark.asyncio
async def test_walk_forward_invokes_progress_callback_per_fold(monkeypatch):
    """Same idea for walk-forward: callback fires after each fold completes."""
    from coordinator.services.validation import walk_forward as wf_mod

    # Stub _run_oos_backtest to return a string id per fold (it's the unit
    # accumulated into oos_run_ids; run_sweep is called inside but produces
    # train run_ids that walk-forward doesn't fire progress on).
    async def fake_oos(db, runner_factory, *, fold_index, **_kw):
        return f"oos-{fold_index}"
    monkeypatch.setattr(wf_mod, "_run_oos_backtest", fake_oos)

    # Stub _pick_best_train_config so we don't need real BacktestRun rows.
    async def fake_pick(db, run_ids, objective):
        return {"vol_target": 0.15}
    monkeypatch.setattr(wf_mod, "_pick_best_train_config", fake_pick)

    # Stub run_sweep (inside walk_forward, imported at module level) so the
    # train sweeps don't actually drive _run_one_backtest.
    async def fake_sweep(db, runner_factory, *, session_id, manifest_path,
                        base_config, parameter_space, search, max_trials,
                        parallelism, **_kw):
        # progress_callback for the train sweep is NOT passed through by
        # walk_forward — it's separate from the OOS progress.
        from coordinator.services.validation.sweep import SweepResult
        return SweepResult(session_id=session_id, n_configs=2, run_ids=["t1", "t2"])
    monkeypatch.setattr(wf_mod, "run_sweep", fake_sweep)

    progress_log: list = []
    async def cb(pct, message, run_ids):
        progress_log.append((pct, message, list(run_ids)))

    result = await wf_mod.run_walk_forward(
        MagicMock(), AsyncMock(),
        session_id=1, manifest_path="x.yaml",
        algorithm_id="a",
        date_range_start=date(2018, 1, 1),
        date_range_end=date(2024, 1, 1),
        initial_cash=10000.0,
        cost_profile="default",
        benchmark_symbol=None,
        benchmark_source=None,
        base_config={},
        parameter_space={"vol_target": [0.1, 0.15]},
        train_years=4.0, test_years=1.0, step_months=12.0,
        objective="sharpe", parallelism=1,
        progress_callback=cb,
    )
    # Should have >= 1 fold (compute_folds with these settings)
    assert result.n_folds >= 1
    assert len(progress_log) == result.n_folds
    assert progress_log[-1][0] == 1.0
    assert progress_log[-1][2] == result.oos_run_ids
