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
