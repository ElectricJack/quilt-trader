from datetime import date
import pytest

from coordinator.services.validation.cpcv import (
    Group,
    compute_groups,
    compute_cpcv_splits,
    Split,
)


def test_compute_groups_divides_timeline_evenly():
    groups = compute_groups(
        timeline_start=date(2024, 1, 1),
        timeline_end=date(2024, 12, 31),
        bar_count=300,  # arbitrary
        n_groups=6,
    )
    assert len(groups) == 6
    assert groups[0].start == date(2024, 1, 1)
    assert groups[-1].end == date(2024, 12, 31)
    # Each group has same bar count (or differs by at most 1)
    sizes = [g.n_bars for g in groups]
    assert max(sizes) - min(sizes) <= 1


def test_compute_groups_contiguous_no_gaps():
    groups = compute_groups(date(2024, 1, 1), date(2024, 12, 31), 300, 6)
    for a, b in zip(groups, groups[1:]):
        # Next group begins exactly where previous ended (no day overlap, no gap)
        assert a.end_index + 1 == b.start_index


def test_compute_groups_rejects_n_lt_4():
    with pytest.raises(ValueError, match="n_groups"):
        compute_groups(date(2024, 1, 1), date(2024, 12, 31), 300, 3)


def test_compute_cpcv_splits_n6_k2():
    splits = compute_cpcv_splits(n_groups=6, test_groups_per_split=2)
    # C(6, 2) = 15 splits
    assert len(splits) == 15
    # Each split has 2 test groups, 4 train groups
    for s in splits:
        assert len(s.test_groups) == 2
        assert len(s.train_groups) == 4
        assert set(s.test_groups).isdisjoint(s.train_groups)
        assert set(s.train_groups) | set(s.test_groups) == set(range(6))


def test_compute_cpcv_splits_canonical_ordering():
    """itertools.combinations ordering is preserved (lexicographic on test_groups)."""
    splits = compute_cpcv_splits(n_groups=4, test_groups_per_split=2)
    test_tuples = [tuple(s.test_groups) for s in splits]
    assert test_tuples == [
        (0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3),
    ]


def test_compute_cpcv_splits_rejects_k_too_large():
    with pytest.raises(ValueError, match="test_groups_per_split"):
        compute_cpcv_splits(n_groups=4, test_groups_per_split=3)


def test_compute_cpcv_splits_rejects_k_zero():
    with pytest.raises(ValueError, match="test_groups_per_split"):
        compute_cpcv_splits(n_groups=4, test_groups_per_split=0)


from coordinator.services.validation.cpcv import reconstruct_paths


def test_reconstruct_paths_n6_k2_yields_5_paths():
    splits = compute_cpcv_splits(n_groups=6, test_groups_per_split=2)
    # Stub each split's oos_segment_run_ids: {group: f"run-{split}-{group}"}
    split_to_run_ids = {
        s.index: {g: f"run-{s.index}-{g}" for g in s.test_groups}
        for s in splits
    }
    paths = reconstruct_paths(
        splits=splits,
        n_groups=6,
        test_groups_per_split=2,
        split_to_run_ids=split_to_run_ids,
    )
    # C(N-1, k-1) = C(5, 1) = 5
    assert len(paths) == 5
    # Each path covers all 6 groups in time order
    for path in paths:
        assert [seg.group for seg in path] == [0, 1, 2, 3, 4, 5]


def test_reconstruct_paths_each_group_appears_once_per_path():
    splits = compute_cpcv_splits(n_groups=4, test_groups_per_split=2)
    split_to_run_ids = {
        s.index: {g: f"run-{s.index}-{g}" for g in s.test_groups}
        for s in splits
    }
    paths = reconstruct_paths(splits, 4, 2, split_to_run_ids)
    # C(3, 1) = 3 paths
    assert len(paths) == 3
    for path in paths:
        groups_in_path = {seg.group for seg in path}
        assert groups_in_path == {0, 1, 2, 3}


def test_reconstruct_paths_no_two_paths_use_same_split_for_same_group():
    """Each cell (group, split) appears in exactly one path."""
    splits = compute_cpcv_splits(n_groups=6, test_groups_per_split=2)
    split_to_run_ids = {
        s.index: {g: f"run-{s.index}-{g}" for g in s.test_groups}
        for s in splits
    }
    paths = reconstruct_paths(splits, 6, 2, split_to_run_ids)
    seen = set()
    for path in paths:
        for seg in path:
            key = (seg.group, seg.split)
            assert key not in seen
            seen.add(key)
