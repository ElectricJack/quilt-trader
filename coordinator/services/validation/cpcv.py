"""Combinatorial Purged Cross-Validation.

Per López de Prado, AFML Ch. 7 + Ch. 12 §12.4.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import combinations
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
