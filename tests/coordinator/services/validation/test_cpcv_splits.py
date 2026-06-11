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
