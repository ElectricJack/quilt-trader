"""Combinatorial Purged Cross-Validation.

Per López de Prado, AFML Ch. 7 + Ch. 12 §12.4.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import combinations
from math import comb
from typing import Literal


@dataclass(frozen=True)
class Group:
    """One chronologically contiguous chunk of the timeline."""
    index: int
    start: date
    end: date
    start_index: int        # bar index within the full timeline
    end_index: int          # inclusive
    n_bars: int


@dataclass(frozen=True)
class Split:
    """One CPCV split — partitions group indices into train + test."""
    index: int
    train_groups: tuple[int, ...]
    test_groups: tuple[int, ...]


def compute_groups(
    timeline_start: date,
    timeline_end: date,
    bar_count: int,
    n_groups: int,
) -> list[Group]:
    """Partition [timeline_start, timeline_end] into n_groups equal-bar chunks.

    Bars are inferred linearly between start and end (this approximation works
    for the orchestrator because it slices on dates, not bar indices).
    """
    if n_groups < 4:
        raise ValueError(f"n_groups must be >= 4 (got {n_groups})")
    if timeline_end <= timeline_start:
        raise ValueError("timeline_end must be after timeline_start")
    if bar_count < n_groups:
        raise ValueError(f"bar_count ({bar_count}) must be >= n_groups ({n_groups})")

    total_days = (timeline_end - timeline_start).days + 1
    base = total_days // n_groups
    extras = total_days - base * n_groups
    bars_per = bar_count // n_groups
    bar_extras = bar_count - bars_per * n_groups

    groups: list[Group] = []
    day_cursor = 0
    bar_cursor = 0
    for i in range(n_groups):
        days_in_group = base + (1 if i < extras else 0)
        bars_in_group = bars_per + (1 if i < bar_extras else 0)
        start = timeline_start + timedelta(days=day_cursor)
        end = timeline_start + timedelta(days=day_cursor + days_in_group - 1)
        groups.append(Group(
            index=i, start=start, end=end,
            start_index=bar_cursor,
            end_index=bar_cursor + bars_in_group - 1,
            n_bars=bars_in_group,
        ))
        day_cursor += days_in_group
        bar_cursor += bars_in_group
    return groups


@dataclass(frozen=True)
class PathSegment:
    """One segment within a reconstructed path."""
    group: int
    split: int
    run_id: str


def reconstruct_paths(
    splits: list[Split],
    n_groups: int,
    test_groups_per_split: int,
    split_to_run_ids: dict[int, dict[int, str]],
) -> list[list[PathSegment]]:
    """Reconstruct C(N-1, k-1) full backtest paths from per-split test segments.

    Algorithm:
      - Each group appears as test in exactly C(N-1, k-1) splits.
      - For path j, group g uses the j-th split-index from g's appearance list.
      - This yields the invariant: each (group, split) cell appears in exactly one path.

    `split_to_run_ids[split_index][group_index] -> run_id` is the only data
    needed beyond split/group enumeration.
    """
    # For each group: the list of split indices where this group is test (in canonical order).
    group_splits: dict[int, list[int]] = {g: [] for g in range(n_groups)}
    for split in splits:
        for g in split.test_groups:
            group_splits[g].append(split.index)

    n_paths = comb(n_groups - 1, test_groups_per_split - 1)
    paths: list[list[PathSegment]] = []
    for path_idx in range(n_paths):
        path: list[PathSegment] = []
        for group_idx in range(n_groups):
            split_idx = group_splits[group_idx][path_idx]
            run_id = split_to_run_ids[split_idx][group_idx]
            path.append(PathSegment(group=group_idx, split=split_idx, run_id=run_id))
        paths.append(path)
    return paths


def compute_cpcv_splits(n_groups: int, test_groups_per_split: int) -> list[Split]:
    """Enumerate every C(N, k) train/test partition over n_groups groups."""
    if test_groups_per_split < 1:
        raise ValueError(f"test_groups_per_split must be >= 1 (got {test_groups_per_split})")
    if test_groups_per_split > n_groups // 2:
        raise ValueError(
            f"test_groups_per_split ({test_groups_per_split}) must be <= n_groups // 2 "
            f"({n_groups // 2})"
        )
    splits: list[Split] = []
    all_idx = set(range(n_groups))
    for i, test_combo in enumerate(combinations(range(n_groups), test_groups_per_split)):
        test_set = set(test_combo)
        train_set = sorted(all_idx - test_set)
        splits.append(Split(
            index=i,
            train_groups=tuple(train_set),
            test_groups=tuple(test_combo),
        ))
    return splits


# ---------------------------------------------------------------------------
# Orchestrator entry point (Tasks 8 + 9)
# ---------------------------------------------------------------------------

import asyncio
import uuid
from typing import Any, Callable, Awaitable
import pandas as pd
from sqlalchemy.orm import Session

from coordinator.database.models import BacktestRun, OptimizationSession
from coordinator.services.validation.bars_cache import BacktestBarsCache
from coordinator.services.validation.bootstrap import block_bootstrap_sharpe
from coordinator.services.validation.sweep import _run_inner_sweep
from coordinator.services.validation.multi_test import deflated_sharpe, probabilistic_sharpe


@dataclass
class SplitResult:
    """Mode-B per-split execution record (empty for mode A)."""
    index: int
    train_groups: tuple[int, ...]
    test_groups: tuple[int, ...]
    selected_config: dict | None = None
    selected_objective: float | None = None
    inner_trial_run_ids: list[str] = field(default_factory=list)
    oos_segment_run_ids: dict[int, str] = field(default_factory=dict)


@dataclass
class CPCVResult:
    """Orchestrator output, serialized into ResearchJob.result JSON."""
    mode: str
    n_groups: int
    test_groups_per_split: int
    embargo: int
    purge_horizon: int
    groups: list[Group] = field(default_factory=list)
    splits: list[SplitResult] = field(default_factory=list)
    segment_run_ids: list[str] = field(default_factory=list)   # mode A only
    paths: list[list[PathSegment]] = field(default_factory=list)  # mode B only
    summary: dict[str, Any] = field(default_factory=dict)


async def run_cpcv(
    db: Session,
    runner_factory,
    *,
    session_id: int,
    mode: str,
    n_groups: int = 6,
    test_groups_per_split: int = 2,
    embargo: int = 5,
    purge_horizon: int = 0,
    parallelism: int = 1,
    bar_count_estimate: int | None = None,
    # mode="select" only:
    parameter_space: dict | None = None,
    search: str | None = None,
    max_trials_per_split: int | None = None,
    objective: str = "sharpe_ratio",
    objective_direction: str = "maximize",
    seed: int = 0,
    progress_callback: Callable | None = None,
    bars_cache: "BacktestBarsCache | None" = None,
) -> CPCVResult:
    """Run CPCV in mode A (fixed) or mode B (select).

    Mode A: dispatches N backtests with base_config, one per group; reports
    per-segment Sharpes + bootstrap CI.

    Mode B: for each of C(N, k) splits, runs an inner sweep on train groups,
    selects the best config, evaluates on test groups; reconstructs paths.
    """
    if mode == "select" and (
        parameter_space is None or search is None or max_trials_per_split is None
    ):
        raise ValueError(
            "mode='select' requires parameter_space, search, and max_trials_per_split"
        )
    session = db.query(OptimizationSession).filter_by(id=session_id).one()
    if bar_count_estimate is None:
        # Conservative default: assume 252 bars/year on daily data.
        years = (session.date_range_end - session.date_range_start).days / 365.0
        bar_count_estimate = max(int(years * 252), n_groups * 10)

    groups = compute_groups(
        timeline_start=session.date_range_start,
        timeline_end=session.date_range_end,
        bar_count=bar_count_estimate,
        n_groups=n_groups,
    )

    result = CPCVResult(
        mode=mode, n_groups=n_groups, test_groups_per_split=test_groups_per_split,
        embargo=embargo, purge_horizon=purge_horizon, groups=groups,
    )

    if mode == "fixed":
        await _run_mode_fixed(
            db=db, runner_factory=runner_factory, session=session,
            groups=groups, embargo=embargo, parallelism=parallelism,
            bars_cache=bars_cache, progress_callback=progress_callback,
            result=result,
        )
    else:
        await _run_mode_select(
            db=db, runner_factory=runner_factory, session=session,
            groups=groups, embargo=embargo, purge_horizon=purge_horizon,
            parameter_space=parameter_space, search=search,
            max_trials_per_split=max_trials_per_split,
            objective=objective, objective_direction=objective_direction,
            parallelism=parallelism, seed=seed,
            bars_cache=bars_cache, progress_callback=progress_callback,
            result=result, test_groups_per_split=test_groups_per_split,
        )
    return result


async def _run_mode_fixed(
    *, db, runner_factory, session, groups, embargo,
    parallelism, bars_cache, progress_callback, result: CPCVResult,
) -> None:
    sem = asyncio.Semaphore(parallelism)

    # Track completion order separately from group order
    group_to_run_id: dict[int, str] = {}

    async def _one(group: Group) -> tuple[int, str]:
        async with sem:
            run_id = f"cpcv-{uuid.uuid4().hex[:8]}"
            db.add(BacktestRun(
                id=run_id, algorithm_id=session.algorithm_id,
                optimization_session_id=session.id,
                status="queued",
                config_overrides=session.base_config or {},
                date_range_start=group.start,
                date_range_end=group.end,
            ))
            db.commit()
            await runner_factory(run_id, bars_cache=bars_cache)
            return group.index, run_id

    tasks = [_one(g) for g in groups]
    completed: list[tuple[int, str]] = []
    for coro in asyncio.as_completed(tasks):
        group_idx, rid = await coro
        group_to_run_id[group_idx] = rid
        completed.append((group_idx, rid))
        if progress_callback:
            run_ids_so_far = [r for _, r in completed]
            progress_callback(
                len(completed) / len(groups),
                f"group {len(completed)}/{len(groups)} evaluated",
                run_ids_so_far,
            )

    # Ordered by group index for deterministic result
    result.segment_run_ids = [group_to_run_id[i] for i in range(len(groups))]

    sharpes: list[float] = []
    concat_returns: list[float] = []
    for rid in result.segment_run_ids:
        row = db.query(BacktestRun).filter_by(id=rid).one()
        sharpes.append(float(row.sharpe_ratio or 0.0))
        if row.equity_curve:
            equity = pd.Series([float(p.get("equity", 1.0)) for p in row.equity_curve])
            rets = equity.pct_change().dropna().tolist()
            concat_returns.extend(rets)

    n = len(sharpes)
    mean_sharpe = sum(sharpes) / n if n > 0 else 0.0
    if n == 0:
        median_sharpe = 0.0
    else:
        s = sorted(sharpes)
        median_sharpe = s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2.0
    std_sharpe = (sum((s - mean_sharpe) ** 2 for s in sharpes) / max(n - 1, 1)) ** 0.5

    ci_lower, ci_upper = 0.0, 0.0
    if concat_returns:
        equity_curve = pd.Series((1.0 + pd.Series(concat_returns)).cumprod().values)
        ci = block_bootstrap_sharpe(
            equity_curve,
            block_size=max(20, len(concat_returns) // 20),
            n_resamples=2000,
            confidence=0.95,
            periods_per_year=252,
            seed=0,
        )
        ci_lower, ci_upper = ci.lower, ci.upper

    result.summary = {
        "segment_sharpes": sharpes,
        "mean_segment_sharpe": mean_sharpe,
        "median_segment_sharpe": median_sharpe,
        "std_segment_sharpe": std_sharpe,
        "bootstrap_ci_lower": ci_lower,
        "bootstrap_ci_upper": ci_upper,
    }


async def _run_mode_select(
    *, db, runner_factory, session, groups, embargo, purge_horizon,
    parameter_space, search, max_trials_per_split,
    objective, objective_direction,
    parallelism, seed, bars_cache, progress_callback,
    result: CPCVResult, test_groups_per_split: int,
) -> None:
    splits = compute_cpcv_splits(
        n_groups=len(groups),
        test_groups_per_split=test_groups_per_split,
    )
    sem = asyncio.Semaphore(parallelism)
    all_run_ids_completed: list[str] = []

    async def _process_split(split: Split) -> SplitResult:
        async with sem:
            train_start = groups[split.train_groups[0]].start
            train_end = groups[split.train_groups[-1]].end
            inner = await _run_inner_sweep(
                db=db, runner_factory=runner_factory,
                session_id=session.id,
                algorithm_id=session.algorithm_id,
                date_range_start=train_start, date_range_end=train_end,
                initial_cash=session.initial_cash,
                cost_profile=session.cost_profile,
                benchmark_source=session.benchmark_source,
                benchmark_symbol=session.benchmark_symbol,
                mtm_realism=session.mtm_realism,
                base_config=session.base_config or {},
                parameter_space=parameter_space,
                search=search,
                max_trials=max_trials_per_split,
                parallelism=1,  # outer split loop handles parallelism
                seed=seed + split.index,
                objective=objective,
                objective_direction=objective_direction,
                progress_callback=None,
                bars_cache=bars_cache,
            )

            oos_runs: dict[int, str] = {}
            for g_idx in split.test_groups:
                rid = f"cpcv-oos-{uuid.uuid4().hex[:8]}"
                g = groups[g_idx]
                db.add(BacktestRun(
                    id=rid, algorithm_id=session.algorithm_id,
                    optimization_session_id=session.id,
                    status="queued",
                    config_overrides=inner.winning_config or {},
                    date_range_start=g.start, date_range_end=g.end,
                ))
                db.commit()
                await runner_factory(rid, bars_cache=bars_cache)
                oos_runs[g_idx] = rid

            return SplitResult(
                index=split.index,
                train_groups=split.train_groups,
                test_groups=split.test_groups,
                selected_config=inner.winning_config,
                selected_objective=inner.winning_objective,
                inner_trial_run_ids=inner.all_run_ids,
                oos_segment_run_ids=oos_runs,
            )

    split_results: list[SplitResult] = []
    tasks = [_process_split(s) for s in splits]
    for coro in asyncio.as_completed(tasks):
        sr = await coro
        split_results.append(sr)
        all_run_ids_completed.extend(sr.inner_trial_run_ids)
        all_run_ids_completed.extend(sr.oos_segment_run_ids.values())
        if progress_callback:
            progress_callback(
                len(split_results) / len(splits),
                f"split {len(split_results)}/{len(splits)} complete",
                all_run_ids_completed,
            )

    # Sort by split index for deterministic output
    split_results.sort(key=lambda s: s.index)
    result.splits = split_results

    # Path reconstruction
    split_to_run_ids = {sr.index: sr.oos_segment_run_ids for sr in split_results}
    paths = reconstruct_paths(
        splits=splits,
        n_groups=len(groups),
        test_groups_per_split=test_groups_per_split,
        split_to_run_ids=split_to_run_ids,
    )
    result.paths = paths

    # Per-path Sharpe
    path_sharpes: list[float] = []
    path_returns_for_dsr: list[float] = []
    for path in paths:
        path_returns: list[float] = []
        for seg in path:
            row = db.query(BacktestRun).filter_by(id=seg.run_id).one()
            if row.equity_curve:
                equity = pd.Series([float(p.get("equity", 1.0)) for p in row.equity_curve])
                rets = equity.pct_change().dropna().tolist()
                path_returns.extend(rets)
        if path_returns:
            arr = pd.Series(path_returns)
            std = arr.std(ddof=1)
            sr = float(arr.mean() / std * (252 ** 0.5)) if std > 0 else 0.0
            path_sharpes.append(sr)
        else:
            path_sharpes.append(0.0)

    # Pick best path (highest Sharpe) for DSR's moment input
    if path_sharpes:
        best_idx = max(range(len(path_sharpes)), key=lambda i: path_sharpes[i])
        best_returns: list[float] = []
        for seg in paths[best_idx]:
            row = db.query(BacktestRun).filter_by(id=seg.run_id).one()
            if row.equity_curve:
                equity = pd.Series([float(p.get("equity", 1.0)) for p in row.equity_curve])
                best_returns.extend(equity.pct_change().dropna().tolist())
        path_returns_for_dsr = best_returns

    n = len(path_sharpes)
    mean_p = sum(path_sharpes) / n if n > 0 else 0.0
    if n == 0:
        median_p = 0.0
    else:
        ss = sorted(path_sharpes)
        median_p = ss[n // 2] if n % 2 == 1 else (ss[n // 2 - 1] + ss[n // 2]) / 2.0
    std_p = (sum((s - mean_p) ** 2 for s in path_sharpes) / max(n - 1, 1)) ** 0.5

    ci_lower, ci_upper = 0.0, 0.0
    if path_returns_for_dsr:
        equity = pd.Series((1.0 + pd.Series(path_returns_for_dsr)).cumprod().values)
        ci = block_bootstrap_sharpe(
            equity=equity,
            block_size=max(20, len(path_returns_for_dsr) // 20),
            n_resamples=2000, confidence=0.95,
            periods_per_year=252, seed=seed,
        )
        ci_lower, ci_upper = ci.lower, ci.upper

    dsr = 0.0
    psr_zero = 0.0
    if path_returns_for_dsr:
        n_trials = len(splits) * max_trials_per_split
        dsr = deflated_sharpe(
            sharpes=path_sharpes,
            returns_of_best=path_returns_for_dsr,
            n_trials=n_trials,
            periods_per_year=252,
        )
        psr_zero = probabilistic_sharpe(
            returns=path_returns_for_dsr,
            sharpe_benchmark=0.0,
            periods_per_year=252,
        )

    result.summary = {
        "path_sharpes": path_sharpes,
        "mean_path_sharpe": mean_p,
        "median_path_sharpe": median_p,
        "std_path_sharpe": std_p,
        "bootstrap_ci_lower": ci_lower,
        "bootstrap_ci_upper": ci_upper,
        "deflated_sharpe_ratio": dsr,
        "probabilistic_sharpe_zero": psr_zero,
    }
