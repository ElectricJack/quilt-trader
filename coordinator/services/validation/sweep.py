from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
from collections.abc import Awaitable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional

# A RunnerFactory is any callable that accepts a persisted BacktestRun id and
# returns an awaitable that executes the run.  The orchestrators are agnostic
# about which services the factory uses; the CLI / API constructs it once with
# real services and passes it through.
RunnerFactory = Callable[[str], Awaitable[None]]

import numpy as np


def expand_grid(parameter_space: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Cross-product expansion of a parameter space.

    Each key in parameter_space maps to a list of candidate values.
    Returns a list of fully-specified config dicts.
    """
    if not parameter_space:
        return [{}]
    keys = list(parameter_space.keys())
    value_lists = [parameter_space[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*value_lists)]


def config_hash(config: dict[str, Any]) -> str:
    """Stable hash of a config dict (key-order-independent)."""
    payload = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def sample_random(
    parameter_space: dict[str, list[Any]],
    n: int,
    seed: int,
    distributions: dict[str, str],
) -> list[dict[str, Any]]:
    """Random sampling from a continuous-bounded parameter space.

    parameter_space: key -> [low, high]
    distributions: key -> "uniform" | "log_uniform" | "int_uniform"
    """
    rng = np.random.default_rng(seed)
    configs: list[dict[str, Any]] = []
    for _ in range(n):
        cfg: dict[str, Any] = {}
        for key, bounds in parameter_space.items():
            lo, hi = bounds[0], bounds[1]
            dist = distributions.get(key, "uniform")
            if dist == "uniform":
                cfg[key] = float(rng.uniform(lo, hi))
            elif dist == "log_uniform":
                cfg[key] = float(np.exp(rng.uniform(np.log(lo), np.log(hi))))
            elif dist == "int_uniform":
                cfg[key] = int(rng.integers(lo, hi + 1))
            else:
                raise ValueError(f"Unknown distribution: {dist}")
        configs.append(cfg)
    return configs


def sample_latin_hypercube(
    parameter_space: dict[str, list[Any]],
    n: int,
    seed: int,
    distributions: dict[str, str],
) -> list[dict[str, Any]]:
    """Latin-hypercube sampling: each parameter is binned into n strata, each
    stratum sampled exactly once."""
    rng = np.random.default_rng(seed)
    keys = list(parameter_space.keys())
    columns: dict[str, list[Any]] = {}
    for key in keys:
        lo, hi = parameter_space[key][0], parameter_space[key][1]
        dist = distributions.get(key, "uniform")
        edges = np.linspace(0.0, 1.0, n + 1)
        offsets = rng.uniform(0, 1.0 / n, n)
        unit = edges[:-1] + offsets
        rng.shuffle(unit)
        if dist == "uniform":
            vals = lo + unit * (hi - lo)
            columns[key] = [float(v) for v in vals]
        elif dist == "log_uniform":
            log_lo, log_hi = np.log(lo), np.log(hi)
            vals = np.exp(log_lo + unit * (log_hi - log_lo))
            columns[key] = [float(v) for v in vals]
        elif dist == "int_uniform":
            vals = lo + unit * (hi - lo + 1)
            columns[key] = [int(v) for v in vals]
        else:
            raise ValueError(f"Unknown distribution: {dist}")
    return [dict(zip(keys, row)) for row in zip(*[columns[k] for k in keys])]


@dataclass
class SweepResult:
    session_id: int
    n_configs: int
    run_ids: list[str] = field(default_factory=list)


async def _run_one_backtest(
    db: Any,
    runner_factory: RunnerFactory,
    *,
    session_id: int,
    base_config: dict[str, Any],
    config: dict[str, Any],
    config_hash_str: str,
) -> dict[str, Any]:
    """Spawn a single backtest with the given config.

    Creates a ``BacktestRun`` row, flushes to obtain its id, then delegates
    execution to ``runner_factory``.  Returns a dict with at least ``run_id``,
    ``config_hash``, and ``config``.  This function is the seam mocked in tests.
    """
    from datetime import date, datetime

    from coordinator.database.models import BacktestRun

    merged = {**base_config, **config}

    def _as_date(v):
        if v is None or isinstance(v, (date, datetime)):
            return v
        if isinstance(v, str):
            return date.fromisoformat(v)
        raise TypeError(f"Cannot coerce {v!r} to date")

    run_row = BacktestRun(
        algorithm_id=merged.get("algorithm_id", ""),
        date_range_start=_as_date(merged.get("start")),
        date_range_end=_as_date(merged.get("end")),
        initial_cash=float(merged.get("initial_cash", 1000.0)),
        config_overrides=merged,
        config_hash=config_hash_str,
        optimization_session_id=session_id,
        status="pending",
    )
    db.add(run_row)
    db.flush()
    db.commit()  # make row visible to async runner via its own separate connection

    run_id = run_row.id
    await runner_factory(run_id)

    db.refresh(run_row)  # pull updated status/metrics written by the runner

    return {
        "run_id": run_row.id,
        "config_hash": config_hash_str,
        "config": config,
    }


async def run_sweep(
    db: Any,
    runner_factory: RunnerFactory,
    *,
    session_id: int,
    manifest_path: str | Path,
    base_config: dict[str, Any],
    parameter_space: dict[str, Any],
    search: Literal["grid", "random", "latin", "tpe"] = "grid",
    max_trials: int = 50,
    parallelism: int = 1,
    seed: int = 0,
    distributions: Optional[dict[str, str]] = None,
    objective: str = "sharpe_ratio",
    objective_direction: Literal["maximize", "minimize"] = "maximize",
) -> SweepResult:
    """Run a parameter sweep under an existing OptimizationSession.

    Expands or samples ``parameter_space`` according to ``search``, then
    dispatches up to ``parallelism`` concurrent backtests via
    ``_run_one_backtest``.  Returns a :class:`SweepResult` summarising
    the session once all trials have completed.

    Search strategies:
    - ``grid``: cross-product of all parameter values (up to max_trials).
    - ``random``: independent uniform samples from bounded ranges.
    - ``latin``: Latin-hypercube samples — better space coverage than random.
    - ``tpe``: Tree-Parzen Estimator (Bayesian) via Optuna. Each trial's
      result steers the next trial's parameters. Inherently sequential,
      so ``parallelism`` is ignored for this strategy.

    For ``tpe``, ``objective`` is the BacktestRun column to optimize
    (default 'sharpe_ratio'). ``objective_direction`` is 'maximize' or
    'minimize'.
    """
    if search == "tpe":
        return await _run_sweep_tpe(
            db, runner_factory,
            session_id=session_id, base_config=base_config,
            parameter_space=parameter_space, max_trials=max_trials,
            seed=seed, distributions=distributions or {},
            objective=objective, objective_direction=objective_direction,
        )

    if search == "grid":
        configs = expand_grid(parameter_space)[:max_trials]
    elif search == "random":
        configs = sample_random(
            parameter_space, n=max_trials, seed=seed,
            distributions=distributions or {},
        )
    elif search == "latin":
        configs = sample_latin_hypercube(
            parameter_space, n=max_trials, seed=seed,
            distributions=distributions or {},
        )
    else:
        raise ValueError(f"Unknown search strategy: {search!r}")

    semaphore = asyncio.Semaphore(parallelism)

    async def _bounded(cfg: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await _run_one_backtest(
                db,
                runner_factory,
                session_id=session_id,
                base_config=base_config,
                config=cfg,
                config_hash_str=config_hash(cfg),
            )

    results = await asyncio.gather(*[_bounded(c) for c in configs])

    return SweepResult(
        session_id=session_id,
        n_configs=len(configs),
        run_ids=[r["run_id"] for r in results if "run_id" in r],
    )


async def _run_sweep_tpe(
    db: Any,
    runner_factory: RunnerFactory,
    *,
    session_id: int,
    base_config: dict[str, Any],
    parameter_space: dict[str, Any],
    max_trials: int,
    seed: int,
    distributions: dict[str, str],
    objective: str,
    objective_direction: str,
) -> SweepResult:
    """Bayesian / Tree-Parzen-Estimator sweep via Optuna.

    Sequential by nature — Optuna's TPE conditions each trial on prior
    trials' results. The orchestrator runs one backtest, reads its
    `objective` metric off the DB row, reports it back to Optuna, then
    asks for the next trial's params.

    parameter_space here supports both discrete-grid form (key -> [v1, v2,
    ...]) AND continuous-bounds form (key -> [low, high]) via the
    ``distributions`` dict ({"vol_target": "uniform", "lookback": "int"}).
    Discrete keys (no distribution declared) fall back to `suggest_categorical`.
    """
    try:
        import optuna
    except ImportError as e:
        raise RuntimeError(
            "TPE search requires the optuna package. Install with: pip install optuna"
        ) from e

    # Quiet optuna's default per-trial INFO output — we have our own logging.
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    sampler = optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction=objective_direction, sampler=sampler)
    run_ids: list[str] = []

    from coordinator.database.models import BacktestRun

    def _suggest(trial: "optuna.Trial") -> dict[str, Any]:
        cfg: dict[str, Any] = {}
        for key, candidates in parameter_space.items():
            dist = distributions.get(key)
            if dist == "uniform":
                lo, hi = float(candidates[0]), float(candidates[1])
                cfg[key] = trial.suggest_float(key, lo, hi)
            elif dist == "log_uniform":
                lo, hi = float(candidates[0]), float(candidates[1])
                cfg[key] = trial.suggest_float(key, lo, hi, log=True)
            elif dist == "int_uniform":
                lo, hi = int(candidates[0]), int(candidates[1])
                cfg[key] = trial.suggest_int(key, lo, hi)
            else:
                # Discrete grid value
                cfg[key] = trial.suggest_categorical(key, list(candidates))
        return cfg

    for trial_idx in range(max_trials):
        trial = study.ask()
        cfg = _suggest(trial)
        result = await _run_one_backtest(
            db,
            runner_factory,
            session_id=session_id,
            base_config=base_config,
            config=cfg,
            config_hash_str=config_hash(cfg),
        )
        run_id = result.get("run_id")
        if run_id is None:
            # Test mocks return without run_id; tell optuna we failed and skip
            study.tell(trial, state=optuna.trial.TrialState.FAIL)
            continue
        run_ids.append(run_id)
        # Read the objective from the just-completed BacktestRun
        row = db.query(BacktestRun).filter(BacktestRun.id == run_id).one_or_none()
        if row is None or getattr(row, objective, None) is None:
            study.tell(trial, state=optuna.trial.TrialState.FAIL)
            continue
        study.tell(trial, float(getattr(row, objective)))

    return SweepResult(
        session_id=session_id,
        n_configs=len(run_ids),
        run_ids=run_ids,
    )
